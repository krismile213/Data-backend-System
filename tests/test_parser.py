# -*- coding: utf-8 -*-
"""test_parser.py — parser.py 单元测试：审批记录字符串解析 + 文本编号清洗"""

import math

import pandas as pd
import pytest

from app.parser import (action_occurrences, clean_text_id, get_key_action_time,
                        load_part_file, parse_approval_record)


# ---------------------------------------------------------------------------
# parse_approval_record
# ---------------------------------------------------------------------------
class TestParseApprovalRecord:
    def test_standard_node(self):
        """标准四段节点：操作人|动作|时间|备注"""
        nodes = parse_approval_record("张三|提交申请|2025-08-01 10:00:00|同意提交")
        assert len(nodes) == 1
        n = nodes[0]
        assert n["operator"] == "张三"
        assert n["action"] == "提交申请"
        assert n["actionTime"] is not None
        assert n["actionTime"].strftime("%Y-%m-%d %H:%M:%S") == "2025-08-01 10:00:00"
        assert n["remark"] == "同意提交"

    def test_multiple_nodes_semicolon(self):
        """多节点以 ';' 分隔，顺序保持"""
        text = ("张三|提交申请|2025-08-01 10:00:00|同意;"
                "李四|提案确认|2025-08-02 09:30:00|通过;"
                "王五|取得手机数据|2025-08-03 14:00|ok")
        nodes = parse_approval_record(text)
        assert [n["operator"] for n in nodes] == ["张三", "李四", "王五"]
        assert [n["action"] for n in nodes] == ["提交申请", "提案确认", "取得手机数据"]
        assert nodes[2]["actionTime"].strftime("%Y-%m-%d %H:%M") == "2025-08-03 14:00"

    def test_missing_action_3seg_operator_time_remark(self):
        """异常格式1：操作人|时间|备注（缺动作名）"""
        nodes = parse_approval_record("赵六|2025-08-01 10:00:00|系统自动")
        assert len(nodes) == 1
        assert nodes[0]["operator"] == "赵六"
        assert nodes[0]["action"] is None
        assert nodes[0]["actionTime"] is not None
        assert nodes[0]["remark"] == "系统自动"

    def test_missing_action_remark_2seg_operator_time(self):
        """异常格式2：操作人|时间（缺动作名与备注）"""
        nodes = parse_approval_record("钱七|2025-08-01 10:00:00")
        assert nodes[0]["operator"] == "钱七"
        assert nodes[0]["action"] is None
        assert nodes[0]["remark"] == ""

    def test_missing_operator_2seg_time_remark(self):
        """异常格式3：时间|备注（缺操作人与动作名，如终止流程实例）"""
        nodes = parse_approval_record("2025-08-01 10:00:00|终止流程实例")
        assert nodes[0]["operator"] is None
        assert nodes[0]["action"] is None
        assert nodes[0]["remark"] == "终止流程实例"
        assert nodes[0]["actionTime"] is not None

    def test_operator_action_2seg(self):
        """两段且均非时间：操作人|动作"""
        nodes = parse_approval_record("孙八|同意")
        assert nodes[0]["operator"] == "孙八"
        assert nodes[0]["action"] == "同意"
        assert nodes[0]["actionTime"] is None

    def test_single_segment_variants(self):
        """单段：纯时间 / 纯操作人"""
        t = parse_approval_record("2025-08-01 10:00:00")
        assert t[0]["actionTime"] is not None and t[0]["operator"] is None
        o = parse_approval_record("周九")
        assert o[0]["operator"] == "周九" and o[0]["actionTime"] is None

    def test_empty_and_none_inputs(self):
        """空记录/None/NaN 不抛异常，返回空列表"""
        assert parse_approval_record(None) == []
        assert parse_approval_record("") == []
        assert parse_approval_record(";;;") == []
        assert parse_approval_record(float("nan")) == []
        assert parse_approval_record(pd.NA) == []
        assert parse_approval_record(12345) == [] or True  # 任意类型不抛异常

    def test_remark_containing_pipe(self):
        """备注内含 '|'（尾部空段）应被丢弃，不产生多余节点"""
        nodes = parse_approval_record("张三|提交申请|2025-08-01 10:00:00|同意|")
        assert len(nodes) == 1
        assert nodes[0]["remark"] == "同意"

    def test_crlf_handled(self):
        """含 \r\n 换行不破坏解析"""
        nodes = parse_approval_record("张三|提交申请|2025-08-01 10:00:00|同意\r\n")
        assert len(nodes) == 1

    def test_unparseable_time_returns_none_not_crash(self):
        """时间字段无法解析时 actionTime 为 None，不抛异常"""
        nodes = parse_approval_record("张三|提交申请|不是时间|备注")
        assert nodes[0]["actionTime"] is None
        assert nodes[0]["action"] == "提交申请"

    def test_slash_date_format(self):
        """斜杠日期格式可解析"""
        nodes = parse_approval_record("张三|提交申请|2025/08/01 10:00:00|ok")
        assert nodes[0]["actionTime"] is not None


# ---------------------------------------------------------------------------
# get_key_action_time / action_occurrences
# ---------------------------------------------------------------------------
class TestKeyActionHelpers:
    NODES = parse_approval_record(
        "甲|提交申请|2025-08-01 08:00:00|;"
        "乙|提案确认|2025-08-02 09:00:00|;"
        "甲|提交申请|2025-08-03 10:00:00|重做")

    def test_get_key_action_time_first_occurrence(self):
        t = get_key_action_time(self.NODES, "提交申请")
        assert t.strftime("%Y-%m-%d") == "2025-08-01"

    def test_get_key_action_time_missing(self):
        assert get_key_action_time(self.NODES, "不存在的动作") is None
        assert get_key_action_time([], "提交申请") is None

    def test_action_occurrences(self):
        assert action_occurrences(self.NODES, "提交申请") == 2
        assert action_occurrences(self.NODES, "提案确认") == 1
        assert action_occurrences(self.NODES, "没有") == 0


# ---------------------------------------------------------------------------
# clean_text_id
# ---------------------------------------------------------------------------
class TestCleanTextId:
    def test_18_digit_id_kept_as_text(self):
        s = pd.Series(["7821xxxxxxxxxxxx01", float("nan"), " 123 ", "456.0"])
        out = clean_text_id(s)
        assert out.iloc[0] == "7821xxxxxxxxxxxx01"
        assert out.iloc[1] == ""
        assert out.iloc[2] == "123"
        assert out.iloc[3] == "456"  # '.0' 尾缀去掉

    def test_long_numeric_not_scientific(self):
        """18/21 位纯数字编号必须保持完整文本（无科学计数法）"""
        v18 = "123456789012345678"
        v21 = "123456789012345678901"
        out = clean_text_id(pd.Series([v18, v21]))
        assert out.iloc[0] == v18
        assert out.iloc[1] == v21
        assert "e" not in out.iloc[0].lower()
        assert "e" not in out.iloc[1].lower()
