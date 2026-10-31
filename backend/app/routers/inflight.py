# -*- coding: utf-8 -*-
"""
inflight.py — 事中管理接口（GET /api/inflight/*）
==================================================
P0-4 数据面 / P0-7 上报清单 / P1-1 进度追踪 / P1-2 段级预警。

接口（设计 §4.1 #1~#7；#8 pipeline/run 在 T05 随 auto_pipeline 落地）：
  GET  /api/inflight/overview                 在研总览（KPI + items）
  GET  /api/inflight/sku/{code}               单 SKU 事中详情（段状态 + 快照进度）
  GET  /api/inflight/alerts                   当日异常上报清单（新超期/持续超期/预警）
  GET  /api/inflight/progress?sku=&days=7     逐日新完成节点 diff
  POST /api/inflight/daily-report             生成当日日报 xlsx（幂等覆盖）
  GET  /api/inflight/daily-report             日报列表（元数据）
  GET  /api/inflight/daily-report/download    下载某日日报 ?date=&category=

全部支持 ?category=（手机膜/手机壳），缺省 = 默认品类。
首次访问当日会自动补拍节点快照（P1-1），与 history 的懒补拍同思路。
"""

import urllib.parse
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .. import inflight_service as svc
from ..category_rules import rules_for

router = APIRouter(prefix="/api/inflight", tags=["inflight"])


def _strip(data):
    """对外输出剔除内部字段（_snap_nodes 供快照用，不进响应）。"""
    data.pop("_snap_nodes", None)
    return data


@router.get("")
def inflight_overview(category: str = None):
    """在研总览：在研/超期/预警 KPI + 每个在研 SKU 的最差环节与根因。"""
    cat = rules_for(category).key
    try:
        svc.maybe_inflight_snapshot(cat)            # 当日首次访问补拍（P1-1）
        data = svc.get_inflight_data(cat)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"事中计算失败: {e}")
    return _strip(data)


@router.get("/sku/{code}")
def inflight_sku(code: str, category: str = None):
    """单 SKU 事中详情：段级状态、今日预警/超期、根因、最近 7 天节点变化。"""
    cat = rules_for(category).key
    try:
        svc.maybe_inflight_snapshot(cat)
        data = svc.get_inflight_data(cat)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"事中计算失败: {e}")
    det = (data.get("skuDetails") or {}).get(code)
    if det is None:
        raise HTTPException(status_code=404,
                            detail=f"{code} 不在「{cat}」的在研清单中"
                                   "（可能已完结/全终止/未导入）")
    det = dict(det)
    det["computedAt"] = data.get("computedAt")
    det["progress7d"] = svc.progress_for(cat, sku=code, days=7)
    return det


@router.get("/alerts")
def inflight_alerts(category: str = None):
    """当日异常上报清单（P0-7 数据源）：当日新超期 / 持续超期 / 85% 预警。"""
    cat = rules_for(category).key
    try:
        svc.maybe_inflight_snapshot(cat)
        return svc.compute_alerts_today(cat)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上报清单计算失败: {e}")


@router.get("/progress")
def inflight_progress(sku: str = None, days: int = 7, category: str = None):
    """最近 N 天逐日新完成节点（sku 缺省 = 全部在研 SKU 合并视图）。"""
    cat = rules_for(category).key
    try:
        svc.maybe_inflight_snapshot(cat)
        return {"computedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "days": days, "items": svc.progress_for(cat, sku=sku, days=days)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"进度追踪失败: {e}")


@router.post("/daily-report")
def make_daily_report(body: dict = None, category: str = None):
    """生成当日日报 xlsx（同日重跑覆盖；body 可带 {"date": "YYYY-MM-DD"}）。"""
    cat = rules_for(category).key
    date_str = None
    if isinstance(body, dict):
        date_str = body.get("date")
    try:
        result = svc.generate_daily_report(cat, date_str=date_str)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"日报生成失败: {e}")
    return {"ok": True, **result}


@router.get("/daily-report")
def list_daily_reports(category: str = None):
    """日报元数据列表（日期/路径/统计），按日期倒序。"""
    entries = svc.reports_index(category)
    return {"computedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "items": entries, "count": len(entries)}


@router.get("/daily-report/download")
def download_daily_report(date: str, category: str = None):
    """下载某日日报 xlsx（?date=YYYY-MM-DD&category=）。"""
    try:
        content, path = svc.read_report_file(date, category)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    fname = urllib.parse.quote(path.replace("\\", "/").rsplit("/", 1)[-1])
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument"
                   ".spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})


@router.post("/pipeline/run")
def run_pipeline_now(body: dict = None, category: str = None):
    """手动触发自动管线（T05 #8）：增量导入 → 快照 → 日报 → [邮件]。

    body 可带 {"skipMail": true}（默认 true——邮件试验通道要业务显式开启）。
    管线内部失败不抛异常，结果里的 ok/errors 才是真相。
    """
    import sys as _sys
    from pathlib import Path as _Path
    backend_dir = _Path(__file__).resolve().parents[2]
    if str(backend_dir) not in _sys.path:
        _sys.path.insert(0, str(backend_dir))
    from etl.auto_pipeline import run_pipeline

    payload = body if isinstance(body, dict) else {}
    result = run_pipeline(category=category,
                          skip_mail=bool(payload.get("skipMail", True)))
    return result
