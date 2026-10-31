# -*- coding: utf-8 -*-
"""probe_sku_owner.py — 追一个 SKU 的 owner 为什么为空（只读）"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import ApprovalNode, Batch, PartDoc
from app.routers.review import (_load_sku_docs, review_sku_segments,
                                rules_for, DEFAULT_CATEGORY)

CAT = "手机膜"
SKUS = sys.argv[1:] or ["G599", "G600", "G601"]

with SessionLocal() as s:
    for sku in SKUS:
        rows = s.execute(
            select(PartDoc.id, PartDoc.part, PartDoc.approval_no,
                   PartDoc.status, PartDoc.raw, PartDoc.create_time)
            .join(Batch, PartDoc.batch_id == Batch.id)
            .where(PartDoc.sku == sku, Batch.category == CAT)
            .order_by(PartDoc.part)
        ).all()
        print(f"\n===== {sku} 单据 {len(rows)} 张 =====")
        for doc_id, part, no, st, raw, ct in rows:
            raw = raw or {}
            nodes = s.execute(
                select(ApprovalNode.seq, ApprovalNode.operator,
                       ApprovalNode.action, ApprovalNode.action_time)
                .where(ApprovalNode.doc_id == doc_id)
                .order_by(ApprovalNode.seq)).all()
            print(f"  [{part}] {no} status={st} create={ct}")
            if part in ("Part3", "Part2"):
                for k in ("历史审批人", "当前负责人", "本地仓到货时间",
                          "审批记录"):
                    v = raw.get(k)
                    if v not in (None, ""):
                        print(f"      raw[{k}] = {str(v)[:80]!r}")
            for seq, op, act, t in nodes:
                print(f"      node{seq}: {act} | op={op!r} | {t}")

# 段级明细
docs = _load_sku_docs(CAT)
rs = rules_for(CAT)
for sku in SKUS:
    parts = {}
    for d in docs:
        if d["sku"] == sku:
            parts.setdefault(d["part"], []).append(d)
    seg_rows, nodes, items = review_sku_segments(sku, parts, {}, ruleset=rs)
    print(f"\n--- {sku} 段级 executor ---")
    for r in seg_rows:
        if r["seg"] in ("采购→本地仓到货", "[产品部留空·后勤接管段]",
                        "签样→本地仓到货") or not r["executor"]:
            print(f"  {r['dept']:4} {r['seg']:22} judge={r['judge']:12} "
                  f"owner={r['owner']!r} executor={r['executor']!r} "
                  f"gap={r['gap']}")
    nd = nodes.get("本地仓到货")
    print(f"  本地仓到货节点: adopted={nd['adopted']} src={nd['src']} "
          f"audit_op={nd['audit_op']!r} action_op={nd['action_op']!r}")
