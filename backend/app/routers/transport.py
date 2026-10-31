# -*- coding: utf-8 -*-
"""
transport.py — 货件运输看板（GET /api/transport*）
==================================================
把散落在「复盘弹窗货件表 / 甘特运输段 / 预警运输明细」三处的逐票货件判定
汇总成独立视图：KPI + 按渠道/运输方式聚合 + 逐票明细。

口径与 review.py 的 build_shipments / transport_rows 完全一致：
  - Part5 发货 × Part8 到货按货件号配对；
  - 运输目标按渠道（空运15 / 海运35 / 卡航35 / 美客多·墨30 归海运 35）；
  - 方式名含「海运」归海运，其余归空运；
  - Part5 有渠道仓但无货件号 → noShip 占位（未发货渠道，不计超时）。

追踪口径（2026-11-01 用户修正）：
  - 逐票带「发往地区（渠道仓）」与「已运输天数」：在途票 = 发货→至今，
    已到货票 = 发货→到货（不再是只有到货票才有天数）；
  - **明细全量保留**：本页是「货件明细」，所有票（含到货很久的历史票）都在
    items / KPI / 渠道聚合里，不因时间被隐藏；
  - 5 天留观只作为**追踪状态**标记：到货 ≤TRACK_ARRIVED_DAYS 天 → 「留观N/5天」，
    超出 → 「已归档(到货N天)」。截断只发生在**事中日报「货件运输」sheet**
    （`inflight_service._shipment_tracking_rows`），本页不截断。

接口：
  GET /api/transport          KPI + 渠道聚合 + 逐票明细（全量）
  GET /api/transport/export   导出逐票明细 xlsx（全量）
"""

from datetime import date

from fastapi import APIRouter, HTTPException

from .review import get_review_data
from ..transport_closure import (TRACK_ARRIVED_DAYS, arrival_ago_days,
                                 elapsed_days, shipment_keep)

router = APIRouter(prefix="/api/transport", tags=["transport"])


def _agg(items):
    """对一组货件做聚合统计。"""
    arrived = [i for i in items if i.get("arr")]
    over = [i for i in arrived
            if i.get("days") is not None
            and float(i["days"]) > float(i.get("target") or 0)]
    ds = [float(i["days"]) for i in arrived if i.get("days") is not None]
    return {
        "total": len(items),
        "arrived": len(arrived),
        "notArrived": len(items) - len(arrived),
        "over": len(over),
        "avgDays": round(sum(ds) / len(ds), 1) if ds else None,
        "maxDays": max(ds) if ds else None,
        "maxOverDays": round(max(float(i["days"]) - float(i["target"])
                                 for i in over), 1) if over else 0,
    }


