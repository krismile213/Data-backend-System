# -*- coding: utf-8 -*-
"""
rule_service.py — 时效规则配置中心（MySQL rule_config 表）
==========================================================
所有可调阈值/目标集中在一个 JSON 文档里（name='main'）：
  - part_targets: 开发类型×Part 的 确认目标/建议目标/是否考核（复盘贡献用）
  - 产品段/运营段/提案超期/空海运底线/到货国数/停滞阈值（加急预警用）

读取顺序：DB 覆盖 → review_rules.py 代码默认值兜底。
保存后 version+1，复盘/预警缓存据此失效，改完立即生效（无需重启）。
"""

import json
import threading
from datetime import datetime

from sqlalchemy import select, text

from .db import SessionLocal
from .review_rules import EFFICIENCY_TARGETS
from .seg_rules import default_seg_targets

_lock = threading.Lock()
_state = {"rules": None, "version": 0}


def _default_rules():
    """代码内置默认值（来源：时效确认规则表 + route_check 口径）。"""
    part_targets = {}
    for dev, parts in EFFICIENCY_TARGETS.items():
        part_targets[dev] = {
            pk: {
                "confirmed": cfg.get("confirmed_d"),
                "suggested": cfg.get("suggested_d"),
                "available": bool(cfg.get("available", False)),
            } for pk, cfg in parts.items()
        }
    return {
        "part_targets": part_targets,
        "seg_targets": default_seg_targets(),   # 段级绩效规则（实际动作映射口径）
        "product_target_generic": 23,      # 素材通用产品段目标(天)
        "product_target_non_generic": 38,  # 素材不通用产品段目标(天)
        "ops_target": 7,                   # 运营段目标(天)
        "proposal_stale_days": 75,         # 提案→取数超期阈值(天)
        "air_good": 49, "air_limit": 60,   # 空运 优/底线(天)
        "sea_good": 69, "sea_limit": 69,   # 海运 优/底线(天)
        "country_min": 14,                 # 到货国数达标线
        "stuck_days": 7,                   # 停滞预警阈值(天)
    }


# 规则项的中文说明（配置中心界面展示用）
RULE_META = {
    "seg_targets": {"label": "环节动作映射目标（段级绩效规则）",
                    "desc": "每段=起点动作→终点动作，目标天=考核口径；是否考核关闭后该段不计贡献与超时"},
    "part_targets": {"label": "Part 目标时效（旧口径·仅存档）",
                     "desc": "已被段级动作映射口径取代，保留兼容旧数据"},
    "product_target_generic": {"label": "产品段目标·素材通用(天)",
                               "desc": "取数→拍摄交付，素材通用/复用"},
    "product_target_non_generic": {"label": "产品段目标·素材不通用(天)",
                                   "desc": "取数→拍摄交付，需真机拍摄"},
    "ops_target": {"label": "运营段目标(天)",
                   "desc": "拍摄交付→max(上架,广告设置)"},
    "proposal_stale_days": {"label": "提案→取数超期(天)",
                            "desc": "超过即 P0 预警"},
    "air_good": {"label": "空运优线(天)", "desc": "≤该值为优"},
    "air_limit": {"label": "空运底线(天)", "desc": "超过即 P0 预警"},
    "sea_good": {"label": "海运优线(天)", "desc": "≤该值为优"},
    "sea_limit": {"label": "海运底线(天)", "desc": "超过即 P0 预警"},
    "country_min": {"label": "到货国数达标线", "desc": "Part8 到货渠道仓去重数"},
    "stuck_days": {"label": "停滞预警阈值(天)",
                   "desc": "卡点环节无动态超过该天数触发 P1"},
}


def get_rules():
    """当前生效规则（DB 覆盖 + 代码兜底），进程内缓存。"""
    with _lock:
        if _state["rules"] is not None:
            return _state["rules"], _state["version"]
    rules = _default_rules()
    try:
        with SessionLocal() as session:
            r = session.execute(text(
                "SELECT value FROM rule_config WHERE name='main'")).first()
            if r and r[0]:
                saved = r[0] if isinstance(r[0], dict) else json.loads(r[0])
                for k, v in saved.items():
                    if k in rules:
                        rules[k] = v
    except Exception:
        pass  # 表不存在/连接异常时用代码默认值，保证系统可用
    with _lock:
        _state["rules"] = rules
        _state["version"] += 1
        return rules, _state["version"]


def save_rules(patch: dict, updated_by: str = ""):
    """合并保存规则（只更新合法键），返回新 version。
    先在副本上合并、DB 写成功后再原子提交到进程缓存——DB 失败不污染内存规则。"""
    rules, _ = get_rules()
    merged = {k: (dict(v) if isinstance(v, dict) else v) for k, v in rules.items()}
    for k, v in (patch or {}).items():
        if k in merged and v is not None:
            merged[k] = v
    with SessionLocal() as session:
        session.execute(text(
            "INSERT INTO rule_config (name, value, updated_at, updated_by) "
            "VALUES ('main', :v, :t, :u) "
            "ON DUPLICATE KEY UPDATE value=:v, updated_at=:t, updated_by=:u"),
            {"v": json.dumps(merged, ensure_ascii=False),
             "t": datetime.now(), "u": updated_by or "admin"})
        session.commit()
    with _lock:
        _state["rules"] = merged
        _state["version"] += 1
        return _state["version"]


def eff_lookup(rules, dev: str, part: str) -> dict:
    """取 开发类型×Part 的目标配置；缺失回退常规开发（口径同 review_rules.eff_cfg）。"""
    t = rules["part_targets"].get(dev, {}).get(part)
    if t is not None:
        return {"confirmed": t.get("confirmed"),
                "suggested": t.get("suggested"),
                "available": bool(t.get("available"))}
    fb = rules["part_targets"].get("常规开发", {}).get(part, {})
    return {"confirmed": fb.get("confirmed"),
            "suggested": fb.get("suggested"),
            "available": False}
