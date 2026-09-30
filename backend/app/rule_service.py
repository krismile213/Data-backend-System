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
import re
import threading
from datetime import datetime
from pathlib import Path

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

        # ---- 流程事中管理与事后评估（P0/P1）新增口径 ----
        # ⚠️ save_rules 只接受**此处已存在**的键，新增配置必须先加进本字典，否则保存时被静默丢弃
        "inflight_warn_ratio": 0.85,       # 段级预警系数：已进行天数 > 目标×该值 即「即将超期」
        "report_retention_days": 90,       # 事中日报保留天数
        "products_xlsx_path": r"C:\Users\dell\Desktop\work-main\资产文件\商品资料.xlsx",
        # 独立登记表（SKU换号 / 人工登记 两张人工维护子表）—— 与商品资料**分开**，
        # 因为商品资料是机器每月重导、会被整体替换；登记表是人工源、丢了不能自愈。
        "registry_xlsx_path": r"C:\Users\dell\Desktop\work-main\资产文件\SKU登记表.xlsx",
        "loss_route_limit": 60,            # 损益推迟天数底线（空运口径，对齐 ROUTE_TARGETS）
        "loss_daily_default": {"min": 1.38, "max": 5.5},   # 评级未命中时的日均兜底区间
        "mail_enabled": False,             # 邮件试验通道总开关（用户要求默认不推送）
        "mail_to": "1432640858@qq.com",    # 试验阶段唯一收件人
        "mail_host": "smtp.qq.com",
        "mail_port": 465,                  # SMTP_SSL 直连（不走 Clash HTTP 代理）
        "mail_user": "",                   # QQ 邮箱账号
        "mail_auth_code": "",              # QQ 邮箱授权码（缺失时仅记日志，不中断管线）
    }


# 规则项的中文说明（配置中心界面展示用）
RULE_META = {
    "seg_targets": {"label": "环节动作映射目标（段级绩效规则）",
                    "desc": "每段=起点动作→终点动作，目标天=考核口径；是否考核关闭后该段不计贡献与超时"},
    "part_targets": {"label": "Part 目标时效（旧口径·仅存档）",
                     "desc": "已被段级动作映射口径取代，保留兼容旧数据"},
    "product_target_generic": {"label": "产品段目标·素材通用(天)",
                               "desc": "取数→素材交付（=Part6「产品营销素材加工」，audit 时间），素材通用/复用"},
    "product_target_non_generic": {"label": "产品段目标·素材不通用(天)",
                                   "desc": "取数→素材交付（=Part6「产品营销素材加工」，audit 时间），需真机拍摄"},
    "ops_target": {"label": "运营段目标(天)",
                   "desc": "素材交付→max(上架,广告设置)"},
    "proposal_stale_days": {"label": "提案→取数超期(天)",
                            "desc": "超过即 P0 预警"},
    "air_good": {"label": "空运优线(天)", "desc": "≤该值为优"},
    "air_limit": {"label": "空运底线(天)", "desc": "超过即 P0 预警"},
    "sea_good": {"label": "海运优线(天)", "desc": "≤该值为优"},
    "sea_limit": {"label": "海运底线(天)", "desc": "超过即 P0 预警"},
    "country_min": {"label": "到货国数达标线", "desc": "Part8 到货渠道仓去重数"},
    "stuck_days": {"label": "停滞预警阈值(天)",
                   "desc": "卡点环节无动态超过该天数触发 P1"},

    # ---- 流程事中管理与事后评估 ----
    "inflight_warn_ratio": {"label": "事中预警系数",
                            "desc": "段已进行天数 > 该段目标×此系数 且未完成 → 即将超期预警（用户定：环节工期的 85%）"},
    "report_retention_days": {"label": "日报保留天数",
                              "desc": "事中日报 xlsx 的保留期，过期由自动管线清理"},
    "products_xlsx_path": {"label": "商品资料路径",
                           "desc": "评级档位与单价的来源 xlsx（商品资料表 A1 为档位说明）"},
    "registry_xlsx_path": {"label": "SKU登记表路径",
                           "desc": "「SKU换号 / 人工登记」两张人工维护子表的来源 xlsx（独立于商品资料）"},
    "loss_route_limit": {"label": "损益推迟底线(天)",
                         "desc": "空运口径：取数→最晚空运到货超过该天数即计入亏损（对齐路线总评级底线）"},
    "loss_daily_default": {"label": "日均销量兜底区间",
                           "desc": "SKU 评级未命中商品资料时的日均销量区间（输出会标注 degraded）"},
    "mail_enabled": {"label": "邮件上报开关",
                     "desc": "默认关闭（用户要求不自动推送）；开启后每日一次发送异常上报"},
    "mail_to": {"label": "上报收件人", "desc": "试验阶段固定收件邮箱"},
    "mail_host": {"label": "SMTP 服务器", "desc": "默认 smtp.qq.com，SSL 直连"},
    "mail_port": {"label": "SMTP 端口", "desc": "默认 465（SMTP_SSL）"},
    "mail_user": {"label": "发件邮箱账号", "desc": "QQ 邮箱地址；与授权码一同配置后才可发信"},
    "mail_auth_code": {"label": "邮箱授权码",
                       "desc": "QQ 邮箱 SMTP 授权码（非登录密码）；缺失时仅记日志不报错"},
}


