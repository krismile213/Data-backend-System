# -*- coding: utf-8 -*-
"""test_review_rounds.py — 多轮提交修复 + 因果链 + 事后评估口径回归（P0-3 / P0-5 / P1-3）。

把 `docs/system_design.md` §8.3 的 **G506 基准**固化成断言，防止后续改动悄悄改坏口径。

G506（Redmi Note 14 4G 钢化膜，常规开发，评级 XC）实测基准（2026-09-24 复验）：

  1. 全部段 `days >= 0` —— 原「提案→取数 = -182 天」反向段消失
  2. 取数 = 2025-10-30、提案 = 2025-10-30 —— **多轮提交统一取『最早一轮』**
     （2026-09-24 起与 `流程复盘/route_check_sys.py` 对齐；改前取「取数日期最贴近
     本单据创建时间的那一轮」，会把提案取成补报轮 R2 的 2026-04-30）
  3. multiRound = {flag: True, gapDays: 182}
     （= 最晚轮创建 2026-04-30 − 最早轮创建 2025-10-30，日历天差；与 sys 的
      `multi_round_note` 的 max_gap 同式，两个系统报同一个数）
  4. 空运路线：取数→最晚空运到货 = 232 天 → delayDays = 172（超 60 底线）
     件数 [237, 946]、金额 [2854.63, 11377.16]（= **未取整**件数 × 单价 12.0266）
  5. rootCause 定到「漏报嫌疑」且 waitDays >= 170（2025-11-11 之后的整段停滞）

⚠️ 依赖真实批次数据：G506 未导入时整体 skip。本用例**只读**，不写库、不重建批次。
"""

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.assessment import compute as assess_one          # noqa: E402
from app.routers.review import compute_review             # noqa: E402


@pytest.fixture(scope="module")
def review_data():
    """全量复盘数据（module 内只算一次，全量计算约数秒）。"""
    return compute_review()


@pytest.fixture(scope="module")
def g506(review_data):
    """G506 复盘档案。"""
    detail = review_data["skuDetails"].get("G506")
    if detail is None:
        pytest.skip("G506 未导入（需含 Part1 两轮提交的真实批次数据）")
    return detail


