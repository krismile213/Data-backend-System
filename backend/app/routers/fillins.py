# -*- coding: utf-8 -*-
"""
fillins.py — 实际时间手工填报（免登录单机版，/api/fillins*）
================================================================
定位：路线图 #11「实际时间填报」的免登录简化版。
本系统为本机使用，不做用户体系；填报人姓名为自由文本（前端记住上次填写）。

存储：backend/state/fillins.json（进程内锁 + 临时文件原子替换写盘）。
每条记录 = (SKU, 节点) 一条：节点名与复盘节点字典(seg_rules.NODES)对齐，
重复填报同 SKU 同节点视为更正（覆盖原记录，保留首次创建时间）。

双口径对比：审批口径时间 = 复盘引擎节点实际时间（系统列 > audit > 动作）；
差异(天) = 手工填报日期 - 审批口径日期，用于发现审批记录与实际情况的偏差。

接口：
  GET    /api/fillins            元数据（节点清单 + 未闭环 SKU + 全部记录）
  GET    /api/fillins/sku/{code} 单 SKU 节点填报档案（审批口径 vs 手工，含差异）
  POST   /api/fillins            新增/更正一条填报 {sku, node, actualAt, note, operator}
  DELETE /api/fillins/{entry_id} 删除一条填报
  GET    /api/fillins/export     导出填报记录 xlsx（含双口径对比列）
"""

import io
import json
import os
import threading
import urllib.parse
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import STATE_DIR
from ..seg_rules import NODES

router = APIRouter(prefix="/api/fillins", tags=["fillins"])

_STATE_FILE = STATE_DIR / "fillins.json"
_lock = threading.Lock()


class FillinIn(BaseModel):
    sku: str
    node: str
    actualAt: str                    # YYYY-MM-DD
    note: str = ""
    operator: str = ""


