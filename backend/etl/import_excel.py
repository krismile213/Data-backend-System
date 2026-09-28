# -*- coding: utf-8 -*-
"""
import_excel.py — 批次 Excel → MySQL 全量导入（ETL）
====================================================
用法（部署 venv）：
    D:\\apps\\data-admin\\venv\\Scripts\\python.exe backend\\etl\\import_excel.py
    可选 --batch 手机膜9.7   只导指定批次

口径：
- 复用 app.parser.load_part_file（审批编号/数据id 文本化、时间列转 datetime、警告抑制）；
- 复用 app.parser.parse_approval_record 解析审批记录 → approval_node；
- 每个批次全量覆盖式导入（先删后插），batch.fingerprint 记录文件指纹，
  后续可据此做增量（指纹未变则跳过）；
- raw 列保留全部原始字段（JSON），关键业务列提取为独立索引列。

性能约定（2026-09-21 重构，勿退回）：
  旧实现每行 `session.add(doc); session.flush()`，为拿 doc.id 而**每行一次 INSERT 往返**
  —— 实测手机膜批次 9315 条语句、SQL 段占导入总耗时约 50%。
  改为「批量 Core insert + 一次 SELECT 回捞 id 映射 + 批量插节点」，
  原因：MySQL 不支持 `INSERT ... RETURNING`，ORM 无法在一次 flush 里既批量插入又拿到主键，
  必须显式回捞。这样每个文件只需约 2~4 次往返。
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import pandas as pd  # noqa: E402
from sqlalchemy import delete, insert, select, text  # noqa: E402

from app.analysis import jsonable  # noqa: E402
from app.config import (DEFAULT_CATEGORY, DATA_ROOTS, FILE_GLOB,  # noqa: E402
                        get_batch_dir, list_batches, locate_batch,
                        part_configs_for)
from app.db import SessionLocal, engine  # noqa: E402
from app.models import ApprovalNode, Base, Batch, PartDoc  # noqa: E402
from app.parser import load_part_file, parse_approval_record  # noqa: E402

# 低于该版本说明库结构落后，需要补列（见 ensure_schema）
# 例：2026-09-22 新增 batch.category（品类分根），旧库该列不存在 → 建表语句
# create_all() 只管"表不存在"，**不会 ALTER 已存在的表**，必须显式补。
_REQUIRED_COLUMNS = {
    "batch": {"category": "VARCHAR(32) NULL"},
}


def ensure_schema():
    """建表 + 补齐后加的列（幂等）。ETL 与后端启动各调一次。"""
    Base.metadata.create_all(engine)
    added = []
    with engine.begin() as conn:
        for table, cols in _REQUIRED_COLUMNS.items():
            have = {r[0] for r in conn.execute(text(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"),
                {"t": table})}
            if not have:
                continue                     # 表刚由 create_all 建好，列已齐
            for col, ddl in cols.items():
                if col not in have:
                    conn.execute(text(f"ALTER TABLE `{table}` ADD COLUMN `{col}` {ddl}"))
                    added.append(f"{table}.{col}")
    if added:
        print(f"[SCHEMA] 已补列: {', '.join(added)}")
    return added


def backfill_batch_category():
    """给旧库（category 为 NULL 的批）按数据根路径回填品类，一次即可。

    判定依据是 batch.path 落在哪个品类的根下 —— 比按批次名猜（"手机壳9.7" vs
    "手机膜9.7"）可靠，也不受目录改名影响。
    """
    fixed = []
    with SessionLocal() as session:
        rows = session.execute(
            select(Batch).where(Batch.category.is_(None))).scalars().all()
        for b in rows:
            p = (b.path or "")
            hit = None
            for cat, root in DATA_ROOTS.items():
                try:
                    if p and Path(p).resolve().is_relative_to(root.resolve()):
                        hit = cat
                        break
                except (OSError, ValueError):
                    continue
            if hit is None:
                continue                     # 路径也不认识 → 留给人工，不当成默认品类
            b.category = hit
            fixed.append(f"{b.name}→{hit}")
        session.commit()
    return fixed

# 单条 INSERT 携带的行数。太小则往返多，太大则单语句过长、失败时回滚粒度粗。
INSERT_CHUNK = 500

# 关键业务列 → 字段映射
COL_MAP = {
    "审批编号": "approval_no",
    "数据id": "data_id",
    "创建人": "creator",
    "创建人部门": "creator_dept",
    "审批状态": "status",
    "审批单标题": "title",
}


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _text_or_none(v):
    """标量 → 去空白文本；None/NaN/空串 → None（与原 sku_of/model_of 口径一致）"""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    return s


def sku_of(row, sku_col):
    if sku_col is None:
        return None
    return _text_or_none(row.get(sku_col))


def model_of(row, has_model):
    if not has_model:
        return None
    return _text_or_none(row.get("新机型号"))


def fingerprint_str(d: Path):
    items = []
    for f in sorted(d.glob(FILE_GLOB)):
        try:
            st = f.stat()
            items.append(f"{f.name}|{st.st_mtime_ns}|{st.st_size}")
        except OSError:
            continue
    return ";".join(items)[:1000]


def _row_to_doc(rec_extra, row, part_key, idx, cols, sku_col, has_model):
    """一行 Excel（dict）→ part_doc 的插入参数"""
    rec = {
        "batch_id": rec_extra["batch_id"],
        "part": part_key,
        "row_idx": idx,
        "sku": sku_of(row, sku_col),
        "model": model_of(row, has_model),
        "create_time": jsonable(row.get("创建时间")),
        "update_time": jsonable(row.get("更新时间")),
        "finish_time": jsonable(row.get("完成时间")),
        "raw": {str(c): jsonable(row.get(c)) for c in cols},
    }
    for src, dst in COL_MAP.items():
        if src in row:
            rec[dst] = _text_or_none(row.get(src))
    return rec


def import_batch(session, batch_name: str, force: bool = False, category=None):
    """导入一个批次。category 缺省时跨品类自动定位（按数据根路径判定）。"""
    if category:
        cat = category.strip()
        d = get_batch_dir(batch_name, cat)   # 校验存在性
    else:
        cat, d = locate_batch(batch_name)
    pcfg = part_configs_for(cat)
    fp = fingerprint_str(d)
    batch = session.execute(
        select(Batch).where(Batch.name == batch_name)).scalar_one_or_none()
    # ⚠️ 旧库 category 为 NULL → 视为默认品类（与 review._cat_cond 同口径），
    #    否则升级后第一次「无变化导入」会被判成 changed，白白清空重灌一遍。
    if batch and batch.fingerprint == fp and not force \
            and (batch.category or DEFAULT_CATEGORY) == cat:
        return ("skipped", batch_name, batch.doc_count)

    if batch is None:
        batch = Batch(name=batch_name, path=str(d), category=cat)
        session.add(batch)
        session.flush()
    batch.path = str(d)
    batch.category = cat
    batch.fingerprint = fp
    batch.imported_at = datetime.now()

    # 覆盖式：先清旧数据。
    # 用 JOIN 一次删完子表（原先按 1000 个 id 分批 IN，几千单据要十几次往返）。
    session.execute(text(
        "DELETE n FROM approval_node n "
        "JOIN part_doc d ON n.doc_id = d.id "
        "WHERE d.batch_id = :bid"), {"bid": batch.id})
    session.execute(delete(PartDoc).where(PartDoc.batch_id == batch.id))
    session.flush()

    doc_count = 0
    node_count = 0
    for f in sorted(d.glob(FILE_GLOB)):
        part_key = f.name.split("_")[0]
        if part_key not in pcfg:            # 按品类校验 Part 键（两品类键名相同）
            continue
        df = load_part_file(f, part_key)
        # 只用一次 to_dict；iterrows 会为每行构造 Series，实测慢约 2.7 倍
        records = df.to_dict("records")
        cols = [c for c in df.columns if not str(c).startswith("_")]
        sku_col = ("新品SKU" if "新品SKU" in df.columns
                   else ("产品SKU" if "产品SKU" in df.columns else None))
        has_model = "新机型号" in df.columns
        extra = {"batch_id": batch.id}

        doc_rows = []
        nodes_by_idx = []
        for idx, row in enumerate(records):
            doc_rows.append(_row_to_doc(extra, row, part_key, idx, cols,
                                        sku_col, has_model))
            nodes_by_idx.append(parse_approval_record(row.get("审批记录")) or ())

        if not doc_rows:
            continue

        # ① 批量写 part_doc（多值 INSERT，不带主键）
        for chunk in _chunks(doc_rows, INSERT_CHUNK):
            session.execute(insert(PartDoc), chunk)
        session.flush()

        # ② 一次回捞 id 映射。
        #    按 (part, row_idx) 配对 —— 同一 part 文件内 row_idx 唯一，够用。
        #    注意：此处依赖「一个 part_key 只对应一个文件」，与旧实现假设一致。
        id_map = {
            row_idx: doc_id
            for doc_id, row_idx in session.execute(
                select(PartDoc.id, PartDoc.row_idx)
                .where(PartDoc.batch_id == batch.id, PartDoc.part == part_key))
        }

        # ③ 批量写 approval_node
        node_rows = []
        for idx, nodes in enumerate(nodes_by_idx):
            doc_id = id_map.get(idx)
            if doc_id is None:
                continue
            for i, n in enumerate(nodes):
                node_rows.append({
                    "doc_id": doc_id, "seq": i,
                    "operator": n["operator"], "action": n["action"],
                    "action_time": n["actionTime"], "remark": n["remark"],
                })
        for chunk in _chunks(node_rows, INSERT_CHUNK):
            session.execute(insert(ApprovalNode), chunk)

        missing = len(doc_rows) - len(id_map)
        if missing:
            raise RuntimeError(
                f"{f.name}: 回捞主键缺失 {missing} 条（期望 {len(doc_rows)}，"
                f"实得 {len(id_map)}），已中止导入")

        doc_count += len(doc_rows)
        node_count += len(node_rows)

    batch.doc_count = doc_count
    session.commit()
    return ("imported", batch_name, doc_count, node_count)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default=None, help="只导指定批次（默认全部）")
    ap.add_argument("--category", default=None,
                    help="只导某品类（手机膜/手机壳；默认扫所有数据根）")
    ap.add_argument("--force", action="store_true", help="指纹未变也强制重导")
    args = ap.parse_args()

    ensure_schema()
    fixed = backfill_batch_category()
    if fixed:
        print(f"[SCHEMA] 旧批次回填品类: {', '.join(fixed)}")

    if args.batch:
        targets = [(args.category, args.batch)]
    else:
        batches = list_batches(args.category, all_categories=not args.category)
        targets = [(b["category"], b["name"]) for b in batches]
    if not targets:
        print("未发现任何批次，检查数据根目录（DATA_ROOTS）")
        return

    print(f"待导入 {len(targets)} 个批次："
          + ", ".join(f"{c or '?'}/{n}" for c, n in targets))
    t0 = datetime.now()
    with SessionLocal() as session:
        for cat, name in targets:
            try:
                result = import_batch(session, name, force=args.force,
                                      category=cat)
                print(result)
            except Exception as e:
                session.rollback()
                print(f"FAILED {cat or '?'}/{name}: {e}")
                raise
    print(f"完成，耗时 {(datetime.now() - t0).total_seconds():.1f}s")


if __name__ == "__main__":
    main()
