# -*- coding: utf-8 -*-
"""
products_loader.py — 商品资料.xlsx → product_catalog 表（评级档位 + 单价）
========================================================================
损益推算的**唯一输入源**：`推迟天数 × 评级日均区间 × 单价`。
档位→日均区间的解析规则见 `app/product_rules.py`（权威 = 商品资料表 A1 说明）。

同时导入同文件的两张登记子表：
  - **`SKU换号`** → `sku_alias` 表（旧号→新号，见 `app/sku_alias.py`）；
  - **`人工登记`** → `sku_hold` 表（业务上不再走完流程 → 免催办，见 `app/sku_hold.py`）。
三者共用一次文件指纹，避免「商品资料更新了但登记表还是旧的」半新半旧状态。

两个入口共用本模块：
    ① CLI   : python backend/etl/products_loader.py [--force] [--path X.xlsx]
    ② 界面  : POST /api/etl/products（app/routers/dataadmin.py）

设计要点：
  - **全量覆盖式**：先清空 product_catalog 再整表写入（约 620 行，单事务，失败自动回滚）。
    商品资料是"当前状态快照"，不做行级 diff——删除的 SKU 必须从表里消失，否则损益会算到已下架 SKU。
  - **指纹增量**：文件 (mtime_ns, size) 未变且未 force 时直接跳过，避免钉钉同步后每次管线都重导。
  - 未命中的列/行**不静默**：缺表头、缺 SKU 列、档位未命中都会写进 notes 并在接口回显。
  - ⚠️ **性能陷阱**：openpyxl 的 read_only 模式**禁止** `ws.cell(row, col)` 随机访问——
    每次调用都会从头重解析工作表，621 行实测要 89 秒。必须单遍 `iter_rows` 流式读取（实测 <0.5s）。
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy import delete, select

HERE = Path(__file__).resolve()
BACKEND_DIR = HERE.parents[1]           # backend/
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import STATE_DIR                                    # noqa: E402
from app.db import SessionLocal, engine                             # noqa: E402
from app.models import (Base, PartDoc, ProductCatalog, SkuAlias,      # noqa: E402
                        SkuHold)
from app.product_rules import (GRADE_RANGES, parse_grade_ranges,     # noqa: E402
                               range_of, save_grades)
from app.rule_service import get_rules                              # noqa: E402
from app.sku_alias import (ALIAS_SHEET, read_alias_from_wb,          # noqa: E402
                           validate_alias_rows)
from app.sku_hold import (HOLD_SHEET, read_hold_from_wb,             # noqa: E402
                          validate_hold_rows)

# 商品资料默认路径（rule_config 的 products_xlsx_path 优先）
DEFAULT_XLSX = r"C:\Users\dell\Desktop\work-main\资产文件\商品资料.xlsx"
SHEET_NAME = "商品资料"
INDEX_PATH = Path(STATE_DIR) / "products_index.json"
PRICE_TYPE = "参考成本价"      # 表内无膜售价列；补售价列后改此处并重导即自动切换

# 表头关键字（**按包含匹配**，规避全角空格与月度列名变化，如「7月销量得分」/「2月销量得分」）
HDR_PRICE_KEYS = ("参考成本价",)
HDR_GRADE_KEYS = ("销量得分",)
HDR_EXACT = {"sku": "SKU", "category": "类型", "state": "状态", "model": "型号"}

MAX_HDR_SCAN = 8          # 表头只在前 8 行里找（第 1 行是档位说明文本，第 2 行才是表头）
GRADE_DRIFT_RATIO = 0.5   # 档位解析值与内置表相差超过该比例即告警（防 A1 被改错）


def _s(v):
    """单元格 → 去空白的字符串；None/NaN 统一为 ''。"""
    if v is None:
        return ""
    t = str(v).strip()
    return "" if t.lower() in ("nan", "none") else t


def _num(v):
    """单元格 → float 或 None（兼容 '12.0266' 文本数字与 '#N/A'）。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = _s(v).replace(",", "")
    if not t or t.startswith("#"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def resolve_path() -> Path:
    """商品资料路径：rule_config.products_xlsx_path 优先，缺失回退默认值。"""
    try:
        rules, _ = get_rules()
        p = (rules or {}).get("products_xlsx_path")
        if p:
            return Path(str(p))
    except Exception:
        pass
    return Path(DEFAULT_XLSX)


def fingerprint_of(path: Path) -> str:
    st = path.stat()
    return f"{st.st_mtime_ns}|{st.st_size}"


def read_index() -> dict:
    if INDEX_PATH.exists():
        try:
            return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _write_index(d: dict) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _map_cols(hdr, notes: list) -> dict:
    """把表头行（单元格元组）映射成 {语义: 1 基列号}。

    ⚠️ 表里有两个名为 SKU 的列（A=主 SKU、O=小语种 SKU），**必须取第一个**。
    """
    cols = {}
    for c, h in enumerate(hdr, start=1):
        h = _s(h)
        if not h:
            continue
        if "sku" not in cols and h.upper() == HDR_EXACT["sku"]:
            cols["sku"] = c
            continue
        if "price" not in cols and any(k in h for k in HDR_PRICE_KEYS):
            cols["price"] = c
            continue
        if "grade" not in cols and any(k in h for k in HDR_GRADE_KEYS):
            cols["grade"] = c
            continue
        for key, exact in HDR_EXACT.items():
            if key != "sku" and key not in cols and h == exact:
                cols[key] = c
    missing = [k for k in ("sku", "price", "grade") if k not in cols]
    if missing:
        notes.append(f"⚠ 缺关键列 {missing}（表头行：{hdr[:6]}…）")
    return cols


def _count_matched(session, catalog_skus) -> int:
    """product_catalog 与 part_doc 的交集 SKU 数（评估里 productMatched 的分子）。"""
    if not catalog_skus:
        return 0
    doc_skus = {(_s(x) if not isinstance(x, str) else x.strip())
                for (x,) in session.execute(select(PartDoc.sku).distinct())}
    doc_skus.discard("")
    return len(set(catalog_skus) & doc_skus)


def import_products(session=None, force: bool = False, path=None) -> dict:
    """导入商品资料。返回摘要 dict（同时写入 state/products_index.json）。"""
    t0 = time.time()
    own_session = session is None
    src = Path(path) if path else resolve_path()
    notes: list = []
    summary = {
        "status": "skipped",
        "xlsxPath": str(src),
        "importedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": 0, "matchedSkuCount": 0, "gradeDist": {}, "priceType": PRICE_TYPE,
        "aliasTotal": 0, "aliasSheet": ALIAS_SHEET,
        "holdTotal": 0, "holdSheet": HOLD_SHEET,
        "elapsed": 0.0, "notes": notes, "lastError": "",
    }

    if not src.is_file():
        summary["status"] = "failed"
        summary["lastError"] = f"商品资料文件不存在：{src}"
        notes.append("⚠ " + summary["lastError"])
        _write_index({**read_index(), **summary})
        return summary

    fp = fingerprint_of(src)
    idx = read_index()
    if not force and idx.get("fingerprint") == fp and idx.get("status") == "imported":
        summary.update(status="skipped", fingerprint=fp,
                       total=idx.get("total", 0),
                       matchedSkuCount=idx.get("matchedSkuCount", 0),
                       gradeDist=idx.get("gradeDist", {}),
                       aliasTotal=idx.get("aliasTotal", 0),
                       holdTotal=idx.get("holdTotal", 0),
                       importedAt=idx.get("importedAt", summary["importedAt"]))
        notes.append("文件指纹未变化，已跳过（force=True 可强制重导）")
        return summary

    wb = None
    try:
        Base.metadata.create_all(engine)          # 幂等建表
        wb = load_workbook(src, read_only=True, data_only=True)
        ws = wb[SHEET_NAME] if SHEET_NAME in wb.sheetnames else wb.worksheets[0]
        if ws.title != SHEET_NAME:
            notes.append(f"⚠ 未找到子表『{SHEET_NAME}』，改用首表『{ws.title}』")

        # ---- 档位表：解析 A1 说明（权威）+ 落盘供口径说明卡展示 ----
        a1 = (next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None) or [None])[0]
        ranges, rnotes = parse_grade_ranges(a1)
        notes.extend(rnotes)
        save_grades(STATE_DIR, ranges, notes=rnotes, source=str(src))
        for g, base in GRADE_RANGES.items():
            lo, _hi = range_of(g, ranges)
            if lo is not None and base[0] is not None \
                    and abs(lo - base[0]) / max(abs(lo), abs(base[0]), 1e-9) > GRADE_DRIFT_RATIO:
                notes.append(f"⚠ 档位 {g} 下界解析为 {lo}，与内置 {base[0]} 相差 "
                             f">{int(GRADE_DRIFT_RATIO * 100)}%，请核对 A1")

        # ---- 单遍流式读取：先定位表头，再收数据行（read_only 下不可随机访问单元格）----
        rows, grade_dist = [], {}
        header_row, cols = None, {}
        skipped_na = no_price = 0
        for i, vals in enumerate(ws.iter_rows(values_only=True), start=1):
            if header_row is None:
                if vals and _s(vals[0]).upper() == HDR_EXACT["sku"]:
                    header_row = i
                    cols = _map_cols(vals, notes)
                elif i >= MAX_HDR_SCAN:
                    notes.append(f"⚠ 前 {MAX_HDR_SCAN} 行未找到以 SKU 开头的表头行")
                    break
                continue
            if i <= header_row:
                continue

            def _cell(key, _v=vals):
                idx2 = cols.get(key)
                return _v[idx2 - 1] if (idx2 and idx2 - 1 < len(_v)) else None

            sku = _s(_cell("sku"))
            if not sku:
                continue
            grade = _s(_cell("grade")).upper()
            dmin, dmax = range_of(grade, ranges)
            if grade and dmin is None and dmax is None:
                skipped_na += 1          # 档位填了但不在档位表里（笔误/新档位）
            price = _num(_cell("price"))
            if price is None:
                no_price += 1
            rows.append({
                "sku": sku,
                "model": _s(_cell("model")) or None,
                "grade": grade or None,
                "daily_min": dmin, "daily_max": dmax,
                "price": price, "price_type": PRICE_TYPE,
                "category": _s(_cell("category")) or None,
                "state": _s(_cell("state")) or None,
                "updated_at": datetime.now(),
            })
            if grade:
                grade_dist[grade] = grade_dist.get(grade, 0) + 1

        # ---- 换号对照（同一文件的『SKU换号』子表）----
        # 与 product_catalog 同一次导入、同一份文件 → 商品资料一变，换号口径跟着变，
        # 不会出现「商品资料更新了但换号还是旧的」这种半新半旧状态。
        alias_rows, anotes = read_alias_from_wb(wb)
        notes.extend(anotes)
        alias_rows, vnotes = validate_alias_rows(
            alias_rows, known_skus=[x["sku"] for x in rows])
        notes.extend(vnotes)

        # ---- 人工登记（同一文件的『人工登记』子表，业务上不再走完 → 免催办）----
        # 与换号表同样的全量覆盖语义；两张子表一起随商品资料刷新，口径不会半新半旧。
        hold_rows, hnotes = read_hold_from_wb(wb)
        notes.extend(hnotes)
        hold_rows, hvnotes = validate_hold_rows(
            hold_rows, known_skus=[x["sku"] for x in rows])
        notes.extend(hvnotes)
        wb.close()
        wb = None

        if header_row is None:
            raise ValueError("未定位到表头行（商品资料表结构可能已变更）")
        if "sku" not in cols:
            raise ValueError("未定位到 SKU 列（商品资料表结构可能已变更）")
        if not rows:
            raise ValueError("未读到任何 SKU 行（表头/数据行定位可能有误）")
        if no_price:
            notes.append(f"⚠ {no_price} 行无参考成本价（金额将输出 null 并标 degraded）")
        if skipped_na:
            notes.append(f"⚠ {skipped_na} 行档位不在档位表中（未命中，将走兜底区间）")

        # ---- 全量覆盖写入（单事务）----
        sess = session if session is not None else SessionLocal()
        try:
            sess.execute(delete(ProductCatalog))
            sess.execute(ProductCatalog.__table__.insert(), rows)
            # 换号表也是全量覆盖：子表里删掉/改掉的行必须跟着消失，
            # 否则旧映射会一直留在库里继续误判「不催办」。
            sess.execute(delete(SkuAlias))
            if alias_rows:
                sess.execute(SkuAlias.__table__.insert(), [{
                    "old_sku": x["old"], "new_sku": x["new"],
                    "note": x.get("note") or None,
                    "effective_at": x.get("effectiveAt") or None,
                    "updated_at": datetime.now(),
                } for x in alias_rows])
            # 人工登记同理全量覆盖：子表里删掉的行必须跟着消失，
            # 否则已恢复推进的 SKU 会一直躺在「已豁免」里不再被催办。
            sess.execute(delete(SkuHold))
            if hold_rows:
                sess.execute(SkuHold.__table__.insert(), [{
                    "sku": x["sku"],
                    "reason": x.get("reason") or None,
                    "registered_at": x.get("registeredAt") or None,
                    "updated_at": datetime.now(),
                } for x in hold_rows])
            matched = _count_matched(sess, [x["sku"] for x in rows])
            if own_session:
                sess.commit()
        except Exception:
            if own_session:
                sess.rollback()
            raise
        finally:
            if own_session:
                sess.close()

        summary.update(
            status="imported", fingerprint=fp, total=len(rows),
            matchedSkuCount=matched, gradeDist=dict(sorted(grade_dist.items())),
            aliasTotal=len(alias_rows),
            holdTotal=len(hold_rows),
        )
    except Exception as e:                       # noqa: BLE001 — 任何失败都落索引并回显
        summary["status"] = "failed"
        summary["lastError"] = f"{type(e).__name__}: {e}"
        notes.append("⚠ " + summary["lastError"])
    finally:
        if wb is not None:
            try:
                wb.close()
            except Exception:
                pass

    summary["elapsed"] = round(time.time() - t0, 2)
    _write_index(summary if summary["status"] != "skipped" else {**read_index(), **summary})
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="导入商品资料（评级档位+单价）到 product_catalog")
    ap.add_argument("--force", action="store_true", help="忽略指纹，强制重导")
    ap.add_argument("--path", default=None, help="商品资料 xlsx 路径（默认读 rule_config）")
    args = ap.parse_args(argv)

    s = import_products(force=args.force, path=args.path)
    print(f"[{s['status']}] {s['xlsxPath']}")
    print(f"  SKU 行数={s['total']}  与 part_doc 命中={s['matchedSkuCount']}  "
          f"单价口径={s['priceType']}  用时={s['elapsed']}s")
    print(f"  换号对照（{s.get('aliasSheet')} 子表）= {s.get('aliasTotal', 0)} 条")
    print(f"  人工登记（{s.get('holdSheet')} 子表）= {s.get('holdTotal', 0)} 条（免催办）")
    print(f"  档位分布: {s['gradeDist']}")
    for n in s["notes"]:
        print("  - " + n)
    if s["lastError"]:
        print("  错误: " + s["lastError"])
    return 0 if s["status"] in ("imported", "skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
