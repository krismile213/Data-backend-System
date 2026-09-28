# -*- coding: utf-8 -*-
"""
dataadmin.py — 数据管理：批次扫描 / 导入触发 / 备份
=====================================================
把原本只能在命令行跑的 ETL 搬到界面上，让"更新数据"不再是唯一一个
必须离开浏览器才能完成的步骤。

接口：
    GET  /api/etl/scan      扫描数据根目录，对照数据库给出每个批次的导入状态
    POST /api/etl/import    触发导入（可选 batch / force）
    GET  /api/etl/backup    列出已有备份
    POST /api/etl/backup    立即备份（state 目录 + mysqldump 若可用）
"""

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..category_rules import DEFAULT_CATEGORY
from ..config import DATA_ROOTS, STATE_DIR, data_root, list_batches
from ..db import MYSQL_DATABASE, MYSQL_HOST, MYSQL_PASSWORD, MYSQL_PORT, MYSQL_USER
from ..db import engine, get_session
from ..models import ApprovalNode, Batch, PartDoc, ProductCatalog

# etl 包位于 backend/etl（不在 app 包内），需把 backend 目录加入 sys.path 才能导入
BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 建表 + 补列幂等（create_all 不会 ALTER 已存在的表，缺列会让导入直接 500）
from etl.import_excel import (ensure_schema, fingerprint_str,  # noqa: E402
                              import_batch)

from etl.products_loader import import_products, read_index  # noqa: E402

ensure_schema()

router = APIRouter(prefix="/api/etl", tags=["data-admin"])

# 备份输出目录（仓库根 / backups，已被 .gitignore 排除）
BACKUP_DIR = Path(__file__).resolve().parents[3] / "backups"

# mysqldump 常见安装位置（本机装在 D 盘）
MYSQLDUMP_CANDIDATES = [
    r"D:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe",
    r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe",
]


class ImportRequest(BaseModel):
    """导入参数：batch 为空表示扫描到的全部批次；category 可限定品类"""
    batch: str | None = None
    force: bool = False
    category: str | None = None


def _fmt(dt):
    """时间统一格式化为 'YYYY-MM-DD HH:MM:SS'（不带 T，便于前端直接展示）"""
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


def _normalize(name: str, result: tuple) -> dict:
    """统一 import_batch 的返回（skipped 是 3 元组，imported 是 4 元组）"""
    if result[0] == "skipped":
        return {"batch": name, "status": "skipped", "docCount": result[2],
                "nodeCount": 0, "message": "文件未变化，已跳过"}
    return {"batch": name, "status": "imported", "docCount": result[2],
            "nodeCount": result[3], "message": ""}



@router.get("/scan")
def scan(session: Session = Depends(get_session)):
    """
    扫描数据根目录并对照数据库，返回每个批次的导入状态。

    state 含义：
        new      磁盘上有、库里没有 —— 待首次导入
        changed  文件指纹变了 —— 待重新导入
        imported 指纹一致 —— 已是最新
        missing  库里记录过、但磁盘上已找不到 —— 注意数据源是否被移动
    """
    disk = list_batches(all_categories=True)      # 全品类扫描
    disk_names = {b["name"] for b in disk}
    rows = {b.name: b for b in session.execute(select(Batch)).scalars()}

    items = []
    for b in disk:
        name = b["name"]
        rec = rows.get(name)
        same = (rec is not None
                and rec.fingerprint == fingerprint_str(Path(b["path"]))
                and (rec.category or DEFAULT_CATEGORY) == b["category"])
        items.append({
            "name": name,
            "category": b["category"],
            "displayName": b.get("displayName", name),
            "fileCount": b["fileCount"],
            "parts": [p["part"] for p in b["parts"]],
            "state": "imported" if same else ("changed" if rec else "new"),
            "docCount": rec.doc_count if rec else 0,
            "importedAt": _fmt(rec.imported_at) if rec else None,
        })

    for name, rec in rows.items():
        if name not in disk_names:
            items.append({
                "name": name, "category": rec.category or DEFAULT_CATEGORY,
                "displayName": name, "fileCount": 0, "parts": [],
                "state": "missing", "docCount": rec.doc_count,
                "importedAt": _fmt(rec.imported_at),
            })

    pending = [i["name"] for i in items if i["state"] in ("new", "changed")]
    roots = {cat: str(p) for cat, p in DATA_ROOTS.items()}
    return {
        "dataRoots": roots,
        # 兼容字段（老前端/脚本读单根）
        "dataRoot": roots.get(DEFAULT_CATEGORY, ""),
        "dataRootExists": data_root(DEFAULT_CATEGORY).exists(),
        "items": items,
        "pending": pending,
        "pendingCount": len(pending),
        "totalDocs": session.query(PartDoc).count(),
        "totalNodes": session.query(ApprovalNode).count(),
    }


