# -*- coding: utf-8 -*-
"""
routes.py — 路线达标看板（GET /api/routes*）
============================================
口径移植自 流程复盘/route_check.py 的路线总览（八）与 grade 函数：

  运营路线 = 取数(Part1 取得手机数据) → max(上架, 广告设置)
             ≤52 优 / ≤60 良 / >60 差(超 60 天底线)
  空运路线 = 取数 → 最晚空运货件到货
             ≤49 优 / ≤60 良 / >60 差
  海运路线 = 取数 → 最晚海运货件到货
             ≤69 优 / >69 差（海运不稳定，不列良）

重点复盘口径（route_check 2026-09 更新）：
  运营/后勤均 ≤60 天 且 后勤 < 运营 → 「重点:后勤<运营(两条均在60天内)」= 运营环节拖后腿。

接口：
  GET /api/routes          全部 SKU 路线评级行 + 评级分布
  GET /api/routes/export   导出路线达标 xlsx
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException

from ..seg_rules import ROUTE_TARGETS
from .review import _parse_dt, get_review_data

router = APIRouter(prefix="/api/routes", tags=["routes"])

GRADE_ORDER = {"优": 0, "良": 1, "差": 2, "-": 3}


def _grade(d, good, limit):
    """route_check.grade 同款：None → '-'，≤good 优，≤limit 良，否则差。"""
    if d is None:
        return "-"
    if d <= good:
        return "优"
    if limit is not None and d <= limit:
        return "良"
    return "差"


def _days(t0, t1):
    if t0 is None or t1 is None:
        return None
    return round((t1 - t0).total_seconds() / 86400.0, 1)


def _latest_arr(shipments, cls):
    """某运输方式下最晚到货时间（忽略未发货占位行）。"""
    arrs = []
    for s in shipments or []:
        if s.get("noShip") or s.get("cls") != cls:
            continue
        t = _parse_dt(s.get("arr"))
        if t is not None:
            arrs.append(t)
    return max(arrs) if arrs else None


def _route_row(name, t0, t1, extra=None):
    cfg = ROUTE_TARGETS[name]
    d = _days(t0, t1)
    row = {
        "route": name,
        "start": t0.strftime("%Y-%m-%d %H:%M") if t0 else None,
        "end": t1.strftime("%Y-%m-%d %H:%M") if t1 else None,
        "days": d,
        "good": cfg["good"],
        "limit": cfg["limit"],
        "grade": _grade(d, cfg["good"], cfg["limit"]),
    }
    if extra:
        row.update(extra)
    return row


def compute_routes():
    """全量计算 SKU 路线评级。"""
    data = get_review_data()
    row_by_sku = {r["sku"]: r for r in data["rows"]}

    out_rows = []
    grade_dist = {k: {"优": 0, "良": 0, "差": 0, "-": 0}
                  for k in ("运营", "空运", "海运")}

    for sku, det in data["skuDetails"].items():
        nodes = det.get("nodes") or {}
        t0 = _parse_dt((nodes.get("取数") or {}).get("iso"))
        if t0 is None:
            continue                       # 无取数时间无法评级

        shipments = det.get("shipments") or []
        dept = det.get("dept") or {}

        t_ops = _parse_dt((dept.get("运营部") or {}).get("time"))
        t_hou_all = _parse_dt((dept.get("后勤部") or {}).get("time"))
        t_air = _latest_arr(shipments, "空运")
        t_sea = _latest_arr(shipments, "海运")

        routes = [
            _route_row("运营", t0, t_ops),
            _route_row("空运", t0, t_air),
            _route_row("海运", t0, t_sea),
        ]
        for r in routes:
            grade_dist[r["route"]][r["grade"]] += 1

        graded = [r for r in routes if r["grade"] != "-"]
        worst = (max(graded, key=lambda r: GRADE_ORDER[r["grade"]])["grade"]
                 if graded else "-")

        # 重点复盘口径：两条路线均 ≤60 天 且 后勤(最晚到货) < 运营 → 运营拖后腿
        d_ops = _days(t0, t_ops)
        d_hou = _days(t0, t_hou_all)
        focus = ""
        if d_ops is not None and d_hou is not None:
            if d_ops > 60:
                focus = "运营>60天底线"
            elif d_hou > 60:
                focus = "后勤>60天底线"
            elif d_hou < d_ops:
                focus = "重点:后勤<运营(两条均在60天内)"

        row = row_by_sku.get(sku, {})
        out_rows.append({
            "sku": sku,
            "model": row.get("model") or "",
            "devType": det.get("devType") or "",
            "valid": bool(det.get("valid")),
            "routes": routes,
            "worstGrade": worst,
            "focus": focus,
            "bottleneck": row.get("bottleneck"),
        })

    # 排序：差 > 良 > 优 > -，同级按最慢路线天数降序
    def _sort_key(r):
        worst_r = max((x for x in r["routes"] if x["grade"] != "-"),
                      key=lambda x: (GRADE_ORDER[x["grade"]],
                                     -(x["days"] or 0)),
                      default=None)
        return (GRADE_ORDER[r["worstGrade"]],
                -(worst_r["days"] or 0) if worst_r else 0, r["sku"])

    out_rows.sort(key=_sort_key)

    n = len(out_rows)
    route_summary = {}
    for name in ("运营", "空运", "海运"):
        gd = grade_dist[name]
        ds = [r for row in out_rows for r in row["routes"]
              if r["route"] == name and r["days"] is not None]
        route_summary[name] = {
            **gd,
            "count": len(ds),
            "avgDays": round(sum(r["days"] for r in ds) / len(ds), 1) if ds else None,
            "maxDays": max((r["days"] for r in ds), default=None),
            "overLimit": gd["差"],
        }

    return {
        "computedAt": data["computedAt"],
        "skuCount": n,
        "targets": {k: dict(v) for k, v in ROUTE_TARGETS.items()},
        "routeSummary": route_summary,
        "focusCounts": {
            "运营>60天底线": sum(1 for r in out_rows if r["focus"] == "运营>60天底线"),
            "后勤>60天底线": sum(1 for r in out_rows if r["focus"] == "后勤>60天底线"),
            "重点:后勤<运营": sum(1 for r in out_rows
                                  if r["focus"].startswith("重点")),
        },
        "rows": out_rows,
    }


@router.get("")
def routes():
    """路线达标看板：运营/空运/海运三条路线的 SKU 级评级 + 分布。"""
    try:
        return compute_routes()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"路线评级失败: {e}")


@router.get("/export")
def export_routes():
    """导出路线达标 xlsx（每 SKU 三行路线）。"""
    import io
    import urllib.parse

    import pandas as pd
    from fastapi.responses import StreamingResponse

    try:
        data = compute_routes()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导出失败: {e}")

    cols = ["SKU", "型号", "开发类型", "是否已闭环", "路线", "起点(取数)",
            "终点", "耗时(天)", "优线(天)", "良线/底线(天)", "评级",
            "重点复盘", "头号卡点"]
    out = []
    for r in data["rows"]:
        for rt in r["routes"]:
            b = r.get("bottleneck") or {}
            out.append({
                "SKU": r["sku"], "型号": r["model"] or "",
                "开发类型": r["devType"] or "",
                "是否已闭环": "已闭环" if r["valid"] else "未闭环",
                "路线": rt["route"],
                "起点(取数)": rt["start"] or "",
                "终点": rt["end"] or "",
                "耗时(天)": rt["days"] if rt["days"] is not None else "",
                "优线(天)": rt["good"],
                "良线/底线(天)": rt["limit"] if rt["limit"] is not None else "不列良",
                "评级": rt["grade"],
                "重点复盘": r["focus"] or "",
                "头号卡点": f"{b['dept']}·{b['seg']}({b['days']}天)" if b else "",
            })
    df = pd.DataFrame(out, columns=cols).fillna("")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="路线达标", index=False)
    buf.seek(0)
    filename = urllib.parse.quote("路线达标看板.xlsx")
    return StreamingResponse(buf, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
    }, media_type="application/vnd.openxmlformats-"
                   "officedocument.spreadsheetml.sheet")
