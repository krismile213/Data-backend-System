# -*- coding: utf-8 -*-
"""
compare.py — GET /api/compare 批次对比
  对比 2~6 个批次的 KPI、各 Part 全流程耗时、审批状态分布。
  数据复用各批次的进程内缓存（overview/timeliness 口径与单批次页一致）。
"""

from fastapi import APIRouter, HTTPException, Query

from ..analysis import get_batch_data

router = APIRouter(prefix="/api", tags=["compare"])

# KPI 中参与对比的字段
KPI_KEYS = ("total", "finished", "running", "terminated", "finishRate",
            "avgTotalHours", "anomalyCount", "anomalyDocCount")


@router.get("/compare")
def compare(batches: str = Query(..., description="逗号分隔的批次名，2~6 个")):
    """跨批次对比：每批次返回 KPI 摘要 + partBar（各 Part 耗时）+ statusPie"""
    names = [b.strip() for b in batches.split(",") if b.strip()]
    if len(names) < 2:
        raise HTTPException(status_code=400, detail="请至少选择 2 个批次进行对比")
    if len(names) > 6:
        raise HTTPException(status_code=400, detail="最多支持 6 个批次对比")
    result = []
    for name in names:
        try:
            bd = get_batch_data(name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"批次不存在: {name}")
        except Exception as e:
            raise HTTPException(status_code=500,
                                detail=f"批次数据加载失败: {name}: {e}")
        ov = bd.overview()
        tl = bd.timeliness()
        result.append({
            "batch": name,
            "baseTime": ov["baseTime"],
            "kpi": {k: ov["kpi"].get(k) for k in KPI_KEYS},
            "partBar": tl["partBar"],
            "statusPie": ov["statusPie"],
        })
    return {"batches": result}
