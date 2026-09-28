# -*- coding: utf-8 -*-
"""
skus.py — SKU 维度接口（MySQL 数据层，复盘功能基座）
  GET /api/skus          SKU 清单：型号/批次覆盖/各 Part 完成状态/最近活动
  GET /api/skus/{code}   SKU 档案：跨批次全部单据 + 审批节点时间线
"""

from fastapi import APIRouter, HTTPException
from sqlalchemy import bindparam, text

from ..db import engine

router = APIRouter(prefix="/api", tags=["skus"])

# 审批状态（中文）→ 聚合键
_STATUS_KEY = {"已结束": "finished", "审批中": "running", "终止": "terminated"}


def _status_key(st):
    return _STATUS_KEY.get(st or "", "other")


def _fmt(v):
    if v is None:
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return v


def _time_key(d):
    """去重时间排序键：update_time 优先，其次 finish/create。"""
    t = d.get("update_time") or d.get("finish_time") or d.get("create_time")
    return t or ""


def dedupe_docs(docs):
    """跨批次重复单据只保留最新：同 (Part, 审批编号) 取 update_time 最新一条。
    无审批编号时退化为 (Part, 创建时间) 键。返回 (保留列表, 去掉数量)。"""
    best, order = {}, []
    for d in docs:
        key = (d["part"], d.get("approval_no") or str(d.get("create_time")))
        if key not in best:
            best[key] = d
            order.append(key)
        elif _time_key(d) > _time_key(best[key]):
            best[key] = d
    kept = [best[k] for k in order]
    return kept, len(docs) - len(kept)


@router.get("/skus")
def list_skus():
    """SKU 清单（跨批次聚合，重复单据只保留最新）：型号、覆盖批次、各 Part 状态"""
    sql = text("""
        SELECT d.sku, d.model, b.name AS batch, d.part, d.status,
               d.approval_no, d.update_time, d.create_time, d.finish_time
        FROM part_doc d JOIN batch b ON d.batch_id = b.id
        WHERE d.sku IS NOT NULL AND d.sku <> ''
        ORDER BY d.sku, d.part
    """)
    with engine.connect() as conn:
        rows = [dict(m) for m in conn.execute(sql).mappings()]

    # 先按 SKU 聚合原始行，再做跨批次去重（同 Part 同审批编号只留最新）
    by_sku = {}
    for r in rows:
        by_sku.setdefault(r["sku"], []).append(r)

    skus = {}
    for sku, docs in by_sku.items():
        kept, _removed = dedupe_docs(docs)
        s = skus.setdefault(sku, {
            "sku": sku,
            "models": set(), "batches": set(),
            "docCount": 0,
            "parts": {},          # part -> {finished, running, terminated, other}
            "lastUpdate": None,
            "firstProposalAt": None,   # 立项时间 = 最早单据创建时间（P0-1 排序键）
        })
        for r in kept:
            if r["model"]:
                s["models"].add(r["model"])
            s["batches"].add(r["batch"])
            s["docCount"] += 1
            p = s["parts"].setdefault(r["part"], {
                "finished": 0, "running": 0, "terminated": 0, "other": 0})
            p[_status_key(r["status"])] += 1
            lu = r["last_update"] = max(
                filter(None, (r["update_time"], r["create_time"])), default=None)
            if lu is not None and (s["lastUpdate"] is None or lu > s["lastUpdate"]):
                s["lastUpdate"] = lu
            ct = r["create_time"]
            if ct is not None and (s["firstProposalAt"] is None
                                   or ct < s["firstProposalAt"]):
                s["firstProposalAt"] = ct

    out = []
    for s in skus.values():
        s["models"] = sorted(s["models"])
        s["batches"] = sorted(s["batches"])
        s["lastUpdate"] = _fmt(s["lastUpdate"])
        s["firstProposalAt"] = _fmt(s["firstProposalAt"])
        out.append(s)
    # P0-1：默认从新到久（立项时间倒序；缺时间的排最后）
    out.sort(key=lambda x: (x["firstProposalAt"] or "", x["sku"]), reverse=True)
    return {"total": len(out), "items": out}


@router.get("/skus/{code}")
def sku_detail(code: str):
    """SKU 档案：全部单据（跨批次重复只保留最新）+ 审批节点时间线"""
    doc_sql = text("""
        SELECT d.id, b.name AS batch, d.part, d.approval_no, d.status, d.title,
               d.creator, d.model, d.create_time, d.finish_time, d.update_time
        FROM part_doc d JOIN batch b ON d.batch_id = b.id
        WHERE d.sku = :code
        ORDER BY d.part, d.create_time
    """)
    with engine.connect() as conn:
        docs = [dict(m) for m in conn.execute(doc_sql, {"code": code}).mappings()]
    if not docs:
        raise HTTPException(status_code=404, detail=f"未找到 SKU: {code}")

    total_docs = len(docs)
    docs, dup_removed = dedupe_docs(docs)

    doc_ids = [d["id"] for d in docs]
    node_sql = text("""
        SELECT doc_id, seq, operator, action, action_time, remark
        FROM approval_node WHERE doc_id IN :ids ORDER BY doc_id, seq
    """)
    nodes_by_doc = {}
    with engine.connect() as conn:
        result = conn.execute(
            node_sql.bindparams(bindparam("ids", expanding=True)),
            {"ids": doc_ids})
        for n in result.mappings():
            nodes_by_doc.setdefault(n["doc_id"], []).append({
                "seq": n["seq"],
                "operator": n["operator"] or "",
                "action": n["action"] or "(缺动作名)",
                "time": _fmt(n["action_time"]) or "",
                "remark": n["remark"] or "",
            })

    part_summary = {}
    for d in docs:
        p = part_summary.setdefault(d["part"], {
            "part": d["part"], "total": 0,
            "finished": 0, "running": 0, "terminated": 0, "other": 0})
        p["total"] += 1
        p[_status_key(d["status"])] += 1

    # 先用 id 关联节点，再格式化时间
    for d in docs:
        d["nodes"] = nodes_by_doc.get(d["id"], [])
        d.pop("id")
        for k in ("create_time", "finish_time", "update_time"):
            d[k] = _fmt(d[k])

    return {
        "sku": code,
        "docCount": len(docs),
        "totalDocs": total_docs,
        "duplicatesRemoved": dup_removed,
        "partSummary": sorted(part_summary.values(), key=lambda x: x["part"]),
        "docs": docs,
    }