# ---------------------------------------------------------------------------
# 自动管线的**实际**触发时刻（只读，不是可编辑规则项）
# ---------------------------------------------------------------------------
# 这里历史上放过一个可编辑键 `daily_pipeline_slots`（默认 ["09:05","14:35"]），
# 但代码里**没有任何消费方**：真正决定触发时刻的是 Windows 计划任务
# `DataAdmin-AutoPipeline`，由 scripts/install_auto_etl.ps1 注册。那个键于是
# 既不准（与已注册的 09:20/14:50 不一致）、又具有误导性（在页面上改它并不改调度）。
# 2026-09-24 删除该键，改为**从真源 ps1 读出实际时刻**只读展示。
_PIPELINE_PS1 = (Path(__file__).resolve().parents[2]
                 / "scripts" / "install_auto_etl.ps1")
_PIPELINE_TASK = "DataAdmin-AutoPipeline"


def pipeline_schedule() -> dict:
    """自动管线的实际触发时刻（只读）。

    真源 = `scripts/install_auto_etl.ps1` 里的 `$At1/$At2` —— 注册计划任务用的
    就是这两个值。读不到时 `ok=False`，前端会显示「请看脚本」而不是编一个时刻。
    """
    info = {"taskName": _PIPELINE_TASK,
            "times": [], "source": "scripts/install_auto_etl.ps1",
            "updatedAt": None, "ok": False}
    try:
        txt = _PIPELINE_PS1.read_text(encoding="utf-8-sig")     # ps1 带 UTF-8 BOM
        times = re.findall(r'^\s*\$At\d+\s*=\s*"([0-2]\d:[0-5]\d)"', txt, re.M)
        info["times"] = sorted(set(times))
        info["ok"] = bool(info["times"])
        info["updatedAt"] = datetime.fromtimestamp(
            _PIPELINE_PS1.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    return info


def get_rules():
    """当前生效规则（DB 覆盖 + 代码兜底），进程内缓存。

    ⚠️ `if k in rules` 这层过滤是**删键后的安全网**：DB 里遗留的已废弃键
    （如 `daily_pipeline_slots`）不会被加载进来，因此删键无需数据迁移。
    """
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


def seg_target(rules: dict, seg_name: str, default):
    """取段级目标天：规则页可编辑值优先，缺失回退 default。

    ⚠️ 语义必须与 review.py 既有写法 `cfg.get("target", def_target)` **逐字一致**：
    键存在但值为 None 时返回 None（= 规则页显式留空），**不是**回退 default。

    「渠道未发货超时判定线」等所有消费方都走这一个入口，保证同一口径只有一个真源。
    """
    cfg = (rules.get("seg_targets") or {}).get(seg_name) or {}
    return cfg.get("target", default)


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
