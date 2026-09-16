# -*- coding: utf-8 -*-
"""overview.py — GET /api/overview/{batch} 总览 KPI"""

from fastapi import APIRouter, HTTPException

from ..analysis import get_batch_data

router = APIRouter(prefix="/api", tags=["overview"])


@router.get("/overview/{batch}")
def get_overview(batch: str):
    """批次总览：KPI 卡片数据、各 Part 状态分布、状态饼图数据"""
    try:
        bd = get_batch_data(batch)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"批次不存在: {batch}")
    except Exception as e:  # 数据损坏等，返回空结构而非 500 崩溃
        raise HTTPException(status_code=500,
                            detail=f"批次数据加载失败: {e}")
    return bd.overview()
