# -*- coding: utf-8 -*-
"""
deltas.py — 单据增量（未闭环 SKU 新增单据）接口
=================================================
T10：批次目录名固定后「只有一个批次」，批次对比（/api/compare）失去意义，
改为追踪**同一批未闭环 SKU 身上的增量**。

接口：
  GET  /api/deltas                基线信息 + 最近一次增量 + 窗口聚合 + 快照流水
                                  ?days=30&sku=G506&category=手机膜
  POST /api/deltas/snapshot       立即拍一张快照（?force=true 可同指纹强制）
  GET  /api/deltas/export         导出 xlsx（5 sheet，含口径说明）

首次访问会自动补拍（指纹变化才真的落一张），与 history / inflight 同思路。
全部接口支持 ?category=（手机膜/手机壳），缺省 = 默认品类。
"""

from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .. import doc_delta_service as svc
from ..category_rules import rules_for
from ..reporting import doc_delta_report

router = APIRouter(prefix="/api/deltas", tags=["deltas"])


@router.get("")
def deltas(days: int = 30, sku: str = None, category: str = None):
    """单据增量总览（days 为统计窗口天数；sku 可选，只看单个 SKU）。"""
    cat = rules_for(category).key
    try:
        svc.maybe_doc_snapshot(cat)          # 指纹变化即补拍（P1-1 同思路）
        return svc.doc_delta(cat, days=days, sku=sku)
    except Exception as e:                   # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"单据增量计算失败: {e}")


@router.post("/snapshot")
def take_snapshot(body: dict = None, force: bool = False, category: str = None):
    """立即拍一张快照。force=true 时同指纹也重拍（排障/演示用）。"""
    cat = rules_for(category).key
    try:
        r = svc.record_doc_snapshot(cat, force=bool(force))
    except Exception as e:                   # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"拍快照失败: {e}")
    event = r.get("event") or {}
    return {"ok": True, "recorded": r.get("recorded"),
            "reason": r.get("reason"), "category": cat,
            "state": r.get("state"),
            "eventAt": event.get("at"), "summary": event.get("summary"),
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


@router.get("/export")
def export_deltas(days: int = 30, sku: str = None, category: str = None):
    """导出单据增量 xlsx（新增单据 / 新动作 / 状态变化 / 未闭环SKU汇总 / 口径说明）。"""
    rs = rules_for(category)
    try:
        svc.maybe_doc_snapshot(rs.key)
        delta = svc.doc_delta(rs.key, days=days, sku=sku)
        rows = svc.window_rows(rs.key, days=days, sku=sku)
        content = doc_delta_report(delta, rows, category=rs.key,
                                   provisional=rs.provisional,
                                   targets_source=rs.targets_source)
    except Exception as e:                   # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"单据增量导出失败: {e}")

    stamp = datetime.now().strftime("%Y%m%d")
    fname = quote(f"单据增量_{rs.key}_{stamp}.xlsx")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument"
                   ".spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})
