# -*- coding: utf-8 -*-
"""batches.py — GET /api/batches 列出可用批次"""

from fastapi import APIRouter

from ..config import list_batches

router = APIRouter(prefix="/api", tags=["batches"])


@router.get("/batches")
def get_batches():
    """列出数据根目录下所有可用批次（含各批次 Part 文件清单）"""
    return {"batches": list_batches()}
