# -*- coding: utf-8 -*-
"""
records.py — 明细查询接口
  GET /api/records/{batch}/{part}          分页 + 筛选 + 搜索 + 排序
  GET /api/records/{batch}/{part}/export   导出当前筛选结果 xlsx（审批编号为文本）
"""

import io
import urllib.parse

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..analysis import get_batch_data

router = APIRouter(prefix="/api", tags=["records"])


def _load_or_404(batch: str, part: str):
    try:
        bd = get_batch_data(batch)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"批次不存在: {batch}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批次数据加载失败: {e}")
    if part not in bd.raw:
        raise HTTPException(status_code=404,
                            detail=f"批次 {batch} 中不存在 {part}")
    return bd


@router.get("/records/{batch}/{part}")
def get_records(
    batch: str,
    part: str,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200),
    sortField: str = Query(None),
    sortOrder: str = Query("asc"),
    approvalNo: str = Query(None, description="审批编号包含匹配"),
    creator: str = Query(None, description="创建人包含匹配"),
    status: str = Query(None, description="审批状态精确匹配"),
    keyword: str = Query(None, description="全字段关键字搜索"),
):
    """明细分页查询（后端分页），每行附审批记录节点展开明细 _nodes"""
    bd = _load_or_404(batch, part)
    try:
        return bd.records(part, page=page, page_size=pageSize,
                          sort_field=sortField, sort_order=sortOrder,
                          approval_no=approvalNo, creator=creator,
                          status=status, keyword=keyword)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"明细查询失败: {e}")


@router.get("/records/{batch}/{part}/export")
def export_records(
    batch: str,
    part: str,
    approvalNo: str = Query(None),
    creator: str = Query(None),
    status: str = Query(None),
    keyword: str = Query(None),
):
    """导出当前筛选结果为 xlsx；审批编号/数据id 列强制按文本写出，杜绝精度丢失"""
    bd = _load_or_404(batch, part)
    try:
        df = bd.records_filtered(part, approval_no=approvalNo,
                                 creator=creator, status=status,
                                 keyword=keyword)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导出失败: {e}")
    # 去掉内部列，时间列转字符串
    df = df.copy()
    drop_cols = [c for c in df.columns if str(c).startswith("_")]
    df = df.drop(columns=drop_cols)
    for c in df.columns:
        if str(df[c].dtype).startswith("datetime"):
            df[c] = df[c].dt.strftime("%Y-%m-%d %H:%M:%S")
    for c in ("审批编号", "数据id"):
        if c in df.columns:
            df[c] = df[c].astype(str)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=f"{part}_{bd.name}", index=False)
    buf.seek(0)
    filename = urllib.parse.quote(f"{batch}_{part}_明细导出.xlsx")
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
    }
    return StreamingResponse(buf, headers=headers,
                             media_type="application/vnd.openxmlformats-"
                                        "officedocument.spreadsheetml.sheet")
