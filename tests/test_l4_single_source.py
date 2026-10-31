# -*- coding: utf-8 -*-
"""test_l4_single_source.py — 「渠道未发货超时判定线」单一真源守卫。

背景（缺陷）：业务概念「采购→发货末批 = 22 天」此前有三处独立取值，其中批次分析页
（analysis.py）用模块级冻结常量 `L4_TARGET_DAYS`、不跟随规则页 —— 于是「复盘页跟规则、
批次页永远 22」两个页面同一个数对不上。

本次改造后**单一真源链**：
    规则页 seg_targets['采购→发货末批'].target  ──优先──┐
    该品类 seg_rules.L4[3]                      ──兜底──┴─▶ rule_service.seg_target()
                                                              ├─▶ analysis.detect_anomalies(l4_target=)
                                                              └─▶ routers.review（段目标天）

覆盖（不使用真实数据绝对数写断言）：
  1. seg_target 语义逐字一致（键存在但 None 时**不**回退 default）；
  2. 判定线真的跟着规则走（决定性用例：规则值 N-1 / N+1 切换异常类型）；
  3. 批次缓存以规则版本为键（改规则后不命中旧缓存）；
  4. 默认零漂移（默认规则下取值恒为 22）；
  5. 无硬编码（analysis.py 源码不再出现 L4_TARGET_DAYS）。
"""

from datetime import datetime, timedelta

import pandas as pd
import pytest

import app.analysis as A
from app.analysis import detect_anomalies
from app.category_rules import rules_for
from app.config import PART_CONFIGS
from app.rule_service import get_rules, seg_target
from app.routers.review import _seg_judge

L4_SEG_NAME = rules_for("手机膜").l4[1]          # "采购→发货末批"
L4_DEFAULT = rules_for("手机膜").l4[3]           # 22


# ---------------------------------------------------------------------------
# 1. seg_target 语义（与 review.py 既有 `cfg.get("target", def_target)` 逐字一致）
# ---------------------------------------------------------------------------
class TestSegTargetSemantics:
    def test_explicit_none_returns_none_not_default(self):
        """键存在但值为 None（规则页显式留空）→ 返回 None，**不是**回退 default。"""
        assert seg_target({"seg_targets": {"X": {"target": None}}}, "X", 22) is None

    def test_missing_rules_returns_default(self):
        assert seg_target({}, "X", 22) == 22

    def test_missing_seg_key_returns_default(self):
        assert seg_target({"seg_targets": {}}, "X", 22) == 22

    def test_seg_cfg_without_target_returns_default(self):
        assert seg_target({"seg_targets": {"X": {}}}, "X", 22) == 22

    def test_none_seg_targets_returns_default(self):
        """seg_targets 整体为 None/缺失也不炸，回退 default。"""
        assert seg_target({"seg_targets": None}, "X", 22) == 22

    def test_value_priority(self):
        assert seg_target({"seg_targets": {"X": {"target": 7}}}, "X", 22) == 7


# ---------------------------------------------------------------------------
# 2. 决定性用例：判定线真的跟着规则走
# ---------------------------------------------------------------------------
def _part5_df(create_time):
    """合成 Part5 单据：有『发往渠道仓库』、无『货件号』、创建时间 = create_time。"""
    return pd.DataFrame({
        "审批编号": ["H001"],
        "审批状态": ["审批中"],
        "创建时间": [pd.Timestamp(create_time)],
        "发往渠道仓库": ["亚马逊美国"],
        "货件号": [None],
        "审批记录": [""],
    })


def _ship_kind(df, base_time, l4_target):
    """跑 detect_anomalies，返回唯一的『渠道未发货*』异常类型。"""
    issues = detect_anomalies(
        "Part5", df, PART_CONFIGS["Part5"]["key_actions"],
        base_time, {}, {}, None, l4_target=l4_target)
    kinds = {i["异常类型"] for i in issues
             if str(i["异常类型"]).startswith("渠道未发货")}
    assert len(kinds) == 1, f"期望恰好一条渠道未发货异常，实得 {kinds}"
    return kinds.pop()


