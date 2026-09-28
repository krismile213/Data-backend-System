# -*- coding: utf-8 -*-
"""
manual.py — 操作手册：把 docs/ 下的 SOP 文档提供给界面
========================================================
采用**白名单**方式暴露文档，避免任意文件读取。

接口：
    GET /api/manual           列出已收录文档（标题 / 说明 / 更新时间）
    GET /api/manual/{key}     取某份文档的 markdown 原文
"""

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

# docs 目录位于仓库根
DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"

# 白名单：key -> 文档元信息（新增文档只需在此登记）
ALLOWED = {
    "sop": {
        "file": "SOP-新品售前流程操作手册.md",
        "title": "新品售前流程 SOP 操作手册",
        "desc": "9 个环节的业务目标、动作链、必填字段、时效与常见卡点；新人上手路径",
    },
    "handover": {
        "file": "岗位职责与交接说明书.md",
        "title": "岗位职责与交接说明书",
        "desc": "岗位×环节职责矩阵、已离职人员影响面、单点风险与交接检查表",
    },
    "inflight": {
        "file": "流程事中与事后评估使用说明.md",
        "title": "流程事中与事后评估 使用说明",
        "desc": "事中日报页与事后评估页怎么读：段状态机、85% 预警线、日报 5 sheets、"
                "损益三指标口径、手机壳待校准提示与常见问题",
    },
}

router = APIRouter(prefix="/api/manual", tags=["manual"])


def _meta_of(key: str, meta: dict) -> dict:
    p = DOCS_DIR / meta["file"]
    exists = p.is_file()
    return {
        "key": key,
        "title": meta["title"],
        "desc": meta["desc"],
        "file": meta["file"],
        "exists": exists,
        "updatedAt": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        if exists else None,
    }


@router.get("")
def list_manuals():
    """列出已收录的手册文档"""
    return {
        "docsDir": str(DOCS_DIR),
        "items": [_meta_of(k, v) for k, v in ALLOWED.items()],
    }


@router.get("/{key}")
def get_manual(key: str):
    """取指定文档的 markdown 原文"""
    meta = ALLOWED.get(key)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"未收录的文档：{key}")
    p = DOCS_DIR / meta["file"]
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"文档文件不存在：{meta['file']}")
    return {
        "key": key,
        "title": meta["title"],
        "updatedAt": _meta_of(key, meta)["updatedAt"],
        "content": p.read_text(encoding="utf-8"),
    }