@router.post("/import")
def run_import(req: ImportRequest, session: Session = Depends(get_session)):
    """导入批次：不传 batch 则处理**所有品类**扫描到的批次（未变化的自动跳过）。

    category 用于批量导入时限定品类（界面按品类筛选）；指定 batch 时自动定位品类。
    """
    # 旧库（加 category 列之前）的批次在这里一次性回填品类，幂等且只在导入时发生。
    from etl.import_excel import backfill_batch_category
    fixed = backfill_batch_category()
    if fixed:
        session.commit()

    all_batches = list_batches(req.category, all_categories=not req.category)
    if not all_batches:
        raise HTTPException(status_code=404,
                            detail=f"未发现任何批次，请检查数据根目录（{req.category or '全部品类'}）")

    if req.batch:
        hit = next((b for b in all_batches if b["name"] == req.batch), None)
        if hit is None:
            # 可能被 category 过滤掉了 → 再跨品类找一次，给出更准的提示
            hit = next((b for b in list_batches(all_categories=True)
                        if b["name"] == req.batch), None)
            if hit is None:
                raise HTTPException(status_code=404, detail=f"批次不存在：{req.batch}")
        targets = [(hit["category"], req.batch)]
    else:
        targets = [(b["category"], b["name"]) for b in all_batches]

    t0 = datetime.now()
    results = []
    for cat, name in targets:
        try:
            results.append(_normalize(name, import_batch(
                session, name, force=req.force, category=cat)))
        except Exception as e:  # noqa: BLE001 — 单个批次失败不应中断其余批次
            session.rollback()
            results.append({"batch": name, "category": cat, "status": "failed",
                            "docCount": 0, "nodeCount": 0, "message": str(e)})

    summary = {k: sum(1 for r in results if r["status"] == k)
               for k in ("imported", "skipped", "failed")}

    # 有新数据入库 → review / alerts / 批次缓存全部失效，
    # 后台立刻重算一遍，避免「导入后第一个打开页面的人」等上几秒。
    if summary["imported"]:
        from ..prewarm import warm_async
        warm_async(f"导入后（{summary['imported']} 个批次）")

    return {
        "elapsed": round((datetime.now() - t0).total_seconds(), 2),
        "results": results,
        **summary,
    }


# ---------------------------------------------------------------------------
# 商品资料（评级档位 + 单价）导入 —— 损益推算的输入源
# ---------------------------------------------------------------------------
@router.post("/products")
def run_products_import(force: bool = False, session: Session = Depends(get_session)):
    """导入商品资料.xlsx → product_catalog（全量覆盖；指纹未变时自动跳过）。

    force=true 忽略指纹强制重导（商品资料改了售价/档位后立刻生效）。
    """
    result = import_products(session=session, force=force)
    # product 表内容参与损益缓存指纹 → 导入成功后让 review/alerts 缓存失效并择机预热
    if result.get("status") == "imported":
        from ..prewarm import warm_async
        warm_async("商品资料导入后")
    return result


