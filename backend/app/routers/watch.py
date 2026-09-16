# -*- coding: utf-8 -*-
"""
watch.py — 页面内跨环节联动预警（GET /api/watch）
================================================================
定位：路线图 #13「跨环节联动预警」的页面内先行版。
不依赖定时任务与通知渠道——清单即时计算，提醒文案一键复制，
等以后有钉钉机器人/邮件渠道再接推送（数据口径不变）。

联动口径（来源：流程复盘/流程优化建议.md 实证结论）：
  Part5 发货停滞 → 连锁卡住 Part6 素材 → 页面 coming soon。
  因此「发货停滞超期 + 素材未启动 + 样品已签样」的 SKU，
  应立即解耦推进素材，不必等发货完成。

提醒分两类：
  R1 素材可先行启动 —— 签样已完成、素材未启动，且发货停滞超过阈值
     （阈值取规则配置 stuck_days，默认 7 天）；动作 = 寄送拍摄/启动素材。
  R2 素材启动停滞   —— 寄送拍摄已完成但超 stuck_days 未交付/未加工；
     动作 = 催拍摄交付与素材加工。

责任人取法：有实际执行人（audit > 动作）用实际执行人，
否则用 seg_rules.NODES 配置的默认责任人。

接口：
  GET /api/watch   联动提醒清单 + 按责任人分组文案 + KPI
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException

from ..rule_service import get_rules
from ..seg_rules import NODES
from .review import _parse_dt, get_review_data

router = APIRouter(prefix="/api/watch", tags=["watch"])


def _material_started(det):
    """素材环节是否已实质启动：素材加工节点有时间，或 Part6 有已结束单据。"""
    nd = (det.get("nodes") or {}).get("素材加工") or {}
    if nd.get("iso"):
        return True
    return bool(((det.get("partStat") or {}).get("Part6") or {}).get("done"))


def _node_hit(det, node, part):
    nd = (det.get("nodes") or {}).get(node) or {}
    return bool(nd.get("iso")) or bool(
        ((det.get("partStat") or {}).get(part) or {}).get("done"))


def _p5_stall_days(det, now):
    """Part5 发货停滞天数：取在途单最早创建/未发货渠道占位 stall 的最大值。"""
    days = []
    for p in (det.get("part5Pending") or []):
        t = _parse_dt(p.get("createAt"))
        if t:
            days.append((now - t).total_seconds() / 86400.0)
    for s in (det.get("shipments") or []):
        if s.get("noShip") and s.get("stallDays") is not None:
            days.append(float(s["stallDays"]))
    return round(max(days), 1) if days else None


def _owner_of(det, node):
    """节点责任人：段级明细中终点为该节点的段执行人 > 默认配置责任人。
    （skuDetails.nodes 不带操作人，执行人取自 segments 的 executor 字段）"""
    for s in det.get("segments") or []:
        if s.get("to") == node and s.get("executor"):
            return s["executor"]
    return NODES[node][3] or "未指定"


def compute_watch():
    rules, _ = get_rules()
    threshold = float(rules.get("stuck_days") or 7)
    data = get_review_data()
    rows = {r["sku"]: r for r in data["rows"]}
    now = datetime.now()

    items = []
    for sku, det in data["skuDetails"].items():
        row = rows.get(sku) or {}
        started = _material_started(det)
        sample_ready = _node_hit(det, "签样", "Part2")
        sent = _node_hit(det, "寄送", "Part6")
        stall = _p5_stall_days(det, now)

        # ---- R1 素材可先行启动 ----
        if not started and sample_ready and stall is not None \
                and stall >= threshold:
            hubs = sorted({p["hub"] for p in (det.get("part5Pending") or [])
                           if p.get("hub")})
            items.append({
                "kind": "R1", "kindName": "素材可先行启动",
                "sku": sku, "model": row.get("model") or "",
                "owner": _owner_of(det, "寄送") if not sent
                         else _owner_of(det, "素材加工"),
                "action": ("寄送拍摄团队并启动素材制作（不必等发货）"
                           if not sent else
                           "催拍摄交付与素材加工（寄送已完成，素材环节停摆）"),
                "stallDays": stall, "threshold": threshold,
                "hubs": hubs,
                "sent": sent,
                "why": (f"Part5 发货已停滞 {stall} 天（阈值 {threshold:.0f} 天），"
                        f"而样品早已签样、素材至今未启动"
                        + (f"；涉及渠道：{'、'.join(hubs[:4])}" if hubs else ""))
                        + "。发货与素材解耦，先行启动素材即可去化 coming soon。",
            })
        # ---- R2 素材启动停滞（寄送完成但交付/加工超期） ----
        elif started is False and sent and not sample_ready:
            items.append({
                "kind": "R2", "kindName": "签样缺失·素材无法启动",
                "sku": sku, "model": row.get("model") or "",
                "owner": _owner_of(det, "签样"),
                "action": "先补齐全套样品测试（签样），素材才有启动条件",
                "stallDays": stall, "threshold": threshold,
                "hubs": [], "sent": sent,
                "why": "寄送拍摄的前置「签样」未完成，素材环节无从启动。",
            })

    items.sort(key=lambda x: (-(x.get("stallDays") or 0), x["sku"]))

    # ---- 按责任人分组 + 提醒文案 ----
    groups = {}
    for it in items:
        g = groups.setdefault(it["owner"], {"owner": it["owner"],
                                            "kinds": set(), "items": []})
        g["kinds"].add(it["kind"])
        g["items"].append(it)
    group_list = []
    for owner, g in sorted(groups.items()):
        lines = [f"【新品流程联动提醒】{owner}，以下 {len(g['items'])} 个 SKU 需要你处理："]
        for it in g["items"]:
            seg = f"（{it['model']}）" if it.get("model") else ""
            hub = f"，渠道：{'、'.join(it['hubs'][:3])}" if it.get("hubs") else ""
            lines.append(
                f"- {it['sku']}{seg} · {it['kindName']} · "
                f"发货停滞 {it['stallDays'] if it['stallDays'] is not None else '—'} 天{hub}"
                f" → {it['action']}")
        lines.append("口径：Part5 发货停滞 → 卡 Part6 素材 → 页面 coming soon"
                     "（素材与发货解耦推进）。")
        group_list.append({
            "owner": owner,
            "kinds": sorted(g["kinds"]),
            "count": len(g["items"]),
            "skus": [it["sku"] for it in g["items"]],
            "remindText": "\n".join(lines),
        })
    group_list.sort(key=lambda g: -g["count"])

    over = [i for i in items
            if i.get("stallDays") is not None
            and i["stallDays"] >= 3 * (i.get("threshold") or 7)]
    return {
        "computedAt": data["computedAt"],
        "thresholdDays": threshold,
        "kpi": {
            "watchCount": len(items),
            "r1Count": sum(1 for i in items if i["kind"] == "R1"),
            "r2Count": sum(1 for i in items if i["kind"] == "R2"),
            "ownerCount": len(groups),
            "maxStallDays": max((i.get("stallDays") or 0) for i in items)
                            if items else 0,
            "severeCount": len(over),
        },
        "items": items,
        "groups": group_list,
        "rules": [
            {"key": "R1 素材可先行启动",
             "rule": f"签样已完成 + 素材未启动 + Part5 发货停滞 ≥ stuck_days"
                     f"（当前 {threshold:.0f} 天，规则配置可调）"},
            {"key": "R2 签样缺失",
             "rule": "寄送拍摄已发起但「签样」节点未完成 → 素材无法启动"},
            {"key": "责任人取法",
             "rule": "节点实际执行人（audit > 审批动作）> seg_rules 默认责任人"},
        ],
    }


@router.get("")
def watch():
    """跨环节联动预警（页面内版）：清单 + 按人提醒文案 + KPI。"""
    try:
        return compute_watch()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"联动预警计算失败: {e}")
