# -*- coding: utf-8 -*-
"""timeliness.py — GET /api/timeliness/{batch} 各 Part 分段时效统计"""

from fastapi import APIRouter, HTTPException

from ..analysis import get_batch_data

router = APIRouter(prefix="/api", tags=["timeliness"])


@router.get("/timeliness/{batch}")
def get_timeliness(batch: str):
    """各 Part 全流程时效（平均/中位/P90）、箱线图数据、分段耗时、创建趋势"""
    try:
        bd = get_batch_data(batch)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"批次不存在: {batch}")
    except Exception as e:
        raise HTTPException(status_code=500,
                            detail=f"批次数据加载失败: {e}")
    return bd.timeliness()
