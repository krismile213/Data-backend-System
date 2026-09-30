# -*- coding: utf-8 -*-
"""
rules.py — 时效规则配置中心接口
  GET /api/rules        当前生效规则（含默认值、说明、来源）
  PUT /api/rules        合并保存规则（合法键校验），保存后复盘/预警缓存立即失效
  POST /api/rules/reset 恢复代码内置默认值

⚠️ 这里**只有可编辑规则项**。调度类信息（自动管线触发时刻）不是规则：它由
Windows 计划任务决定，GET 里以只读字段 `pipeline` 带出（真源见 rule_service）。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..review_rules import PART_LIST
from ..rule_service import (RULE_META, _default_rules, get_rules,
                            pipeline_schedule, save_rules)
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

# 事中预警 / 日报 / 邮件通道（P0/P1）：数值键 (下限, 上限, 类型, 人话)
_NUM_KEYS = {
    "inflight_warn_ratio": (0.0, 1.0, float, "0~1 之间的比例"),
    "report_retention_days": (1, 3650, int, "1~3650 的整数"),
    "loss_route_limit": (0, 3650, int, "0~3650 的整数"),
    "mail_port": (1, 65535, int, "1~65535 的端口号"),
}
# 文本键 → 最大长度
_STR_KEYS = {
    "products_xlsx_path": 512,
    "registry_xlsx_path": 512,
    "mail_to": 256, "mail_host": 256, "mail_user": 256, "mail_auth_code": 256,
}
_BOOL_KEYS = {"mail_enabled"}


class RulePatch(BaseModel):
    patch: dict = Field(default_factory=dict)


@router.get("")
@router.get("/")
def read_rules():
    """当前生效规则 + 默认值 + 中文说明 + 段级动作映射定义 + 只读调度信息。"""
    rules, version = get_rules()
    return {
        "version": version,
        "rules": rules,
        "defaults": _default_rules(),
        "meta": RULE_META,
        "parts": PART_LIST,
        "segs": seg_meta(),
        # 只读：自动管线的实际触发时刻（真源 = scripts/install_auto_etl.ps1）。
        # 曾经是可编辑键 daily_pipeline_slots，与真实调度脱钩 → 2026-09-24 删除。
        "pipeline": pipeline_schedule(),
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
        elif k in _NUM_KEYS:
            lo, hi, typ, human = _NUM_KEYS[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) \
                    or not (lo <= v <= hi):
                raise HTTPException(status_code=422, detail=f"{k} 需为 {human}")
            clean[k] = typ(v)
        elif k in _BOOL_KEYS:
            if not isinstance(v, bool):
                raise HTTPException(status_code=422, detail=f"{k} 需为布尔值")
            clean[k] = v
        elif k in _STR_KEYS:
            if not isinstance(v, str) or len(v) > _STR_KEYS[k]:
                raise HTTPException(
                    status_code=422,
                    detail=f"{k} 需为长度 ≤{_STR_KEYS[k]} 的文本")
            clean[k] = v.strip()
        elif k == "loss_daily_default":
            if not isinstance(v, dict):
                raise HTTPException(status_code=422, detail="loss_daily_default 必须是对象")
            base = {}
            for f in ("min", "max"):
                if f not in v:
                    continue
                n = v[f]
                if isinstance(n, bool) or not isinstance(n, (int, float)) or n < 0:
                    raise HTTPException(status_code=422,
                                        detail=f"loss_daily_default.{f} 需为非负数值")
                base[f] = float(n)
            cur, _ = get_rules()
            base = {**(cur.get("loss_daily_default") or {}), **base}
            if (base.get("min") is not None and base.get("max") is not None
                    and base["min"] > base["max"]):
                raise HTTPException(status_code=422,
                                    detail="loss_daily_default 的下限不能大于上限")
            clean[k] = base
        else:
            raise HTTPException(status_code=422, detail=f"未知规则项: {k}")

    if not clean:
        raise HTTPException(status_code=422, detail="没有可保存的规则项")
    version = save_rules(clean)
    rules, _ = get_rules()
    _warm_after_rule_change(version)
    return {"ok": True, "version": version, "rules": rules}


@router.post("/reset")
def reset_rules():
    """恢复代码内置默认值（写入 DB 覆盖层）。"""
    version = save_rules(_default_rules())
    rules, _ = get_rules()
    _warm_after_rule_change(version)
    return {"ok": True, "version": version, "rules": rules}


def _warm_after_rule_change(version: int) -> None:
    """规则版本变了 → review / alerts 的缓存键随之失效，后台立刻重算，
    免得「改完规则后第一个打开复盘页的人」等上几秒。"""
    from ..prewarm import warm_async
    warm_async(f"规则更新（version={version}）")