class TestRuleDrivesTimeout:
    def test_rule_value_switches_type(self):
        """规则值 = N-1 → 超时；规则值 = N+1 → 未超时。证明判定线跟着规则走。"""
        base = datetime(2026, 9, 30, 10, 0, 0)
        n_days = 10
        df = _part5_df(base - timedelta(days=n_days))
        # 规则页把目标天改成 N-1（比已等待天数还小）→ 判超时
        tight = seg_target({"seg_targets": {L4_SEG_NAME: {"target": n_days - 1}}},
                           L4_SEG_NAME, L4_DEFAULT)
        assert tight == n_days - 1
        assert _ship_kind(df, base, tight) == "渠道未发货(超时)"
        # 规则页把目标天改成 N+1（比已等待天数大）→ 不超时
        loose = seg_target({"seg_targets": {L4_SEG_NAME: {"target": n_days + 1}}},
                           L4_SEG_NAME, L4_DEFAULT)
        assert loose == n_days + 1
        assert _ship_kind(df, base, loose) == "渠道未发货"

    def test_default_rule_value_is_timeout_boundary_22(self):
        """默认（无规则覆盖）→ 取值 22：等待 23 天判超时、21 天不判。"""
        base = datetime(2026, 9, 30, 10, 0, 0)
        over = _part5_df(base - timedelta(days=23))
        under = _part5_df(base - timedelta(days=21))
        assert _ship_kind(over, base, L4_DEFAULT) == "渠道未发货(超时)"
        assert _ship_kind(under, base, L4_DEFAULT) == "渠道未发货"

    def test_detect_anomalies_default_fallback_is_22(self):
        """不传 l4_target（哨兵 _UNSET）→ 函数内兜底 = 默认品类 L4 目标天（22）。"""
        base = datetime(2026, 9, 30, 10, 0, 0)
        over = _part5_df(base - timedelta(days=23))
        under = _part5_df(base - timedelta(days=21))

        def kind(df):
            issues = detect_anomalies(
                "Part5", df, PART_CONFIGS["Part5"]["key_actions"],
                base, {}, {}, None)          # 不传 l4_target → _UNSET → 兜底 22
            ks = {i["异常类型"] for i in issues
                  if str(i["异常类型"]).startswith("渠道未发货")}
            assert len(ks) == 1
            return ks.pop()

        assert kind(over) == "渠道未发货(超时)"
        assert kind(under) == "渠道未发货"


# ---------------------------------------------------------------------------
# 3. 缓存以规则版本为键：改规则后不命中旧缓存
# ---------------------------------------------------------------------------
class TestCacheRulesVersion:
    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        A._cache.clear()
        yield
        A._cache.clear()

    def test_rules_version_change_invalidates_cache(self, monkeypatch):
        calls = {"n": 0}
        ver = {"v": 1}

        class FakeBD:
            def __init__(self, name, path):
                calls["n"] += 1
                self.name = name
                self.path = path
                self.fingerprint = ("fp",)
                self.rules_version = A.get_rules()[1]

        monkeypatch.setattr(A, "BatchData", FakeBD)
        monkeypatch.setattr(A, "get_batch_dir", lambda name: "<dummy>")
        monkeypatch.setattr(A, "_dir_fingerprint", lambda d: ("fp",))
        monkeypatch.setattr(A, "get_rules", lambda: ({"seg_targets": {}}, ver["v"]))

        b1 = A.get_batch_data("B")
        assert calls["n"] == 1
        b2 = A.get_batch_data("B")          # 版本未变 → 命中缓存
        assert calls["n"] == 1 and b2 is b1

        ver["v"] = 2                        # 规则版本变了（数据文件指纹没变）
        b3 = A.get_batch_data("B")
        assert calls["n"] == 2, "规则版本变化后必须重算，不得命中旧缓存"
        assert b3 is not b1
        assert b3.rules_version == 2

        A.get_batch_data("B")               # 版本稳定 → 再次命中
        assert calls["n"] == 2

    def test_data_fingerprint_still_invalidates(self, monkeypatch, tmp_path):
        """既有行为不退化：数据文件指纹变了也要重算。"""
        calls = {"n": 0}
        fp = {"v": ("fp1",)}

        class FakeBD:
            def __init__(self, name, path):
                calls["n"] += 1
                self.name = name
                self.path = path
                self.fingerprint = fp["v"]
                self.rules_version = A.get_rules()[1]

        monkeypatch.setattr(A, "BatchData", FakeBD)
        monkeypatch.setattr(A, "get_batch_dir", lambda name: tmp_path)
        monkeypatch.setattr(A, "_dir_fingerprint", lambda d: fp["v"])
        monkeypatch.setattr(A, "get_rules", lambda: ({"seg_targets": {}}, 1))

        A.get_batch_data("B")
        assert calls["n"] == 1
        fp["v"] = ("fp2",)                  # 数据文件变了
        A.get_batch_data("B")
        assert calls["n"] == 2


