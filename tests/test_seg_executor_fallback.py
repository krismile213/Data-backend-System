# -*- coding: utf-8 -*-
"""
test_seg_executor_fallback.py — 段执行人回退链（审批记录兜底）
================================================================
背景（2026-10-29 用户反馈）：「手机膜的本地仓到货检查，审批人显示未配置，
但是审批记录还有历史审批人是有名字的。配置表有可能会变，但是这个不会。」

实测（手机膜 G599/G600/G601）：段「采购→本地仓到货」在 seg_rules 里责任人本就
为空，本地仓到货还没做 → 节点既无时间也无审批动作，旧逻辑直接渲染「⚠未配置」；
但这三张 Part3 单据的「当前负责人」写着夏康富 —— 信息在库里没被取用，于是
日报的「责任人」列、超期明细、绩效视角的「(未配置)」桶全错。

本文件钉住四件事：
  ① 回退顺序：审批动作 > 配置责任人 > 审批记录（当前负责人 > 历史审批人 > 审批记录原文）
     —— **配置优先于审批记录**，保证原来有名字的行一个都不被改；
  ② 多值取人：从右往左、跳过「已离职」；
  ③ 审批记录原文解析：跳过「提交申请」（常是外部公司）/通知/抄送/评论；
  ④ 运输段执行人 = Part5「发货」实际操作人（旧逻辑恒为空 → 日报 4 个在运 SKU 全「未配置」）。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers import review as rv                            # noqa: E402


def _nd(part="Part3", audit_op=None, action_op=None):
    return {"part": part, "audit_op": audit_op, "action_op": action_op}


def _doc(part="Part3", status="审批中", create="2026-10-15 13:57:20", **raw):
    return {"part": part, "status": status, "create_time": create, "raw": raw,
            "nodes": []}


# ---------------------------------------------------------------------------
# ② 多值取人
# ---------------------------------------------------------------------------
def test_pick_person_takes_rightmost():
    # 审批链上最后一位才是「这一段经手的人」
    assert rv._pick_person("张智博,李杰华,夏康富") == "夏康富"


def test_pick_person_skips_resigned():
    # 离职的人不该再被催 → 跳过，取左边在岗的那位
    assert rv._pick_person("夏康富,谢雪娜(已离职)") == "夏康富"


def test_pick_person_handles_mixed_separators():
    assert rv._pick_person("李杰华、林宇炫；夏康富") == "夏康富"
    assert rv._pick_person("  ") == ""
    assert rv._pick_person(None) == ""


# ---------------------------------------------------------------------------
# ③ 审批记录原文
# ---------------------------------------------------------------------------
def test_last_action_person_skips_apply_and_notify():
    # 巨川电子 = 外部提交方；通知/抄送 = 群发节点，都不代表该段责任人
    text = ("巨川电子|提交申请|2026-10-15 13:57:20|提交申请;\n\n"
            "张智博|站点需求收集|2026-10-13 11:42:46|audit;\n\n"
            "通知全体站点负责人正常备货数量|通知|2026-10-13 11:42:46|通知;")
    assert rv._last_action_person(text) == "张智博"


def test_last_action_person_empty_when_only_apply():
    # G599/G600/G601 的 Part3 原文就长这样 —— 取不到人时必须返回空，
    # 不能把「巨川电子」当成责任人写进日报。
    text = "巨川电子|提交申请|2026-10-15 13:54:52|提交申请"
    assert rv._last_action_person(text) == ""


# ---------------------------------------------------------------------------
# ① 回退顺序
# ---------------------------------------------------------------------------
def test_resolve_executor_prefers_action_over_config():
    name, src = rv._resolve_executor(_nd(action_op="夏康富"), "周奕奕", {})
    assert (name, src) == ("夏康富", "审批动作")


def test_resolve_executor_config_beats_approval_record():
    """配置有名字时不动它 —— 这是「纯加法」的底线：旧结果一个都不许变。"""
    docs = {"Part3": [_doc(当前负责人="夏康富")]}
    name, src = rv._resolve_executor(_nd(), "周奕奕", docs)
    assert (name, src) == ("周奕奕", "配置")


def test_resolve_executor_falls_back_to_current_owner():
    docs = {"Part3": [_doc(当前负责人="夏康富")]}
    name, src = rv._resolve_executor(_nd(), "", docs)
    assert (name, src) == ("夏康富", "审批记录·当前负责人")


def test_resolve_executor_falls_back_to_history_approver():
    docs = {"Part3": [_doc(历史审批人="张智博,李杰华,夏康富")]}
    name, src = rv._resolve_executor(_nd(), "", docs)
    assert (name, src) == ("夏康富", "审批记录·历史审批人")


def test_resolve_executor_falls_back_to_record_text():
    docs = {"Part3": [_doc(审批记录="巨川电子|提交申请|x|提交申请;\n张智博|"
                                  "站点需求收集|2026-10-13 11:42:46|audit;")]}
    name, src = rv._resolve_executor(_nd(), "", docs)
    assert (name, src) == ("张智博", "审批记录·审批动作")


def test_resolve_executor_prefers_pending_doc():
    """同 Part 多张单据：审批中的那张才代表「此刻停在谁手上」。"""
    docs = {"Part3": [
        _doc(status="已结束", create="2026-09-01 10:00:00", 当前负责人="李杰华"),
        _doc(status="审批中", create="2026-10-15 10:00:00", 当前负责人="夏康富"),
    ]}
    name, _src = rv._resolve_executor(_nd(), "", docs)
    assert name == "夏康富"


def test_resolve_executor_returns_empty_when_nothing_known():
    """真的什么都没有 → 仍然返回空，由渲染层显示「⚠未配置」（不能编）。"""
    assert rv._resolve_executor(_nd(), "", {"Part3": [_doc()]}) == ("", "")
    assert rv._resolve_executor(_nd(), "", {}) == ("", "")


# ---------------------------------------------------------------------------
# ④ 运输段执行人
# ---------------------------------------------------------------------------
def test_transport_rows_use_ship_operator():
    items = [
        {"trk": "T1", "hub": "美国-FBA", "ship": rv._parse_dt("2026-10-01"),
         "arr": rv._parse_dt("2026-10-20"), "days": 19, "mode": "海运",
         "target": 35, "cls": "海运", "noShip": False},
    ]
    rows = rv.transport_rows(items, ship_op="林惠欢")
    assert rows and all(r["executor"] == "林惠欢" for r in rows)
    assert all(r["execSrc"] == "审批动作·发货" for r in rows)


def test_transport_rows_blank_without_ship_operator():
    """没有发货记录时仍留空（交给渲染层标「未配置」），不能凭空指派。"""
    items = [
        {"trk": "T1", "hub": "美国-FBA", "ship": rv._parse_dt("2026-10-01"),
         "arr": rv._parse_dt("2026-10-20"), "days": 19, "mode": "海运",
         "target": 35, "cls": "海运", "noShip": False},
    ]
    rows = rv.transport_rows(items)
    assert rows and all(r["executor"] == "" for r in rows)


# ---------------------------------------------------------------------------
# 段级产出带来源，便于排查「这个名字是哪来的」
# ---------------------------------------------------------------------------
def test_seg_rows_carry_exec_src():
    parts = {
        "Part1": [_doc("Part1", 提案日期="2026-09-01 10:00:00")],
        "Part2": [_doc("Part2", 全套样品测试完成时间="2026-09-10 10:00:00")],
        "Part3": [_doc("Part3", 当前负责人="夏康富")],
    }
    rows, _nodes, _items = rv.review_sku_segments("G600", parts, {},
                                                  ruleset=rv.rules_for("手机膜"))
    hit = [r for r in rows if r["seg"] == "采购→本地仓到货"]
    assert len(hit) == 1
    assert hit[0]["executor"] == "夏康富"
    assert hit[0]["execSrc"] == "审批记录·当前负责人"