def compute_transport(category=None):
    """全量汇总货件运输（category 缺省 = 默认品类手机膜）。

    返回的 items = **全量明细**：在途票 + 未发货占位 + 已到货票（不论到货多久）。
    5 天留观只体现在 track / archived 标记上，不做任何过滤。
    每票附 elapsed（已运输天数）/ arrivedAgo（到货至今几天）/ track（追踪状态）。
    """
    data = get_review_data(category)
    row_by_sku = {r["sku"]: r for r in data["rows"]}
    today = date.today()

    items = []
    archived = 0                    # 到货超 5 天、只留档不再催的票数（透明计数）
    for sku, det in data["skuDetails"].items():
        row = row_by_sku.get(sku, {})
        for s in det.get("shipments") or []:
            days = s.get("days")
            target = s.get("target")
            ship, arr = s.get("ship"), s.get("arr")
            no_ship = bool(s.get("noShip"))
            # 明细全量保留：5 天只决定「追踪状态」，不再过滤任何票
            is_archived = not shipment_keep(ship, arr, today)
            if is_archived:
                archived += 1
            ago = arrival_ago_days(arr, today)
            if no_ship:
                judge = "未发货"
            elif days is None:
                judge = "未到货"
            else:
                judge = "超时" if float(days) > float(target or 0) else "达标"
            # 追踪状态：未发货 / 在途 / 留观N/5天 / 已归档(到货N天)
            if no_ship:
                track = "未发货"
            elif ago is None:
                track = "在途"
            elif is_archived:
                track = f"已归档(到货{ago}天)"
            else:
                track = f"留观{ago}/{TRACK_ARRIVED_DAYS}天"
            items.append({
                "sku": sku,
                "model": row.get("model") or "",
                "devType": det.get("devType") or "",
                "valid": bool(det.get("valid")),
                "trk": s.get("trk") or "",
                "hub": s.get("hub") or "",
                "ship": ship,
                "arr": arr,
                "days": days,
                "elapsed": elapsed_days(ship, arr, today),
                "arrivedAgo": ago,
                "track": track,
                "archived": is_archived,
                "target": target,
                "mode": s.get("mode") or "",
                "cls": s.get("cls") or "",
                "stallDays": s.get("stallDays"),
                "noShip": no_ship,
                "judge": judge,
                "bottleneck": row.get("bottleneck"),
            })

    # 排序：追踪中在前、已归档在后；组内超时优先（超时幅度降序），
    # 其次未到货/未发货，最后达标。未到货组内按已运输天数降序 ——
    # 「持续运输多少天」越长的越该先催。
    def _sort_key(i):
        rank = {"超时": 0, "未到货": 1, "未发货": 2, "达标": 3}.get(i["judge"], 9)
        over = (float(i["days"]) - float(i["target"] or 0)
                if i["judge"] == "超时" else 0)
        transit = -(i["elapsed"] or 0) if i["judge"] == "未到货" else 0
        return (1 if i["archived"] else 0, rank, -over, transit, i["sku"], i["trk"])

    items.sort(key=_sort_key)

    # ---- 按渠道 × 运输方式聚合（不含 noShip 占位行） ----
    by_hub_map = {}
    for i in items:
        if i["noShip"] or not i["hub"]:
            continue
        key = (i["hub"], i["cls"])
        by_hub_map.setdefault(key, []).append(i)
    by_hub = []
    for (hub, cls), lst in sorted(by_hub_map.items(),
                                  key=lambda kv: (-len(kv[1]), kv[0][0])):
        st = _agg(lst)
        by_hub.append({
            "hub": hub, "cls": cls, "mode": lst[0]["mode"],
            "target": lst[0]["target"], **st,
        })

    # ---- 按运输方式聚合 ----
    by_mode = []
    for cls, name in (("空运", "空运"), ("海运", "海运")):
        lst = [i for i in items if not i["noShip"] and i["cls"] == cls]
        if not lst:
            continue
        by_mode.append({"cls": cls, **_agg(lst)})

    no_ship_hubs = sorted({i["hub"] for i in items if i["noShip"]})
    kpi = _agg([i for i in items if not i["noShip"]])
    kpi.update({
        "noShip": sum(1 for i in items if i["noShip"]),
        "noShipHubs": no_ship_hubs,
        "skuCount": len({i["sku"] for i in items}),
        "overRate": round(100 * kpi["over"] / kpi["arrived"], 1)
        if kpi["arrived"] else 0.0,
        # 到货超 5 天只留档、不再催的票；tracking = 仍需盯的票（在途 + 留观 + 未发货占位外的真实票）
        "archived": archived,
        "tracking": sum(1 for i in items
                        if not i["noShip"] and not i["archived"]),
        "retentionDays": TRACK_ARRIVED_DAYS,
    })

    return {
        "computedAt": data["computedAt"],
        "kpi": kpi,
        "byMode": by_mode,
        "byHub": by_hub,
        "items": items,
    }


@router.get("")
def transport(category: str = None):
    """货件运输看板：逐票判定 + 渠道/方式聚合。"""
    try:
        return compute_transport(category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"货件运输汇总失败: {e}")


@router.get("/export")
def export_transport(category: str = None):
    """导出逐票货件明细 xlsx。"""
    import io
    import urllib.parse

    import pandas as pd
    from fastapi.responses import StreamingResponse

    try:
        data = compute_transport(category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导出失败: {e}")

    cols = ["SKU", "型号", "开发类型", "是否已闭环", "货件号", "渠道仓",
            "运输方式", "目标(天)", "发货", "到货", "已运输(天)", "判定",
            "追踪状态", "未发货停滞(天)", "头号卡点"]
    out = []
    for i in data["items"]:
        b = i.get("bottleneck") or {}
        out.append({
            "SKU": i["sku"], "型号": i["model"] or "",
            "开发类型": i["devType"] or "",
            "是否已闭环": "已闭环" if i["valid"] else "未闭环",
            "货件号": i["trk"], "渠道仓": i["hub"],
            "运输方式": i["mode"],
            "目标(天)": i["target"] if i["target"] is not None else "",
            "发货": i["ship"] or "", "到货": i["arr"] or "",
            "已运输(天)": (i["elapsed"] if i.get("elapsed") is not None
                          else (i["days"] if i["days"] is not None else "")),
            "判定": i["judge"],
            "追踪状态": i.get("track") or "",
            "未发货停滞(天)": i["stallDays"] if i.get("stallDays") is not None else "",
            "头号卡点": f"{b['dept']}·{b['seg']}({b['days']}天)" if b else "",
        })
    df = pd.DataFrame(out, columns=cols).fillna("")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="货件运输明细", index=False)
    buf.seek(0)
    filename = urllib.parse.quote("货件运输明细.xlsx")
    return StreamingResponse(buf, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
    }, media_type="application/vnd.openxmlformats-"
                   "officedocument.spreadsheetml.sheet")
