# -*- coding: utf-8 -*-
"""
scenarios.py — 跨环节联动诊断：场景 A/B/C（GET /api/scenarios*）
================================================================
口径来源：流程复盘/流程优化建议.md（基于 手机膜8.28 批次实证）
  核心结论：Part5 发货停滞是链条断点 → 连锁卡住 Part6 素材 → 页面 coming soon。
  三个场景用于把「coming soon 待去化」的 SKU 直接列出来。

场景定义（原文档）：
  A. 已首次上架但素材未完成    → 最易进入 coming soon，优先排素材即可去化
  B. 首上架 + 素材完成，最终上架未完成 → 接近 Ready，只差最后一步
  C. Part5 发货存在未「已结束」单 → 上游停滞，连锁拖住素材/上架启动

判定口径（本系统实现，与文档对齐并显式化，便于核对）：
  首次上架完成 = 节点「首次上架」有实际时间（系统列>audit>动作），或 Part4 存在已结束单据
  素材完成     = 节点「素材加工」有实际时间，或 Part6 存在已结束单据
  最终上架完成 = 节点「上架」有实际时间，或 Part7 存在已结束单据
  发货未完成   = Part5 存在状态非「已结束」的单据（审批中/终止）
  A/B 仅在「未闭环」（复盘无效）的 SKU 中统计——已闭环的 SKU 已 Ready，不属于待去化对象；
  C 不做闭环过滤（在途单据本身即为风险），但每条附带 valid 标记。

接口：
  GET /api/scenarios          场景 A/B/C 分组清单 + KPI + 去化路径
  GET /api/scenarios/export   导出场景清单 xlsx（可选 ?code=A）
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException

from .review import _parse_dt, get_review_data

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])

# 场景元数据：名称 / 影响 / 去化路径 / 责任方 / 配色（与前端约定一致）
SCENARIOS = [
    {
        "code": "A", "name": "已首上架·素材未完成",
        "impact": "最易进入 coming soon（页面已可下单但无素材）",
        "path": "素材完成 → Part7 最终上架",
        "owner": "素材组 / 运营",
        "color": "#d32f2f",
    },
    {
        "code": "B", "name": "素材已完成·最终上架未完成",
        "impact": "接近 Ready，只差最后一步",
        "path": "Part7 最终上架",
        "owner": "运营",
        "color": "#ed6c02",
    },
    {
        "code": "C", "name": "Part5 发货未完成",
        "impact": "上游停滞，连锁拖住素材 / 上架启动",
        "path": "Part5 完成发货（建议素材与发货解耦、提前启动）",
        "owner": "后勤 / 发货",
        "color": "#0277bd",
    },
]

RULES = [
    {"key": "首次上架完成", "rule": "节点「首次上架」有实际时间，或 Part4 存在已结束单据"},
    {"key": "素材完成", "rule": "节点「素材加工」有实际时间，或 Part6 存在已结束单据"},
    {"key": "最终上架完成", "rule": "节点「上架」有实际时间，或 Part7 存在已结束单据"},
    {"key": "发货未完成", "rule": "Part5 存在状态非「已结束」的单据（审批中 / 终止）"},
    {"key": "统计范围", "rule": "场景 A/B 仅统计未闭环 SKU；场景 C 全量统计并附闭环标记"},
]


def _hit(nodes, part_stat, node, part):
    """节点是否已达成 → (bool, 判据文本)。节点时间优先，退化到该 Part 是否有已结束单。"""
    nd = nodes.get(node) or {}
    if nd.get("iso"):
        return True, f"{node}节点（{nd.get('src')}）"
    if (part_stat.get(part) or {}).get("done"):
        return True, f"{part} 已有已结束单据"
    return False, ""


def _since(iso, now):
    """距某时间点已过天数（保留 1 位），无法解析返回 None。"""
    t = _parse_dt(iso)
    if t is None:
        return None
    return round((now - t).total_seconds() / 86400.0, 1)


def _item_base(sku, det, row):
    return {
        "sku": sku,
        "model": row.get("model") or "",
        "devType": det.get("devType") or "",
        "valid": bool(det.get("valid")),
        "invalidReason": row.get("invalidReason") or "",
        "bottleneck": row.get("bottleneck"),
    }


def compute_scenarios(category=None):
    """全量计算场景 A/B/C 分组清单（category 缺省 = 默认品类手机膜）。"""
    data = get_review_data(category)
    rows = {r["sku"]: r for r in data["rows"]}
    now = datetime.now()

    groups = {c["code"]: [] for c in SCENARIOS}
    hit_map = {}                       # sku -> [场景 code]

    for sku, det in data["skuDetails"].items():
        row = rows.get(sku) or {}
        nodes = det.get("nodes") or {}
        ps = det.get("partStat") or {}
        valid = bool(det.get("valid"))

        p4, _p4_src = _hit(nodes, ps, "首次上架", "Part4")
        mat, _mat_src = _hit(nodes, ps, "素材加工", "Part6")
        p7, _p7_src = _hit(nodes, ps, "上架", "Part7")
        at_p4 = (nodes.get("首次上架") or {}).get("iso")
        at_mat = (nodes.get("素材加工") or {}).get("iso")

        # ---- 场景 A：已首上架、素材未完成、未闭环 ----
        if p4 and not mat and not valid:
            d = _since(at_p4, now)
            it = _item_base(sku, det, row)
            it.update({
                "sinceLabel": "距首次上架",
                "sinceAt": (nodes.get("首次上架") or {}).get("at"),
                "sinceDays": d,
                "stage": {"首次上架": (nodes.get("首次上架") or {}).get("at"),
                          "素材加工": "—",
                          "最终上架": (nodes.get("上架") or {}).get("at") or "—"},
                "why": (f"Part4 首次上架已于 {d if d is not None else '?'} 天前完成，"
                        f"Part6 素材至今未完成（{_mat_src or '无素材加工节点、无已结束单'}）"),
                "action": "优先排素材制作；素材完成 + Part7 最终上架后即可 Ready 去化",
            })
            groups["A"].append(it)
            hit_map.setdefault(sku, []).append("A")

        # ---- 场景 B：素材完成、最终上架未完成、未闭环 ----
        elif p4 and mat and not p7 and not valid:
            d = _since(at_mat, now)
            it = _item_base(sku, det, row)
            it.update({
                "sinceLabel": "距素材完成",
                "sinceAt": (nodes.get("素材加工") or {}).get("at"),
                "sinceDays": d,
                "stage": {"首次上架": (nodes.get("首次上架") or {}).get("at"),
                          "素材加工": (nodes.get("素材加工") or {}).get("at"),
                          "最终上架": "—"},
                "why": (f"素材已于 {d if d is not None else '?'} 天前完成，"
                        f"Part7 最终上架仍未完成"),
                "action": "催办 Part7 最终上架（素材已就绪，属于最短路径）",
            })
            groups["B"].append(it)
            hit_map.setdefault(sku, []).append("B")

        # ---- 场景 C：Part5 发货存在未结束单据 ----
        pend = det.get("part5Pending") or []
        if pend:
            p5_stat = ps.get("Part5") or {}
            dated = [(_parse_dt(p["createAt"]), p) for p in pend]
            dated = [(t, p) for t, p in dated if t is not None]
            oldest_t, oldest_p = min(dated, key=lambda x: x[0]) if dated else (None, None)
            d = (round((now - oldest_t).total_seconds() / 86400.0, 1)
                 if oldest_t else None)
            hubs = sorted({p["hub"] for p in pend if p.get("hub")})
            st_set = sorted({p["status"] for p in pend if p.get("status")})
            it = _item_base(sku, det, row)
            it.update({
                "sinceLabel": "距最早在途发货单创建",
                "sinceAt": (oldest_p or {}).get("createAt"),
                "sinceDays": d,
                "pendingCount": len(pend),
                "pendingStatus": st_set,
                "hubs": hubs,
                "stage": {"首次上架": (nodes.get("首次上架") or {}).get("at") or "—",
                          "素材加工": (nodes.get("素材加工") or {}).get("at") or "—",
                          "最终上架": (nodes.get("上架") or {}).get("at") or "—"},
                "why": (f"Part5 有 {len(pend)} 张未结束单据"
                        f"（{'/'.join(st_set) or '状态未知'}），"
                        f"最早一张已停滞 {d if d is not None else '?'} 天"
                        + (f"；涉及渠道：{'、'.join(hubs[:4])}" if hubs else "")),
                "action": "推动 Part5 发货；同时建议素材环节与发货解耦、按样品到位提前启动",
                "part5Total": p5_stat.get("total"),
                "part5Running": p5_stat.get("running"),
            })
            groups["C"].append(it)
            hit_map.setdefault(sku, []).append("C")

    # ---- 排序：停滞天数降序（无天数的排后），其次 SKU ----
    for code, items in groups.items():
        items.sort(key=lambda x: (-(x.get("sinceDays") or -1), x["sku"]))

    # ---- 汇总 ----
    summary = []
    for meta in SCENARIOS:
        items = groups[meta["code"]]
        open_items = [i for i in items if not i["valid"]]
        summary.append({
            **{k: meta[k] for k in ("code", "name", "impact", "path",
                                    "owner", "color")},
            "count": len(items),
            "openCount": len(open_items),
            "closedCount": len(items) - len(open_items),
            "maxStallDays": max((i.get("sinceDays") or 0) for i in items)
                            if items else 0,
            "skus": [i["sku"] for i in items],
        })

    # ---- 跨场景重叠 ----
    overlap = [{"sku": s, "codes": c, "count": len(c)}
               for s, c in sorted(hit_map.items()) if len(c) > 1]

    all_open = {s for s, d in data["skuDetails"].items() if not d.get("valid")}
    involved = set(hit_map)

    return {
        "computedAt": data["computedAt"],
        "totalSkus": len(data["skuDetails"]),
        "openSkus": len(all_open),
        "involvedSkus": len(involved),
        "coveredOpenSkus": len(involved & all_open),
        "summary": summary,
        "groups": [{"code": m["code"], "name": m["name"], "impact": m["impact"],
                    "path": m["path"], "owner": m["owner"], "color": m["color"],
                    "items": groups[m["code"]]}
                   for m in SCENARIOS],
        "overlap": overlap,
        "rules": RULES,
    }


@router.get("")
def scenarios(category: str = None):
    """跨环节联动诊断：场景 A/B/C 分组清单 + KPI + 去化路径。"""
    try:
        return compute_scenarios(category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"场景诊断失败: {e}")


@router.get("/export")
def export_scenarios(code: str = "", category: str = None):
    """导出场景清单 xlsx（可按场景过滤 A/B/C，默认全量）。"""
    import io
    import urllib.parse

    import pandas as pd
    from fastapi.responses import StreamingResponse

    try:
        data = compute_scenarios(category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导出失败: {e}")

    codes = [code.upper()] if code else [g["code"] for g in data["groups"]]
    cols = ["场景", "场景名称", "SKU", "型号", "开发类型", "是否已闭环",
            "停滞起点", "停滞(天)", "首次上架", "素材完成", "最终上架",
            "头号卡点", "卡点部门", "卡点耗时(天)", "在途单数", "涉及渠道",
            "判定说明", "建议动作"]
    out = []
    for g in data["groups"]:
        if g["code"] not in codes:
            continue
        for it in g["items"]:
            b = it.get("bottleneck") or {}
            st = it.get("stage") or {}
            out.append({
                "场景": g["code"], "场景名称": g["name"], "SKU": it["sku"],
                "型号": it["model"] or "",
                "开发类型": it["devType"] or "",
                "是否已闭环": "已闭环" if it["valid"] else "未闭环",
                "停滞起点": it.get("sinceAt") or "",
                "停滞(天)": it.get("sinceDays") if it.get("sinceDays") is not None else "",
                "首次上架": st.get("首次上架") or "",
                "素材完成": st.get("素材加工") or "",
                "最终上架": st.get("最终上架") or "",
                "头号卡点": b.get("seg") or "",
                "卡点部门": b.get("dept") or "",
                "卡点耗时(天)": b.get("days") if b else "",
                "在途单数": it.get("pendingCount") or "",
                "涉及渠道": "、".join(it.get("hubs") or []),
                "判定说明": it.get("why") or "",
                "建议动作": it.get("action") or "",
            })
    if not out:
        raise HTTPException(status_code=404, detail=f"场景无匹配数据: {code}")

    df = pd.DataFrame(out, columns=cols).fillna("")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="跨环节场景诊断", index=False)
    buf.seek(0)
    suffix = f"_{''.join(codes)}" if code else ""
    filename = urllib.parse.quote(f"跨环节场景诊断{suffix}.xlsx")
    return StreamingResponse(buf, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
    }, media_type="application/vnd.openxmlformats-"
                   "officedocument.spreadsheetml.sheet")
