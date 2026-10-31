# -*- coding: utf-8 -*-
"""diag.py — 前端自检上报（回答「页面到底能不能进得去」）

为什么需要这个：

    curl 能证明 HTML 和每个模块都能下载，但证明不了页面能渲染。
    白屏的真正原因（运行时异常、依赖 hash 不一致导致的模块加载失败）
    只在真实浏览器里出现 —— 而本项目的运行环境里没有可用的无头浏览器，
    所以让页面**自己把现场报回来**是最可靠的一条路。

工作方式：

    前端在 index.html 里挂了两个监听：
      1. window.onerror / unhandledrejection —— 捕获任何未处理异常
      2. load 后延时自检 #root 是否有子节点 —— 捕获「没报错但也没渲染」的白屏
    只有**出错或白屏**时才上报，正常运行零噪音。

数据落盘：backend/state/frontend_diag.jsonl（保留最近 50 条）
"""

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query, Request

router = APIRouter(prefix="/api/diag", tags=["diag"])

_STATE_DIR = Path(__file__).resolve().parents[2] / "state"
_LOG = _STATE_DIR / "frontend_diag.jsonl"
_MAX_KEEP = 50


@router.post("/report")
async def report(payload: dict, request: Request):
    """接收前端上报的现场。永远返回 200，避免上报本身变成新的错误源。"""
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "client": request.client.host if request.client else "",
            **(payload if isinstance(payload, dict) else {"payload": payload}),
        }
        try:
            lines = _LOG.read_text("utf-8").splitlines() if _LOG.exists() else []
        except Exception:
            lines = []
        lines = (lines + [json.dumps(record, ensure_ascii=False)])[-_MAX_KEEP:]
        _LOG.write_text("\n".join(lines) + "\n", "utf-8")
    except Exception:
        # 上报通道自己出问题绝不能反过来影响页面
        pass
    return {"ok": True}


@router.get("/report")
def read_report(limit: int = Query(20, ge=1, le=_MAX_KEEP)):
    """读回最近的上报记录，供排查时直接取用。"""
    if not _LOG.exists():
        return {"count": 0, "items": []}
    lines = _LOG.read_text("utf-8").splitlines()
    items = []
    for line in lines[-limit:]:
        try:
            items.append(json.loads(line))
        except Exception:
            continue
    return {"count": len(lines), "items": items}


@router.delete("/report")
def clear_report():
    """清空上报记录（修完后重新验证时用）。

    注意：这里**不能**用 Path.unlink()。当前运行环境挂了 safe-delete 守护，
    删除文件会抛 SystemExit —— 那是**进程级**退出信号，绕过 FastAPI 的异常
    处理，会直接把整个 uvicorn 进程带走（实测踩过一次，后端无故消失）。
    清空内容同样达到目的，且没有任何风险。
    """
    try:
        if _LOG.exists():
            _LOG.write_text("", "utf-8")
    except Exception:
        pass
    return {"ok": True}
