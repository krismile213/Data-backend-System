# -*- coding: utf-8 -*-
"""
rules.py — 时效规则配置中心接口
  GET /api/rules        当前生效规则（含默认值、说明、来源）
  PUT /api/rules        合并保存规则（合法键校验），保存后复盘/预警缓存立即失效
  POST /api/rules/reset 恢复代码内置默认值
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..review_rules import PART_LIST
from ..rule_service import RULE_META, _default_rules, get_rules, save_rules
from ..seg_rules import seg_meta

router = APIRouter(prefix="/api/rules", tags=["rules"])

_SIMPLE_KEYS = {
    "product_target_generic": (0, 365, int),
    "product_target_non_generic": (0, 365, int),
    "ops_target": (0, 365, int),
    "proposal_stale_days": (0, 3650, int),
    "air_good": (0, 3650, int),
    "air_limit": (0, 3650, int),
    "sea_good": (0, 3650, int),
    "sea_limit": (0, 3650, int),
    "country_min": (0, 200, int),
    "stuck_days": (0, 365, int),
}


class RulePatch(BaseModel):
    patch: dict = Field(default_factory=dict)


@router.get("")
@router.get("/")
def read_rules():
    """当前生效规则 + 默认值 + 中文说明 + 段级动作映射定义。"""
    rules, version = get_rules()
    return {
        "version": version,
        "rules": rules,
        "defaults": _default_rules(),
        "meta": RULE_META,
        "parts": PART_LIST,
        "segs": seg_meta(),
    }


@router.put("")
@router.put("/")
def update_rules(body: RulePatch):
    """合并保存。校验：简单键必须是合理范围的整数；
    part_targets 结构为 {开发类型: {Part: {confirmed, suggested, available}}}。"""
    patch = body.patch or {}
    clean = {}

    for k, v in patch.items():
        if k == "part_targets":
            if not isinstance(v, dict):
                raise HTTPException(status_code=422, detail="part_targets 必须是对象")
            cur, _ = get_rules()
            merged = {dev: dict(parts) for dev, parts in cur["part_targets"].items()}
            for dev, parts in v.items():
                if dev not in merged:
                    raise HTTPException(status_code=422, detail=f"未知开发类型: {dev}")
                for pk, cfg in (parts or {}).items():
                    if pk not in PART_LIST:
                        raise HTTPException(status_code=422, detail=f"未知环节: {pk}")
                    if not isinstance(cfg, dict):
                        raise HTTPException(status_code=422,
                                            detail=f"{dev}/{pk} 配置必须是对象")
                    base = dict(merged[dev].get(pk) or {})
                    for f in ("confirmed", "suggested"):
                        if f in cfg:
                            val = cfg[f]
                            if val is not None and not (isinstance(val, (int, float))
                                                        and 0 <= val <= 3650):
                                raise HTTPException(
                                    status_code=422,
                                    detail=f"{dev}/{pk}.{f} 需为 0~3650 的数值或 null")
                            base[f] = val
                    if "available" in cfg:
                        base["available"] = bool(cfg["available"])
                    merged[dev][pk] = base
            clean["part_targets"] = merged
        elif k == "seg_targets":
            if not isinstance(v, dict):
                raise HTTPException(status_code=422, detail="seg_targets 必须是对象")
            valid_names = {s["name"] for s in seg_meta()}
            cur, _ = get_rules()
            merged = {name: dict(cfg) for name, cfg in cur["seg_targets"].items()}
            for name, cfg in v.items():
                if name not in valid_names:
                    raise HTTPException(status_code=422, detail=f"未知环节: {name}")
                if not isinstance(cfg, dict):
                    raise HTTPException(status_code=422,
                                        detail=f"{name} 配置必须是对象")
                base = dict(merged.get(name) or {})
                if "target" in cfg:
                    val = cfg["target"]
                    if val is not None and not (isinstance(val, (int, float))
                                                and 0 <= val <= 3650):
                        raise HTTPException(
                            status_code=422,
                            detail=f"{name}.target 需为 0~3650 的数值或 null")
                    base["target"] = val
                if "available" in cfg:
                    base["available"] = bool(cfg["available"])
                merged[name] = base
            clean["seg_targets"] = merged
        elif k in _SIMPLE_KEYS:
            lo, hi, typ = _SIMPLE_KEYS[k]
            # isinstance(True, int) 为真：显式拒绝布尔值混入数值规则
            if isinstance(v, bool) or not isinstance(v, (int, float)) \
                    or not (lo <= v <= hi):
                raise HTTPException(status_code=422,
                                    detail=f"{k} 需为 {lo}~{hi} 的数值")
            clean[k] = typ(v)
        else:
            raise HTTPException(status_code=422, detail=f"未知规则项: {k}")

    if not clean:
        raise HTTPException(status_code=422, detail="没有可保存的规则项")
    version = save_rules(clean)
    rules, _ = get_rules()
    return {"ok": True, "version": version, "rules": rules}


@router.post("/reset")
def reset_rules():
    """恢复代码内置默认值（写入 DB 覆盖层）。"""
    version = save_rules(_default_rules())
    rules, _ = get_rules()
    return {"ok": True, "version": version, "rules": rules}
