# -*- coding: utf-8 -*-
"""probe_part3_operator.py — 排查「本地仓到货检查 审批人显示未配置」

只读，不改任何数据。输出：
  1. Part3 raw 里「历史审批人 / 当前负责人」的填充率
  2. ApprovalNode 里 action=本地仓到货检查 的 operator 填充率
  3. 逐 SKU：executor（现链路） vs 历史审批人 —— 找出「未配置但历史有名字」的样本
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import ApprovalNode, Batch, PartDoc

CAT = sys.argv[1] if len(sys.argv) > 1 else "手机膜"


def clean(v):
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s in ("", "None", "nan", "NaT", "-") else s


with SessionLocal() as s:
    rows = s.execute(
        select(PartDoc.id, PartDoc.sku, PartDoc.approval_no, PartDoc.status,
               PartDoc.raw, Batch.category, Batch.name)
        .join(Batch, PartDoc.batch_id == Batch.id)
        .where(PartDoc.part == "Part3", Batch.category == CAT)
    ).all()
    doc_ids = [r[0] for r in rows]
    nodes = s.execute(
        select(ApprovalNode.doc_id, ApprovalNode.seq, ApprovalNode.operator,
               ApprovalNode.action)
        .where(ApprovalNode.doc_id.in_(doc_ids))
        .order_by(ApprovalNode.doc_id, ApprovalNode.seq)
    ).all()

print(f"品类={CAT}  Part3 单据 {len(rows)} 张")
if not rows:
    sys.exit(0)

raw_keys = {}
for _id, _sku, _no, _st, raw, _c, _b in rows:
    for k in (raw or {}):
        raw_keys[k] = raw_keys.get(k, 0) + 1
print("\n[raw 列填充率 Top]")
for k, v in sorted(raw_keys.items(), key=lambda x: -x[1])[:20]:
    print(f"  {k}: {v}/{len(rows)}")

by_doc = {}
for doc_id, seq, op, act in nodes:
    by_doc.setdefault(doc_id, []).append((seq, op, act))

print("\n[ApprovalNode action 分布]")
acts = {}
for _id, _sku, _no, _st, raw, _c, _b in rows:
    for _seq, _op, act in by_doc.get(_id, []):
        acts[act] = acts.get(act, 0) + 1
for k, v in sorted(acts.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")

print("\n[逐 SKU：历史审批人 / 当前负责人 / 本地仓到货检查 operator]")
n_hist = n_cur = n_op = 0
samples = []
for _id, sku, no, st, raw, _c, _b in rows:
    raw = raw or {}
    hist = clean(raw.get("历史审批人"))
    cur = clean(raw.get("当前负责人"))
    ops = [clean(op) for _s, op, act in by_doc.get(_id, [])
           if act == "本地仓到货检查"]
    ops = [o for o in ops if o]
    n_hist += bool(hist)
    n_cur += bool(cur)
    n_op += bool(ops)
    if hist and not cur and not ops:
        samples.append((sku, no, hist, cur, ops))
print(f"  历史审批人有值: {n_hist}/{len(rows)}")
print(f"  当前负责人有值: {n_cur}/{len(rows)}")
print(f"  审批节点(本地仓到货检查)有 operator: {n_op}/{len(rows)}")
print(f"  ★「历史有名字、但配置/节点都取不到」的 SKU: {len(samples)}")
for sku, no, hist, cur, ops in samples[:15]:
    print(f"    {sku} | {no} | 历史审批人={hist!r} 当前负责人={cur!r}")
