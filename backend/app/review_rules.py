# -*- coding: utf-8 -*-
"""
review_rules.py — 大流程复盘规则配置（移植自 流程复盘/review.py）
================================================================
口径与 review.py 完全一致：
  - 三部门终节点：产品部=Part6 产品营销素材加工（素材交付）
                  运营部=Part7 上架+广告设置（最终上架与广告上架完成）
                  后勤部=Part8 到货通知（渠道仓到货）
  - 有效复盘 = 三个终节点全部完成（按用户决策，已取消 3~8 月时间窗口，走全量）
  - 提前开发 = Part1 官方发布时间 >= Part1 提交申请时间
  - 目标时效按 开发类型 × Part（确认目标/建议目标/是否考核/是否有下游依赖）
后续规则调整只需改本文件（或未来做成配置中心界面）。
"""

# 9 个 Part 的关键动作链
PART_ACTIONS = {
    "Part1": ["提交申请", "提案确认", "取得手机数据", "产品信息补充", "通知_手机数据已取得"],
    "Part2": ["提交申请", "站点需求收集", "特殊需求数量收集", "全套样品测试完成", "采购下单"],
    "Part3": ["提交申请", "本地仓到货检查"],
    "Part4": ["提交申请", "新品首次上架", "新品上架完成"],
    "Part5": ["提交申请", "发货"],
    "Part6": ["提交申请", "寄送拍摄团队", "素材接收", "产品营销素材加工", "新品营销素材完成通知"],
    "Part7": ["提交申请", "上架", "广告设置"],
    "Part8": ["提交申请", "到货通知", "手机上架确认", "新品促销开始"],
    "Part9": ["提交申请"],
}

PART_NAMES = {
    "Part1": "产品开发", "Part2": "新品首单下单", "Part3": "新品本地仓到货",
    "Part4": "新品站点首次上架", "Part5": "新品渠道仓发货", "Part6": "新品素材跟进",
    "Part7": "新品站点最终上架和广告上架", "Part8": "新品渠道仓到货", "Part9": "新品环节检查",
}

# 三个部门最终节点（口径：工作任务拆分.xlsx 下周任务规划 P1）
# ⚠️ 产品部终节点=素材交付（Part6 产品营销素材加工）；
#    _dept_final() 实际计算以品类规则集 PRODUCT_END_NODE 为准。
DEPARTMENTS = {
    "产品部": {"part": "Part6", "node": "产品营销素材加工", "最终工作节点": "素材交付"},
    "运营部": {"part": "Part7", "nodes": ["上架", "广告设置"],
               "最终工作节点": "最终上架与广告上架完成"},
    "后勤部": {"part": "Part8", "node": "到货通知", "最终工作节点": "渠道仓到货"},
}

# 按 开发类型×Part 的目标时效（来源：时效确认规则表，已实测校准）
EFFICIENCY_TARGETS = {
    "提前开发": {
        "Part1": {"available": False, "confirmed_d": None, "suggested_d": None,
                  "note": "取数时间不确定(提前获取)，不列入考核"},
        "Part2": {"available": True, "confirmed_d": 1.0, "suggested_d": 1.0,
                  "note": "签样后即下单(正负1天均正常)"},
        "Part3": {"available": True, "confirmed_d": round(320 / 24, 2),
                  "suggested_d": round(320 / 24, 2), "note": "本地仓到货检查建议320h内"},
        "Part4": {"available": True, "confirmed_d": 2.0, "suggested_d": 2.0,
                  "note": "首次上架2天(含节假日7天)"},
        "Part5": {"available": True, "confirmed_d": round(840 / 24, 2),
                  "suggested_d": round(840 / 24, 2), "note": "渠道仓发货建议840h内"},
        "Part6": {"available": True, "confirmed_d": 13.0, "suggested_d": 15.0,
                  "note": "寄拍接收10天+收到素材3工作日/SKU"},
        "Part7": {"available": True, "confirmed_d": 7.0, "suggested_d": 7.0,
                  "note": "最终上架+广告按7个工作日"},
        "Part8": {"available": True, "confirmed_d": 15.0, "suggested_d": 15.0,
                  "note": "到货运输12-35天+确认1+促销2(本表取代表值15)"},
        "Part9": {"available": True, "confirmed_d": 1.0, "suggested_d": 1.0,
                  "note": "Part7完成后1天内发起检查(最终环节,不计贡献)"},
    },
    "常规开发": {
        "Part1": {"available": True, "confirmed_d": 1.0, "suggested_d": 1.0,
                  "note": "提案确认/取数/补信息 一天内"},
        "Part2": {"available": True, "confirmed_d": 1.0, "suggested_d": 1.0,
                  "note": "签样后即下单(临近下班跨工作日)"},
        "Part3": {"available": True, "confirmed_d": round(320 / 24, 2),
                  "suggested_d": round(320 / 24, 2), "note": "本地仓到货检查建议320h内"},
        "Part4": {"available": True, "confirmed_d": 2.0, "suggested_d": 2.0,
                  "note": "首次上架2天(含节假日7天)"},
        "Part5": {"available": True, "confirmed_d": round(840 / 24, 2),
                  "suggested_d": round(840 / 24, 2), "note": "渠道仓发货建议840h内"},
        "Part6": {"available": True, "confirmed_d": 3.0, "suggested_d": 3.0,
                  "note": "素材3天内一个SKU"},
        "Part7": {"available": True, "confirmed_d": 7.0, "suggested_d": 7.0,
                  "note": "最终上架+广告按7个工作日"},
        "Part8": {"available": True, "confirmed_d": 15.0, "suggested_d": 15.0,
                  "note": "确认≤1天+促销≤2天；运输12-35天(本表取代表值15)"},
        "Part9": {"available": True, "confirmed_d": 1.0, "suggested_d": 1.0,
                  "note": "Part7完成后1天内发起检查(最终环节,不计贡献)"},
    },
}

# 最终环节（无下游依赖，不计贡献）
NO_DOWNSTREAM = {"Part9"}

PART_LIST = list(PART_ACTIONS.keys())


def eff_cfg(dev: str, part: str) -> dict:
    """取 开发类型×Part 的目标配置；缺失时回退常规开发。"""
    cfg = EFFICIENCY_TARGETS.get(dev, {}).get(part)
    if cfg is not None:
        return cfg
    fallback = EFFICIENCY_TARGETS["常规开发"].get(part, {})
    return {**fallback, "available": False,
            "note": fallback.get("note", "无此开发类型配置，不考核")}
