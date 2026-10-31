# -*- coding: utf-8 -*-
"""test_analysis.py — analysis.py 单元测试：分段耗时计算、统计口径、分页/筛选/排序"""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from app.analysis import (BatchData, delta_hour, jsonable, p90,
                          compute_statistics)
from app.config import PART_CONFIGS


# ---------------------------------------------------------------------------
# delta_hour
# ---------------------------------------------------------------------------
class TestDeltaHour:
    def test_known_interval(self):
        t1 = datetime(2025, 8, 1, 8, 0, 0)
        t2 = datetime(2025, 8, 1, 20, 30, 0)
        assert delta_hour(t1, t2) == 12.5

    def test_zero_interval(self):
        t = datetime(2025, 8, 1, 8, 0, 0)
        assert delta_hour(t, t) == 0.0

    def test_negative_interval(self):
        """结束早于开始返回负数（该场景由异常检测负责，函数本身如实计算）"""
        t1 = datetime(2025, 8, 2, 8, 0, 0)
        t2 = datetime(2025, 8, 1, 8, 0, 0)
        assert delta_hour(t1, t2) == -24.0

    def test_none_and_nat(self):
        t = datetime(2025, 8, 1, 8, 0, 0)
        assert delta_hour(None, t) is None
        assert delta_hour(t, None) is None
        assert delta_hour(pd.NaT, t) is None
        assert delta_hour(t, pd.NaT) is None


# ---------------------------------------------------------------------------
# p90 / jsonable
# ---------------------------------------------------------------------------
class TestP90:
    def test_known_series(self):
        s = pd.Series(range(1, 101), dtype=float)  # 1..100
        # pandas 默认线性插值：P90 = 90.1
        assert p90(s) == 90.1

    def test_empty(self):
        assert p90(pd.Series([], dtype=float)) is None
        assert p90(pd.Series([np.nan, np.nan])) is None

    def test_single_value(self):
        assert p90(pd.Series([5.0])) == 5.0


class TestJsonable:
    def test_nan_inf_to_none(self):
        assert jsonable(float("nan")) is None
        assert jsonable(float("inf")) is None
        assert jsonable(float("-inf")) is None

    def test_timestamp(self):
        ts = pd.Timestamp("2025-08-01 10:00:00")
        assert jsonable(ts) == "2025-08-01 10:00:00"
        assert jsonable(pd.NaT) is None

    def test_numpy_types(self):
        assert jsonable(np.int64(3)) == 3 and isinstance(jsonable(np.int64(3)), int)
        assert jsonable(np.float64(1.5)) == 1.5
        assert jsonable(np.bool_(True)) is True

    def test_plain_passthrough(self):
        assert jsonable("abc") == "abc"
        assert jsonable(None) is None


