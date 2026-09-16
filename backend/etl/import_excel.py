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
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import pandas as pd  # noqa: E402
from sqlalchemy import delete, select, text  # noqa: E402

from app.analysis import jsonable  # noqa: E402
from app.config import FILE_GLOB, PART_CONFIGS, get_batch_dir  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.models import ApprovalNode, Base, Batch, PartDoc  # noqa: E402
from app.parser import load_part_file, parse_approval_record  # noqa: E402

CHUNK = 1000

# 关键业务列 → 字段映射
COL_MAP = {
    "审批编号": "approval_no",
    "数据id": "data_id",
    "创建人": "creator",
    "创建人部门": "creator_dept",
    "审批状态": "status",
    "审批单标题": "title",
}


def sku_of(df, row):
    col = "新品SKU" if "新品SKU" in df.columns else ("产品SKU" if "产品SKU" in df.columns else None)
    if col is None:
        return None
    v = row.get(col)
    if v is None or (isinstance(v, float) and pd.isna(v)) or pd.isna(v):
        return None
    s = str(v).strip()
    return s or None if s.lower() != "nan" else None


def model_of(df, row):
    if "新机型号" not in df.columns:
        return None
    v = row.get("新机型号")
    if v is None or pd.isna(v):
        return None
    s = str(v).strip()
    return s or None if s.lower() != "nan" else None


def fingerprint_str(d: Path):
    items = []
    for f in sorted(d.glob(FILE_GLOB)):
        try:
            st = f.stat()
            items.append(f"{f.name}|{st.st_mtime_ns}|{st.st_size}")
        except OSError:
            continue
    return ";".join(items)[:1000]


def import_batch(session, batch_name: str, force: bool = False):
    d = get_batch_dir(batch_name)
    fp = fingerprint_str(d)
    batch = session.execute(
        select(Batch).where(Batch.name == batch_name)).scalar_one_or_none()
    if batch and batch.fingerprint == fp and not force:
        return ("skipped", batch_name, batch.doc_count)

    if batch is None:
        batch = Batch(name=batch_name, path=str(d))
        session.add(batch)
        session.flush()
    batch.path = str(d)
    batch.fingerprint = fp
    batch.imported_at = datetime.now()

    # 覆盖式：先清旧数据
    old_ids = [r[0] for r in session.execute(
        select(PartDoc.id).where(PartDoc.batch_id == batch.id))]
    if old_ids:
        for i in range(0, len(old_ids), CHUNK):
            session.execute(delete(ApprovalNode).where(
                ApprovalNode.doc_id.in_(old_ids[i:i + CHUNK])))
        session.execute(delete(PartDoc).where(PartDoc.batch_id == batch.id))
        session.flush()

    doc_count = 0
    node_count = 0
    for f in sorted(d.glob(FILE_GLOB)):
        part_key = f.name.split("_")[0]
        if part_key not in PART_CONFIGS:
            continue
        df = load_part_file(f, part_key)
        for idx, (_, row) in enumerate(df.iterrows()):
            rec = {
                "batch_id": batch.id,
                "part": part_key,
                "row_idx": idx,
                "sku": sku_of(df, row),
                "model": model_of(df, row),
                "create_time": jsonable(row.get("创建时间")),
                "update_time": jsonable(row.get("更新时间")),
                "finish_time": jsonable(row.get("完成时间")),
                "raw": {str(c): jsonable(row[c]) for c in df.columns
                        if not str(c).startswith("_")},
            }
            for src, dst in COL_MAP.items():
                if src in df.columns:
                    v = row.get(src)
                    rec[dst] = None if v is None or pd.isna(v) else str(v).strip() or None
            doc = PartDoc(**rec)
            session.add(doc)
            session.flush()
            doc_count += 1

            nodes = parse_approval_record(row.get("审批记录"))
            if nodes:
                session.add_all([
                    ApprovalNode(
                        doc_id=doc.id, seq=i,
                        operator=n["operator"], action=n["action"],
                        action_time=n["actionTime"], remark=n["remark"],
                    ) for i, n in enumerate(nodes)
                ])
                node_count += len(nodes)
    batch.doc_count = doc_count
    session.commit()
    return ("imported", batch_name, doc_count, node_count)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default=None, help="只导指定批次（默认全部）")
    ap.add_argument("--force", action="store_true", help="指纹未变也强制重导")
    args = ap.parse_args()

    Base.metadata.create_all(engine)

    from app.config import list_batches
    names = [args.batch] if args.batch else [b["name"] for b in list_batches()]
    if not names:
        print("未发现任何批次，检查 DATA_ROOT")
        return

    t0 = datetime.now()
    with SessionLocal() as session:
        for name in names:
            try:
                result = import_batch(session, name, force=args.force)
                print(result)
            except Exception as e:
                session.rollback()
                print(f"FAILED {name}: {e}")
                raise
    print(f"完成，耗时 {(datetime.now() - t0).total_seconds():.1f}s")


if __name__ == "__main__":
    main()