def _iso(detail, node):
    v = (detail["nodes"].get(node) or {}).get("iso")
    assert v, f"节点「{node}」无时间"
    return datetime.strptime(v, "%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# P0-3 · 多轮提交
# ---------------------------------------------------------------------------
def test_no_negative_segment(g506):
    """轮次配对后不得再出现 days < 0 的反向段（原为 -182 天）。"""
    bad = [(s["seg"], s["days"]) for s in g506["segments"]
           if s.get("days") is not None and float(s["days"]) < 0]
    assert not bad, f"仍有反向段：{bad}"


def test_proposal_and_data_same_round(g506):
    """提案与取数必须同轮：取数 = 2025-10-30，且「提案→取数」≈ 0 天。"""
    assert _iso(g506, "取数").strftime("%Y-%m-%d") == "2025-10-30"
    assert _iso(g506, "提案").strftime("%Y-%m-%d") == "2025-10-30"
    seg = next(s for s in g506["segments"] if s["seg"] == "提案→取数")
    assert float(seg["days"]) == pytest.approx(0, abs=1)


def test_multi_round_annotation(g506):
    """多轮提交必须显式标注（间隔 182 天 · 疑似漏报），不得静默合并。"""
    mr = g506["multiRound"]
    assert mr["flag"] is True
    assert mr["count"] == 2
    assert mr["gapDays"] == pytest.approx(182, abs=2)
    assert "多轮提交" in mr["note"] and "漏报" in mr["note"]
    assert len(g506["rounds"]) == 2
    assert g506["rounds"][0]["createTime"].startswith("2025-10-30")
    assert g506["rounds"][1]["createTime"].startswith("2026-04-30")


# ---------------------------------------------------------------------------
# P0-3 · 取轮口径 = 最早一轮（纯函数，不依赖真实数据）
# ---------------------------------------------------------------------------
def _p1_doc(doc_id, create_time, data_raw=None, status="已结束"):
    """造一张 Part1 单据（手机膜口径）。data_raw=None → 该轮没填取数日期。"""
    raw = {"创建时间": create_time}
    if data_raw is not None:
        raw["手机数据取得日期"] = data_raw
    return {"doc_id": doc_id, "part": "Part1", "sku": "T", "approval_no": doc_id,
            "status": status, "create_time": create_time, "raw": raw, "nodes": []}


def test_earliest_round_wins():
    """多轮提交取『最早一轮』：提案取最早轮创建时间，不是补报的最新轮。"""
    from app.routers.review import extract_node_times, resolve_part1_rounds

    t_r1 = datetime(2025, 10, 30, 17, 1, 56)
    t_r2 = datetime(2026, 4, 30, 15, 20, 36)
    # 故意「最新在前」传入，模拟 parts_docs 的排序（create_time 降序）
    docs = {"Part1": [_p1_doc("2", t_r2, "2025-10-30"),
                      _p1_doc("1", t_r1, "2025-10-30")]}

    nodes = extract_node_times(docs)
    assert nodes["提案"]["adopted"] == t_r1, "提案必须取最早一轮，不是最新一轮"
    assert nodes["取数"]["adopted"].strftime("%Y-%m-%d") == "2025-10-30"

    base, rounds, mr = resolve_part1_rounds(docs["Part1"])
    assert base["create_time"] == t_r1
    assert len(rounds) == 2
    assert rounds[0]["createTime"].startswith("2025-10-30")
    assert rounds[1]["createTime"].startswith("2026-04-30")
    assert mr["flag"] is True
    assert mr["gapDays"] == 182          # 最晚轮创建 − 最早轮创建（日历天差）
    assert "多轮提交" in mr["note"] and "漏报" in mr["note"]


def test_earliest_doc_with_data_wins_cross_round():
    """『最早一轮』是**逐列**判定：最早轮没填取数 → 取次轮那张（跨轮＝真实等待）。"""
    from app.routers.review import extract_node_times

    t_r1 = datetime(2025, 1, 10, 9, 0)
    t_r2 = datetime(2025, 3, 20, 9, 0)
    docs = {"Part1": [_p1_doc("2", t_r2, "2025-03-18"),
                      _p1_doc("1", t_r1, None)]}          # R1 没填取数
    nodes = extract_node_times(docs)
    assert nodes["提案"]["adopted"] == t_r1                      # 仍取最早轮
    assert nodes["取数"]["adopted"].strftime("%Y-%m-%d") == "2025-03-18"


def test_same_day_duplicate_rounds_not_flagged():
    """同日重复提交（间隔 0 天）不算多轮提交异常，但仍必须给出说明。"""
    from app.routers.review import resolve_part1_rounds

    _base, _rounds, mr = resolve_part1_rounds(
        [_p1_doc("1", datetime(2025, 5, 6, 9, 0), "2025-05-06"),
         _p1_doc("2", datetime(2025, 5, 6, 15, 0), "2025-05-06")])
    assert mr["flag"] is False
    assert mr["gapDays"] is None
    assert mr["count"] == 2 and mr["note"]


# ---------------------------------------------------------------------------
# P1-3 · 空运口径损益
# ---------------------------------------------------------------------------
def test_assessment_air_span_and_delay(g506):
    """时钟起点 = 取数；终点 = 最晚空运到货；推算推迟天数。"""
    a = g506["assessment"]
    assert a is not None
    assert a["basis"] == "空运"
    assert a["spanDays"] == pytest.approx(232, abs=2)
    assert a["limit"] == 60
    assert a["delayDays"] == pytest.approx(172, abs=2)
    assert a["direction"] == "loss"


def test_assessment_amounts(g506):
    """172 × 1.38~5.5 = 237~946 件；× ¥12.0266 = ¥2,854.63~¥11,377.16。"""
    a = g506["assessment"]
    assert a["grade"] == "XC"
    assert a["dailyMin"] == pytest.approx(1.38, abs=1e-6)
    assert a["dailyMax"] == pytest.approx(5.5, abs=1e-6)
    assert a["price"] == pytest.approx(12.0266, abs=1e-4)
    assert a["priceType"] == "参考成本价"
    assert a["qtyMin"] == 237
    assert a["qtyMax"] == 946
    assert a["amountMin"] == pytest.approx(2854.63, abs=0.5)
    assert a["amountMax"] == pytest.approx(11377.16, abs=0.5)
    assert a["degraded"] == [], "G506 应完全命中商品资料，不该有降级标注"


# ---------------------------------------------------------------------------
# P0-5 · 核心原因因果链
# ---------------------------------------------------------------------------
def test_root_cause_points_to_omission(g506):
    """根因定到「漏报嫌疑」，等待 ≥170 天，且证据里说明多轮提交。"""
    rc = g506["rootCause"]
    assert rc is not None
    assert rc["suspect"] is True
    assert rc["type"] == "漏报嫌疑"
    assert rc["waitDays"] >= 170
    assert "多轮提交" in rc["evidence"]
    # 2025-11-11（签样/寄送同日）之后整段停滞，两个分支都可能夺魁
    assert rc["seg"] in ("寄送→拍摄交付", "签样→采购")


def test_root_cause_chain_starts_at_proposal(g506):
    """因果链从「提案」起步，逐节点带等待天数，终点 = 根因段的下游节点。"""
    rc = g506["rootCause"]
    chain = rc["chain"]
    assert chain and chain[0]["node"] == "提案"
    assert chain[-1]["node"] == rc["to"]
    nodes = [c["node"] for c in chain]
    assert nodes.index("取数") < nodes.index("签样")


def test_no_cross_round_inversion(review_data):
    """全量：「提案→取数」不得再出现**跨轮错配**（修复前 G506 = -182 天）。

    ⚠️ 别把判据写成 `days >= 0`：实测有 60+ 个 SKU 的取数日期早于 Part1 建单日
    （先线下取数、后补录审批，差值多在 1~29 天），那是业务现实而非错配。
    跨轮错配的特征量级 = 两轮间隔（月/季级），故用「负值超过 60 天」作判据。
    """
    bad = []
    for sku, d in review_data["skuDetails"].items():
        for s in d["segments"]:
            if (s["seg"] == "提案→取数" and s.get("days") is not None
                    and float(s["days"]) < -60):
                bad.append((sku, s["days"]))
    assert not bad, f"仍存在跨轮错配（前 5 条）：{bad[:5]}"


def test_multi_round_skus_are_annotated(review_data):
    """凡 Part1 出现 ≥2 轮的 SKU，都必须给出轮次说明（不得静默合并）。"""
    missing = []
    for sku, d in review_data["skuDetails"].items():
        rounds = d.get("rounds") or []
        if len(rounds) >= 2:
            mr = d.get("multiRound") or {}
            if mr.get("count") != len(rounds) or not mr.get("note"):
                missing.append(sku)
    assert not missing, f"多轮但无标注（前 5 个）：{missing[:5]}"


# ---------------------------------------------------------------------------
# 降级行为（纯函数，不依赖真实数据）
# ---------------------------------------------------------------------------
def _nodes(t_data, t_up=None, t_ad=None):
    d = {"取数": {"adopted": t_data}}
    if t_up:
        d["上架"] = {"adopted": t_up}
    if t_ad:
        d["广告"] = {"adopted": t_ad}
    return d


def _air(days):
    base = datetime(2025, 1, 1)
    return [{"cls": "空运", "arr": base + timedelta(days=days)}]


def test_degraded_when_grade_missing():
    """评级未命中 → 用兜底区间并标注（手机壳 SKU 属此类，是口径正确而非缺数据）。"""
    r = assess_one(sku="X", nodes=_nodes(datetime(2025, 1, 1)),
                   items=_air(300), product=None,
                   rules={"loss_route_limit": 60,
                          "loss_daily_default": {"min": 1.38, "max": 5.5}})
    assert r["direction"] == "loss"
    assert r["delayDays"] == 240
    assert "评级未命中" in r["degraded"]
    assert r["qtyMin"] == round(240 * 1.38)


def test_degraded_when_price_missing():
    """单价缺失 → 金额为 None，但件数区间照常给出。"""
    r = assess_one(sku="X", nodes=_nodes(datetime(2025, 1, 1)),
                   items=_air(300),
                   product={"grade": "XC", "dailyMin": 1.38, "dailyMax": 5.5,
                            "price": None, "priceType": ""},
                   rules={"loss_route_limit": 60})
    assert r["amountMin"] is None and r["amountMax"] is None
    # 取数 2025-01-01 → 空运到货 +300 天；推迟 300-60=240 天
    assert r["qtyMin"] == round(240 * 1.38) == 331
    assert r["qtyMax"] == round(240 * 5.5) == 1320
    assert "缺单价" in r["degraded"]


def test_degrade_to_ops_route_without_air_shipment():
    """无空运货件 → 退化为运营路线（取数→max(上架,广告)）并标注。"""
    t0 = datetime(2025, 1, 1)
    r = assess_one(sku="X", nodes=_nodes(t0, t_up=t0 + timedelta(days=197),
                                         t_ad=t0 + timedelta(days=180)),
                   items=[{"cls": "海运", "arr": t0 + timedelta(days=300)}],
                   product={"grade": "XC", "dailyMin": 1.38, "dailyMax": 5.5,
                            "price": 10, "priceType": "参考成本价"},
                   rules={"loss_route_limit": 60})
    assert r["basis"] == "运营路线(降级)"
    assert r["spanDays"] == 197
    assert r["delayDays"] == 137
    assert "无空运货件" in "；".join(r["degraded"])


def test_gain_only_when_ahead_of_schedule():
    """增益只在提前完成时出现（delayDays < 0），正好踩线算 none。"""
    t0 = datetime(2025, 1, 1)
    early = assess_one(sku="X", nodes=_nodes(t0), items=_air(30),
                       product={"grade": "XC", "dailyMin": 1.38, "dailyMax": 5.5,
                                "price": 10, "priceType": "参考成本价"},
                       rules={"loss_route_limit": 60})
    assert early["direction"] == "gain"
    assert early["delayDays"] == -30
    assert early["qtyMin"] == round(30 * 1.38)

    on_time = assess_one(sku="X", nodes=_nodes(t0), items=_air(60),
                         product={"grade": "XC", "dailyMin": 1.38,
                                  "dailyMax": 5.5, "price": 10,
                                  "priceType": "参考成本价"},
                         rules={"loss_route_limit": 60})
    assert on_time["direction"] == "none"
    assert on_time["delayDays"] == 0
