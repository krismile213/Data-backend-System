# -*- coding: utf-8 -*-
"""batches.py — GET /api/batches 列出可用批次"""

from fastapi import APIRouter

from ..category_rules import categories_meta
from ..config import list_batches

router = APIRouter(prefix="/api", tags=["batches"])


@router.get("/batches")
def get_batches(category: str = None, all: bool = False):
    """列出数据根目录下所有可用批次（含各批次 Part 文件清单）。

    category  指定品类（缺省 = 默认品类手机膜，行为与改造前一致）
    all=true  列出全部品类的批次（每项带 category 字段）
    """
    batches = list_batches(category, all_categories=all)
    return {
        "batches": batches,
        "categories": categories_meta(),
        "count": len(batches),
    }
