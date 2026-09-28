# -*- coding: utf-8 -*-
"""
assessment.py — 事后评估：空运口径损益推算（P1-3）
====================================================
口径（用户 2026-09-22 定稿，替换掉早先"标准最晚周期 空运125/海运145"的错误口径）：

    时钟起点 = `start_node`（默认「取数」；不是提案。提案→取数 75 天只挂「提案超期」异常，不进考核）
              ⚠️ **手机壳无「取数」节点**（品类级结构性缺失）→ 起点由品类规则集给出
                 `RuleSet.start_node = "提案"`（见 category_rules / seg_rules_shell）
    结束点   = 最晚**空运**货件到货时间  ← 空运到了就能开卖
    推迟天数 delayDays = 起点→最晚空运到货 − loss_route_limit（默认 60，对齐路线总评级底线）

    销量损失 = delayDays × 评级日均区间（XC = 1.38~5.5）
    亏损     = 销量损失 × 单价
    **增益仅"提前完成"才算**（delayDays < 0 → direction="gain"；正好 0 → "none"）

数量与金额精度：
    件数按区间原值算，对外展示四舍五入取整（172×1.38=237.36 → 237）；
    金额 = **未取整件数 × 单价** 后保留 2 位（237.36×12.0266=2854.63；
    946×12.0266=11377.16），不先取整件数再乘，避免误差累积。

降级行为（全部在 degraded 里显式标注，不静默）：
    ① 无空运货件（纯海运 SKU）→ 退化用运营路线（取数→max(上架,广告)），basis="运营路线(降级)"
    ② 商品资料未命中评级 → 用 loss_daily_default 兜底区间，degraded 含「评级未命中」
       （⚠️ 商品资料 A1 明示「手机壳产品不参与以上产品评级规则」，手机壳走此分支属口径正确）
    ③ 单价缺失 → 金额输出 None，degraded 含「缺单价」（件数区间照常给）
"""


def _days(a, b):
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 86400.0


def compute(*, sku, nodes, items, product, rules, start_node="取数"):
    """算一个 SKU 的损益。

    nodes     : {节点名: {adopted: datetime|None}}（起点节点 / 上架 / 广告）
    items     : build_shipments 产出的货件列表（含 cls 空/海 与 arr 到货时间）
    product   : {"grade", "dailyMin", "dailyMax", "price", "priceType"} 或 None
    rules     : 生效规则（读 loss_route_limit / loss_daily_default）
    start_node: 考核时钟起点节点名 —— 手机膜="取数"、手机壳="提案"
                （由 category_rules.RuleSet.start_node 给出）

    无法评估（缺起点时间 / 无可用终点）返回 None。
    """
    limit = rules.get("loss_route_limit", 60)
    fallback = rules.get("loss_daily_default") or {"min": 1.38, "max": 5.5}
    degraded = []

    t_data = (nodes.get(start_node) or {}).get("adopted")
    if t_data is None:
        return None

    air = [it["arr"] for it in items
           if it.get("cls") == "空运" and it.get("arr") is not None]
    if air:
        end, basis = max(air), "空运"
    else:
        # 降级：没有空运货件 → 退回运营路线终点（取数→max(上架,广告)）
        pool = [t for t in ((nodes.get("上架") or {}).get("adopted"),
                            (nodes.get("广告") or {}).get("adopted"))
                if t is not None]
        if not pool:
            return None
        end, basis = max(pool), "运营路线(降级)"
        degraded.append("无空运货件·按运营路线降级")

    span = _days(t_data, end)
    if span is None:
        return None
    delay = span - float(limit)

    prod = product or {}
    grade = prod.get("grade") or ""
    dmin, dmax = prod.get("dailyMin"), prod.get("dailyMax")
    # ⚠️ 档位表里 X = 「暂无销售数据」，区间是 (0, 0) —— 那不是销量区间而是"没有数据"。
    #    若照用会算出「晚 N 天、亏损 ¥0」这种静默错值（手机壳 K011-010 实测命中：
    #    晚 50.6 天 / 金额 0），比缺数据更危险。视同区间不可用 → 兜底 + 显式降级。
    no_volume_band = (dmin is not None and dmax is not None
                      and float(dmin) == 0.0 and float(dmax) == 0.0)
    if no_volume_band:
        degraded.append(f"档位 {grade or '?'} 无日均区间(暂无销售数据)·按兜底区间")
        dmin = dmax = None
    if dmin is None or dmax is None:
        if not no_volume_band:
            degraded.append("评级区间缺失·按兜底区间" if grade else "评级未命中")
        dmin = fallback.get("min") if dmin is None else dmin
        dmax = fallback.get("max") if dmax is None else dmax
    if dmin is None or dmax is None:
        dmin, dmax = 1.38, 5.5
        degraded.append("兜底区间亦缺失·用内置 XC")

    price = prod.get("price")
    if price is None:
        degraded.append("缺单价")

    direction = "loss" if delay > 0 else ("gain" if delay < 0 else "none")
    mag = abs(delay)
    qty_min, qty_max = mag * float(dmin), mag * float(dmax)
    if price is not None:
        amount_min = round(qty_min * float(price), 2)
        amount_max = round(qty_max * float(price), 2)
    else:
        amount_min = amount_max = None

    return {
        "sku": sku,
        "grade": grade or None,
        "basis": basis,
        "startNode": start_node,             # 考核时钟起点（膜=取数 / 壳=提案）
        "startAt": t_data.strftime("%Y-%m-%d"),
        "spanDays": round(span, 1),          # 起点 → 终点
        "limit": limit,
        "delayDays": round(delay, 1),
        "direction": direction,
        "qtyMin": round(qty_min),            # 展示用：四舍五入取整
        "qtyMax": round(qty_max),
        "amountMin": amount_min,             # 未取整件数 × 单价，保留 2 位
        "amountMax": amount_max,
        "dailyMin": dmin,
        "dailyMax": dmax,
        "price": price,
        "priceType": prod.get("priceType"),
        "degraded": degraded,
    }


def summarize(items):
    """全量汇总（前端 KPI 与导出用）。"""
    done = [x for x in items if x]
    loss = [x for x in done if x["direction"] == "loss"]
    gain = [x for x in done if x["direction"] == "gain"]

    def _sum(seq, key):
        vals = [x[key] for x in seq if x.get(key) is not None]
        return round(sum(vals), 2) if vals else 0

    return {
        "assessedCount": len(done),
        "lossCount": len(loss),
        "gainCount": len(gain),
        "noneCount": len(done) - len(loss) - len(gain),
        "lossTotalMin": _sum(loss, "amountMin"),
        "lossTotalMax": _sum(loss, "amountMax"),
        "gainTotalMin": _sum(gain, "amountMin"),
        "gainTotalMax": _sum(gain, "amountMax"),
        "lossQtyMin": _sum(loss, "qtyMin"),
        "lossQtyMax": _sum(loss, "qtyMax"),
        "degradedCount": sum(1 for x in done if x["degraded"]),
    }