def _load():
    """读填报存储；文件缺失/损坏时返回空结构（不因状态文件问题影响主功能）。"""
    if not _STATE_FILE.exists():
        return {"nextId": 1, "entries": []}
    try:
        with open(_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "entries" not in data:
            return {"nextId": 1, "entries": []}
        data.setdefault("nextId", len(data["entries"]) + 1)
        return data
    except Exception:
        return {"nextId": 1, "entries": []}


def _save(data):
    """原子写盘：先写临时文件再 os.replace，避免半写状态。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _STATE_FILE)


def _upsert(entry_in: FillinIn) -> dict:
    sku = entry_in.sku.strip()
    node = entry_in.node.strip()
    if not sku or len(sku) > 64:
        raise HTTPException(status_code=422, detail="SKU 不能为空")
    if node not in NODES:
        raise HTTPException(
            status_code=422,
            detail=f"未知节点: {node}（可选：{'、'.join(NODES)}）")
    try:
        datetime.strptime(entry_in.actualAt.strip(), "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="actualAt 需为 YYYY-MM-DD 日期")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _lock:
        data = _load()
        entries = data["entries"]
        hit = next((e for e in entries
                    if e["sku"] == sku and e["node"] == node), None)
        if hit:
            hit.update({
                "actualAt": entry_in.actualAt.strip(),
                "note": (entry_in.note or "").strip(),
                "operator": (entry_in.operator or "").strip(),
                "updatedAt": now,
            })
            saved = hit
        else:
            saved = {
                "id": data["nextId"],
                "sku": sku, "node": node,
                "actualAt": entry_in.actualAt.strip(),
                "note": (entry_in.note or "").strip(),
                "operator": (entry_in.operator or "").strip(),
                "createdAt": now, "updatedAt": now,
            }
            data["nextId"] += 1
            entries.append(saved)
        _save(data)
    return saved


# ---------------------------------------------------------------------------
# 复盘数据引用（延迟导入，避免与 review 的启动顺序耦合）
# ---------------------------------------------------------------------------
def _review():
    from .review import get_review_data
    return get_review_data()


def _approval_iso(det, node):
    return ((det or {}).get("nodes") or {}).get(node, {}).get("iso")


def _diff_days(manual: str, approval_iso):
    """差异(天) = 手工填报 - 审批口径；任一缺失返回 None。"""
    if not approval_iso:
        return None
    try:
        m = datetime.strptime(manual, "%Y-%m-%d")
    except ValueError:
        return None
    from .review import _parse_dt
    a = _parse_dt(approval_iso)
    if a is None:
        return None
    return float((m.date() - a.date()).days)


def _entry_view(e):
    """记录视图：附 Part 与审批口径对比列。"""
    part = NODES.get(e["node"], ("",))[0]
    return {
        **e,
        "part": part,
        "approvalAt": None,       # 由 sku 档案接口补全（列表接口不逐条算）
        "diffDays": None,
    }


@router.get("")
def list_fillins():
    """填报元数据：节点清单 + 未闭环 SKU + 全部记录。"""
    data = _review()
    open_skus = [
        {"sku": r["sku"], "model": r["model"] or "",
         "invalidReason": r["invalidReason"] or ""}
        for r in data["rows"] if not r["valid"]
    ]
    with _lock:
        store = _load()
    entries = [_entry_view(e) for e in
               sorted(store["entries"], key=lambda x: -x["id"])]
    nodes_meta = [
        {"node": name, "part": cfg[0],
         "defaultOwner": cfg[3], "action": cfg[2]}
        for name, cfg in NODES.items()
    ]
    return {
        "computedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "nodes": nodes_meta,
        "openSkus": open_skus,
        "entries": entries,
        "entryCount": len(entries),
        "skuCount": len({e["sku"] for e in entries}),
    }


@router.get("/sku/{code}")
def sku_fillins(code: str):
    """单 SKU 节点填报档案：每个复盘节点的 审批口径 vs 手工填报 + 差异。"""
    data = _review()
    det = data["skuDetails"].get(code)
    row = next((r for r in data["rows"] if r["sku"] == code), None)
    with _lock:
        store = _load()
    mine = {e["node"]: e for e in store["entries"] if e["sku"] == code}

    nodes_out = []
    for name, cfg in NODES.items():
        nd = ((det or {}).get("nodes") or {}).get(name) or {}
        entry = mine.get(name)
        diff = _diff_days(entry["actualAt"], nd.get("iso")) if entry else None
        nodes_out.append({
            "node": name, "part": cfg[0],
            "approvalAt": nd.get("at"), "approvalSrc": nd.get("src"),
            "entry": entry, "diffDays": diff,
        })
    return {
        "sku": code,
        "model": (row or {}).get("model") or "",
        "valid": bool(det.get("valid")) if det else None,
        "invalidReason": (row or {}).get("invalidReason") or "",
        "nodes": nodes_out,
        "filledCount": len(mine),
    }


@router.post("")
def save_fillin(body: FillinIn):
    """新增/更正一条手工填报（同 SKU 同节点覆盖）。"""
    try:
        saved = _upsert(body)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"填报保存失败: {e}")
    return {"ok": True, "entry": _entry_view(saved)}


@router.delete("/{entry_id}")
def delete_fillin(entry_id: int):
    with _lock:
        data = _load()
        before = len(data["entries"])
        data["entries"] = [e for e in data["entries"] if e["id"] != entry_id]
        if len(data["entries"]) == before:
            raise HTTPException(status_code=404, detail=f"记录不存在: {entry_id}")
        _save(data)
    return {"ok": True}


@router.get("/export")
def export_fillins():
    """导出填报记录 xlsx（含审批口径对比列）。"""
    import pandas as pd
    from fastapi.responses import StreamingResponse

    data = _review()
    details = data["skuDetails"]
    with _lock:
        store = _load()
    rows = []
    for e in sorted(store["entries"], key=lambda x: (x["sku"], x["node"])):
        ap = _approval_iso(details.get(e["sku"]), e["node"])
        rows.append({
            "SKU": e["sku"], "节点": e["node"],
            "Part": NODES.get(e["node"], ("",))[0],
            "审批口径": (ap or "")[:16],
            "手工填报": e["actualAt"],
            "差异(天)": _diff_days(e["actualAt"], ap),
            "备注": e["note"] or "",
            "填报人": e["operator"] or "",
            "更新时间": e["updatedAt"],
        })
    df = pd.DataFrame(rows, columns=["SKU", "节点", "Part", "审批口径",
                                     "手工填报", "差异(天)", "备注", "填报人",
                                     "更新时间"]).fillna("")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="手工填报", index=False)
    buf.seek(0)
    filename = urllib.parse.quote("实际时间手工填报.xlsx")
    return StreamingResponse(buf, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
    }, media_type="application/vnd.openxmlformats-"
                   "officedocument.spreadsheetml.sheet")
