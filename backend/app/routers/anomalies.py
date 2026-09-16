# -*- coding: utf-8 -*-
"""
anomalies.py — 异常单据接口
  GET /api/anomalies/{batch}          异常单据列表
  GET /api/anomalies/{batch}/export   导出异常清单 xlsx（审批编号为文本）
"""

import io
import urllib.parse

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..analysis import get_batch_data, jsonable

router = APIRouter(prefix="/api", tags=["anomalies"])

# 导出列顺序（仅保留清单需要的字段）
EXPORT_COLS = ["Part", "环节名称", "异常类型", "审批编号", "SKU",
               "新机型号", "审批状态", "说明"]


def _load_or_404(batch: str):
    try:
        return get_batch_data(batch)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"批次不存在: {batch}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批次数据加载失败: {e}")


@router.get("/anomalies/{batch}")
def get_anomalies(
    batch: str,
    type: str = Query(None, description="按异常类型过滤"),
    part: str = Query(None, description="按 Part 过滤，如 Part1"),
):
    """异常单据列表（含类型汇总、类型/Part 过滤）"""
    bd = _load_or_404(batch)
    return bd.anomaly_list(anomaly_type=type, part=part)


@router.get("/anomalies/{batch}/export")
def export_anomalies(
    batch: str,
    type: str = Query(None),
    part: str = Query(None),
):
    """导出异常清单为 xlsx（与页面相同的类型/Part 过滤），审批编号按文本写出"""
    bd = _load_or_404(batch)
    try:
        data = bd.anomaly_list(anomaly_type=type, part=part)
        rows = [{c: jsonable(item.get(c)) for c in EXPORT_COLS}
                for item in data["items"]]
        df = pd.DataFrame(rows, columns=EXPORT_COLS)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导出失败: {e}")
    # 空值显示为空白单元格，而非 NaN / "None"
    df = df.fillna("")
    if "审批编号" in df.columns:
        df["审批编号"] = (df["审批编号"].astype(str)
                          .replace({"None": "", "nan": "", "NaT": ""}))
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="异常清单", index=False)
    buf.seek(0)
    filename = urllib.parse.quote(f"{batch}_异常清单.xlsx")
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
    }
    return StreamingResponse(buf, headers=headers,
                             media_type="application/vnd.openxmlformats-"
                                        "officedocument.spreadsheetml.sheet")