@router.get("/products")
def products_status(session: Session = Depends(get_session)):
    """商品资料导入状态：行数、与 part_doc 的命中数、档位分布、单价口径、上次错误。"""
    idx = read_index()

    has_table = True
    total = matched = 0
    grade_dist: dict = {}
    try:
        total = session.query(ProductCatalog).count()
        doc_skus = {r for (r,) in session.execute(select(PartDoc.sku).distinct()) if r}
        cat_skus = {r for (r,) in session.execute(select(ProductCatalog.sku)) if r}
        matched = len(doc_skus & cat_skus)
        for g, n in session.execute(text(
                "SELECT grade, COUNT(*) FROM product_catalog "
                "GROUP BY grade ORDER BY grade")):
            grade_dist[g or "(空)"] = n
    except Exception as e:  # noqa: BLE001 — 表未建/连接异常时仍给出可读状态
        has_table = False
        idx = {**idx, "lastError": idx.get("lastError") or f"{type(e).__name__}: {e}"}

    return {
        "hasTable": has_table,
        "total": total,
        "matchedSkuCount": matched,
        "gradeDist": grade_dist or idx.get("gradeDist", {}),
        "priceType": idx.get("priceType", "参考成本价"),
        "importedAt": idx.get("importedAt"),
        "xlsxPath": idx.get("xlsxPath"),
        "notes": idx.get("notes", []),
        "lastError": idx.get("lastError", ""),
    }


def _find_mysqldump() -> str | None:
    for c in MYSQLDUMP_CANDIDATES:
        if Path(c).is_file():
            return c
    return shutil.which("mysqldump")


@router.get("/backup")
def list_backups():
    """列出已有备份（按时间倒序）"""
    if not BACKUP_DIR.exists():
        return {"backupDir": str(BACKUP_DIR), "items": []}
    items = []
    for d in sorted(BACKUP_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        files = {f.name: f.stat().st_size for f in d.iterdir() if f.is_file()}
        items.append({
            "name": d.name,
            "createdAt": _fmt(datetime.fromtimestamp(d.stat().st_mtime)),
            "sizeKB": round(sum(files.values()) / 1024, 1),
            "files": sorted(files),
        })
    return {"backupDir": str(BACKUP_DIR), "items": items}


@router.post("/backup")
def run_backup():
    """
    立即备份。

    重点备份 backend/state/ —— 手工填报与 KPI 快照是**无法从 Excel 重新生成**的数据；
    数据库本身可由 data/ 下的 xlsx 重建，所以 mysqldump 只作为快速回滚用途（缺失不报错）。
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / stamp
    target.mkdir(parents=True, exist_ok=True)

    state_files = []
    if STATE_DIR.exists():
        for f in STATE_DIR.iterdir():
            if f.is_file():
                shutil.copy2(f, target / f.name)
                state_files.append(f.name)

    dump_name = None
    dump_status = "skipped"
    dump_error = ""
    mysqldump = _find_mysqldump()
    if mysqldump:
        dump_path = target / f"{MYSQL_DATABASE}.sql"
        cmd = [mysqldump, "-h", MYSQL_HOST, "-P", str(MYSQL_PORT), "-u", MYSQL_USER]
        if MYSQL_PASSWORD:
            cmd.append(f"-p{MYSQL_PASSWORD}")
        cmd += [f"--result-file={dump_path}", "--single-transaction",
                "--skip-lock-tables", MYSQL_DATABASE]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=300)
            if proc.returncode == 0 and dump_path.exists():
                dump_name = dump_path.name
                dump_status = "ok"
            else:
                dump_status = "failed"
                dump_error = (proc.stderr or b"").decode("utf-8", "ignore").strip()[:300]
        except Exception as e:  # noqa: BLE001
            dump_status = "failed"
            dump_error = str(e)[:300]

    manifest = target / "manifest.txt"
    manifest.write_text(
        "\n".join([
            f"备份时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"state 文件: {', '.join(state_files) or '(无)'}",
            f"数据库导出: {dump_name or '(未执行)'}  状态: {dump_status}",
            f"数据库: {MYSQL_DATABASE} @ {MYSQL_HOST}:{MYSQL_PORT}",
            "数据根目录: " + "；".join(f"{c}={p}" for c, p in DATA_ROOTS.items()),
            f"说明: state/ 为不可再生的手工数据；数据库可由 data/ 下的 xlsx 重新导入。",
        ]),
        encoding="utf-8",
    )

    return {
        "name": stamp,
        "path": str(target),
        "stateFiles": state_files,
        "dump": dump_name,
        "dumpStatus": dump_status,
        "dumpError": dump_error,
    }