# ---------------------------------------------------------------------------
# 4. 默认零漂移：默认规则下取值恒为 22（手机膜 / 手机壳）
# ---------------------------------------------------------------------------
class TestDefaultZeroDrift:
    def test_membrane_default_is_22(self):
        rs = rules_for("手机膜")
        assert seg_target(get_rules()[0], rs.l4[1], rs.l4[3]) == 22

    def test_shell_default_is_22(self):
        rs = rules_for("手机壳")
        assert seg_target(get_rules()[0], rs.l4[1], rs.l4[3]) == 22

    def test_both_categories_share_l4_seg_name(self):
        """两品类的『采购→发货末批』段名一致 → 规则页同一键即可覆盖两品类。"""
        assert rules_for("手机膜").l4[1] == rules_for("手机壳").l4[1] == "采购→发货末批"


# ---------------------------------------------------------------------------
# 5. 无硬编码：analysis.py 源码不再出现旧常量
# ---------------------------------------------------------------------------
class TestNoHardcodedConstant:
    def test_analysis_source_has_no_L4_TARGET_DAYS(self):
        src = (A.__file__)
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert "L4_TARGET_DAYS" not in text, "analysis.py 不应再出现冻结常量 L4_TARGET_DAYS"

    def test_analysis_wires_seg_target(self):
        """analysis.py 必须经 seg_target 取值、并把 l4_target 传入 detect_anomalies。"""
        with open(A.__file__, encoding="utf-8") as f:
            text = f.read()
        assert "seg_target(" in text
        assert "l4_target" in text


# ---------------------------------------------------------------------------
# 6. target=null 统一语义：「该段不设目标 → 不判超时」，任何路径不得抛异常
#    （前端 Rules.jsx 清空数字框会提交 target=null，rules.py 校验明确放行 null）
# ---------------------------------------------------------------------------
class TestTargetNullSemantics:
    def test_seg_judge_target_none_no_crash(self):
        """_seg_judge(d, None) 不得抛 TypeError，返回含「未设目标」。"""
        out = _seg_judge(5, None, "某段")
        assert "未设目标" in out

    def test_seg_judge_days_none_still_missing_time(self):
        """顺序不能错：d 缺时间优先返回「—缺时间」。"""
        assert _seg_judge(None, None) == "—缺时间"

    def test_seg_judge_negative_precedes_target_none(self):
        """负值（反向/补录）判定在 target=None 之前 → 数据质量标注不因「没设目标」消失。"""
        assert _seg_judge(-3, None).startswith("⚠反向")
        assert "补录" in _seg_judge(-3, None, backfill_ok=True)

    def test_seg_judge_normal_target_unaffected(self):
        """有目标天时行为不变（零回归）。"""
        assert _seg_judge(3, 7) == "✓"
        assert _seg_judge(9, 7).startswith("✗超")

    def test_detect_anomalies_l4_none_is_neutral_but_still_reported(self):
        """l4_target=None（规则页清空）→ 不判超时，但仍产出中性「渠道未发货」，不丢数据。"""
        base = datetime(2026, 9, 30, 10, 0, 0)
        df = _part5_df(base - timedelta(days=100))
        assert _ship_kind(df, base, None) == "渠道未发货"

    def test_detect_anomalies_unset_falls_back_to_default_22(self):
        """不传 l4_target（哨兵 _UNSET）→ 兜底默认品类 L4 目标天 22 → 100 天前判超时。"""
        base = datetime(2026, 9, 30, 10, 0, 0)
        df = _part5_df(base - timedelta(days=100))
        issues = detect_anomalies(
            "Part5", df, PART_CONFIGS["Part5"]["key_actions"], base, {}, {}, None)
        kinds = {i["异常类型"] for i in issues
                 if str(i["异常类型"]).startswith("渠道未发货")}
        assert kinds == {"渠道未发货(超时)"}

    def test_detect_anomalies_unset_sentinel_distinct_from_none(self):
        """哨兵与 None 必须是两种含义（防下个人把两者混掉）。"""
        assert A._UNSET is not None
        base = datetime(2026, 9, 30, 10, 0, 0)
        df = _part5_df(base - timedelta(days=100))
        unset_kind = _ship_kind(df, base, A._UNSET)
        none_kind = _ship_kind(df, base, None)
        assert unset_kind == "渠道未发货(超时)"   # _UNSET → 兜底 22 → 超时
        assert none_kind == "渠道未发货"          # 显式 None → 不设目标 → 中性