# ---------------------------------------------------------------------------
# compute_statistics（分段耗时统计口径）
# ---------------------------------------------------------------------------
class TestComputeStatistics:
    def _make_flow(self, seg_values, status="已结束"):
        """构造 Part3 的 flow 表：单分段 提交申请→本地仓到货检查(h)"""
        return pd.DataFrame({
            "Part": "Part3",
            "环节名称": "新品本地仓到货",
            "审批编号": [f"ID{i}" for i in range(len(seg_values))],
            "审批状态": status,
            "提交申请→本地仓到货检查(h)": seg_values,
            "全流程总耗时(h)": seg_values,
        })

    def test_statistics_known_values(self):
        # 已知序列：均值 12，中位数 12，min 2，max 22
        vals = [2.0, 6.0, 10.0, 14.0, 18.0, 22.0]
        flow = self._make_flow(vals)
        flow_dict = {"Part3": (flow, PART_CONFIGS["Part3"]["key_actions"])}
        stat_df, outlier_map, med_map = compute_statistics(flow_dict)

        row = stat_df[(stat_df["Part"] == "Part3")
                      & (stat_df["流程环节"] == "提交申请→本地仓到货检查(h)")].iloc[0]
        assert row["有效单据数"] == 6
        assert row["平均耗时h"] == 12.0
        assert row["最大耗时h"] == 22.0
        assert row["最小耗时h"] == 2.0
        assert row["中位数耗时h"] == 12.0
        # Q1 = 7.0, Q3 = 17.0（线性插值），IQR = 10，离群上界 = 17 + 15 = 32
        assert row["Q3耗时h"] == 17.0
        assert row["离群上界h"] == 32.0
        assert row["离群单据数"] == 0  # max(168, 35) 上限下无离群

    def test_outlier_map_and_med_map(self):
        vals = [2.0, 6.0, 10.0, 14.0, 18.0, 22.0]
        flow = self._make_flow(vals)
        flow_dict = {"Part3": (flow, PART_CONFIGS["Part3"]["key_actions"])}
        _, outlier_map, med_map = compute_statistics(flow_dict)
        assert outlier_map["Part3"]["提交申请→本地仓到货检查(h)"] == 32.0
        assert med_map["Part3"]["提交申请→本地仓到货检查(h)"] == 12.0

    def test_all_nan_segment(self):
        """分段全为空：统计行有效单据数 0，各指标 None，不抛异常"""
        flow = self._make_flow([np.nan, np.nan])
        flow_dict = {"Part3": (flow, PART_CONFIGS["Part3"]["key_actions"])}
        stat_df, outlier_map, med_map = compute_statistics(flow_dict)
        row = stat_df.iloc[0]
        assert row["有效单据数"] == 0
        assert row["平均耗时h"] is None
        assert outlier_map["Part3"]["提交申请→本地仓到货检查(h)"] is None
        assert med_map["Part3"]["提交申请→本地仓到货检查(h)"] is None

    def test_empty_flow(self):
        """空 flow 表不抛异常"""
        flow = pd.DataFrame(columns=["审批状态", "提交申请→本地仓到货检查(h)",
                                     "全流程总耗时(h)"])
        flow_dict = {"Part3": (flow, PART_CONFIGS["Part3"]["key_actions"])}
        stat_df, _, _ = compute_statistics(flow_dict)
        assert len(stat_df) == 2  # 1 个分段 + 全流程


# ---------------------------------------------------------------------------
# 分页 / 筛选 / 排序（用小数据集直接驱动 BatchData.records）
# ---------------------------------------------------------------------------
def _make_fake_batch(df):
    """绕过 BatchData.__init__（不走真实数据目录），仅注入 raw 数据"""
    bd = object.__new__(BatchData)
    bd.name = "测试批次"
    bd.path = None
    bd.raw = {"Part3": df}
    return bd


@pytest.fixture
def fake_batch():
    df = pd.DataFrame({
        "审批编号": ["A001", "A002", "A003", "A004", "A005"],
        "创建人": ["张三", "李四", "张三", "王五", "李四"],
        "审批状态": ["已结束", "审批中", "已结束", "终止", "审批中"],
        "创建时间": pd.to_datetime([
            "2025-08-01 10:00:00", "2025-08-02 10:00:00", "2025-08-03 10:00:00",
            "2025-08-04 10:00:00", "2025-08-05 10:00:00"]),
        "完成时间": pd.to_datetime(["2025-08-02 10:00:00", pd.NaT,
                                    "2025-08-04 10:00:00", pd.NaT, pd.NaT]),
        "审批记录": [
            "张三|提交申请|2025-08-01 08:00:00|ok;李四|本地仓到货检查|2025-08-02 09:00:00|done",
            "张三|提交申请|2025-08-02 08:00:00|ok",
            "",
            "王五|提交申请|2025-08-04 08:00:00|start",
            None,
        ],
        "_part": "Part3",
        "_partName": "新品本地仓到货",
    })
    return _make_fake_batch(df)


class TestRecordsPagingFilterSort:
    def test_default_page(self, fake_batch):
        r = fake_batch.records("Part3")
        assert r["total"] == 5
        assert r["page"] == 1 and r["pageSize"] == 20
        assert len(r["rows"]) == 5
        assert r["partName"] == "新品本地仓到货"

    def test_pagination(self, fake_batch):
        p1 = fake_batch.records("Part3", page=1, page_size=2)
        p2 = fake_batch.records("Part3", page=2, page_size=2)
        p3 = fake_batch.records("Part3", page=3, page_size=2)
        p4 = fake_batch.records("Part3", page=4, page_size=2)
        assert [x["审批编号"] for x in p1["rows"]] == ["A001", "A002"]
        assert [x["审批编号"] for x in p2["rows"]] == ["A003", "A004"]
        assert [x["审批编号"] for x in p3["rows"]] == ["A005"]  # 5 行 / 每页2 = 3 页
        assert p4["rows"] == [] and p4["total"] == 5

    def test_page_beyond_range(self, fake_batch):
        r = fake_batch.records("Part3", page=99)
        assert r["rows"] == []
        assert r["total"] == 5  # total 不因翻页变化

    def test_page_floor(self, fake_batch):
        """page<=1 / page_size 越界被收敛，不抛异常"""
        r = fake_batch.records("Part3", page=0)
        assert r["page"] == 1
        r = fake_batch.records("Part3", page=1, page_size=100000)
        assert r["pageSize"] == 200
        r = fake_batch.records("Part3", page=1, page_size=0)
        assert r["pageSize"] == 1

    def test_status_filter_exact(self, fake_batch):
        r = fake_batch.records("Part3", status="已结束")
        assert r["total"] == 2
        assert all(x["审批状态"] == "已结束" for x in r["rows"])

    def test_status_filter_no_hit(self, fake_batch):
        r = fake_batch.records("Part3", status="不存在状态")
        assert r["total"] == 0 and r["rows"] == []

    def test_creator_contains(self, fake_batch):
        r = fake_batch.records("Part3", creator="张三")
        assert r["total"] == 2

    def test_approval_no_contains(self, fake_batch):
        r = fake_batch.records("Part3", approval_no="A00")
        assert r["total"] == 5
        r = fake_batch.records("Part3", approval_no="003")
        assert r["total"] == 1 and r["rows"][0]["审批编号"] == "A003"

    def test_keyword_no_hit_empty_not_error(self, fake_batch):
        r = fake_batch.records("Part3", keyword="绝不存在的关键字XYZ")
        assert r["total"] == 0 and r["rows"] == []

    def test_keyword_hit_across_columns(self, fake_batch):
        r = fake_batch.records("Part3", keyword="本地仓到货检查")
        assert r["total"] == 1  # 命中 A001 的审批记录

    def test_sort_by_time_desc(self, fake_batch):
        r = fake_batch.records("Part3", sort_field="创建时间", sort_order="desc")
        times = [x["创建时间"] for x in r["rows"]]
        assert times == sorted(times, reverse=True)

    def test_sort_asc_with_nat_last(self, fake_batch):
        """升序排序 NaT 应排在最后"""
        r = fake_batch.records("Part3", sort_field="完成时间", sort_order="asc")
        vals = [x["完成时间"] for x in r["rows"]]
        not_null = [v for v in vals if v]
        assert not_null == sorted(not_null)
        # NaT/None 行在末尾
        assert all(v is None for v in vals[len(not_null):])

    def test_sort_unknown_field_falls_back(self, fake_batch):
        """未知排序字段：回退原顺序，不抛异常"""
        r = fake_batch.records("Part3", sort_field="不存在列")
        assert [x["审批编号"] for x in r["rows"]] == ["A001", "A002", "A003",
                                                     "A004", "A005"]

    def test_invalid_part_raises_keyerror(self, fake_batch):
        with pytest.raises(KeyError):
            fake_batch.records("Part99")

    def test_nodes_expanded(self, fake_batch):
        """每行附 _nodes 审批节点明细"""
        r = fake_batch.records("Part3")
        row1 = next(x for x in r["rows"] if x["审批编号"] == "A001")
        assert len(row1["_nodes"]) == 2
        assert row1["_nodes"][0]["operator"] == "张三"
        assert row1["_nodes"][0]["action"] == "提交申请"
        # 空审批记录 → _nodes 为空列表
        row3 = next(x for x in r["rows"] if x["审批编号"] == "A003")
        assert row3["_nodes"] == []

    def test_records_filtered_same_semantics(self, fake_batch):
        df = fake_batch.records_filtered("Part3", status="审批中")
        assert len(df) == 2
        df = fake_batch.records_filtered("Part3", keyword="绝不存在的关键字XYZ")
        assert len(df) == 0
        with pytest.raises(KeyError):
            fake_batch.records_filtered("Part99")
