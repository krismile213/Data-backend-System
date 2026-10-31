# -*- coding: utf-8 -*-
"""test_product_rules.py — 商品资料档位规则解析回归。

口径权威来源：`资产文件\\商品资料.xlsx` 的 `商品资料` 表 A1 单元格说明。
G506 的评级 XC → 日均 1.38~5.5 是损益推算的输入，错一位数就会污染金额区间，
所以这里把 A1 原文的解析结果、A~E 与 XA~XE 的交叉一致性全部固化成断言。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.product_rules import (GRADE_RANGES, parse_grade_ranges,  # noqa: E402
                               range_of)

# 商品资料 A1 原文（2026-09-22 抄录）
A1 = ("A:1个月销量500套及以上。B:3个月销量500套及以上。C:6个月销量250套及以上。"
      "D:6个月销量200-250套。E:6个月销量200套以下的！X类为新品:"
      "XA：sku日均≥16.7,XB：16.7＞sku日均≥5.5,XC：5.5＞sku日均≥1.38,"
      "XD：1.38＞sku日均≥1.11,XE：1.11＞sku日均,X：暂无销售数据的。"
      "评级为E或者XE,不要采购。停售标准：钢化膜、水凝膜膜产品连续6个月评级为E级"
      "更新产品状态为停售，平板钢化膜一年总销售低于250套更新产品为停售。"
      "手机壳产品不参与以上产品评级规则！")

REL = 0.02


def _close(a, b):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) / max(abs(a), abs(b), 1e-9) <= REL


def test_xc_range_is_authoritative():
    """G506 的档位 XC 必须解析为 1.38~5.5（损益推算的输入）。"""
    assert range_of("XC") == (1.38, 5.5)
    assert range_of("xc") == (1.38, 5.5)      # 大小写不敏感


def test_unmatched_grade_degrades():
    """未命中档位返回 (None, None)，由调用方降级，不得瞎猜。"""
    assert range_of("ZZ") == (None, None)
    assert range_of(None) == (None, None)
    assert range_of("") == (None, None)


def test_parse_a1_produces_full_table():
    """A1 原文能解析出全部 11 个档位，且与内置兜底表一致。"""
    ranges, notes = parse_grade_ranges(A1)
    for g in ["A", "B", "C", "D", "E", "XA", "XB", "XC", "XD", "XE", "X"]:
        assert g in ranges, f"档位 {g} 未解析出来"
        lo1, hi1 = ranges[g]
        lo2, hi2 = GRADE_RANGES[g]
        assert _close(lo1, lo2) and _close(hi1, hi2), f"{g}: {ranges[g]} != {GRADE_RANGES[g]}"
    # 交叉校验不允许有 ⚠
    assert not [n for n in notes if "⚠" in n], notes


@pytest.mark.parametrize("grade,lo,hi", [
    ("A", 16.6667, None), ("B", 5.5556, 16.6667), ("C", 1.3889, 5.5556),
    ("D", 1.1111, 1.3889), ("E", 0.0, 1.1111),
    ("XA", 16.7, None), ("XB", 5.5, 16.7), ("XD", 1.11, 1.38),
    ("XE", 0.0, 1.11), ("X", 0.0, 0.0),
])
def test_letter_and_x_bands_aligned(grade, lo, hi):
    """A~E 套数换算与 XA~XE 原文日均必须逐档重合（口径自洽性）。"""
    r, _ = parse_grade_ranges(A1)
    got_lo, got_hi = r[grade]
    assert _close(got_lo, lo), f"{grade}.min={got_lo} 期望≈{lo}"
    assert _close(got_hi, hi), f"{grade}.max={got_hi} 期望≈{hi}"


def test_letter_open_upper_filled_by_next_band():
    """A~E 原文只给下界，上界须补齐为相邻更高档的下界（否则 B/C 算不出亏损上限）。"""
    r, notes = parse_grade_ranges(A1)
    assert r["B"][1] is not None and _close(r["B"][1], r["A"][0])
    assert r["C"][1] is not None and _close(r["C"][1], r["B"][0])
    assert r["D"][1] is not None and _close(r["D"][1], r["C"][0])
    assert r["A"][1] is None, "A 档上界本就开放，不应被补齐"
    assert any("开放上界" in n for n in notes), notes


def test_grade_range_min_le_max():
    """不变量：有界档位必须 min <= max（防止解析方向颠倒）。"""
    r, _ = parse_grade_ranges(A1)
    for g, (lo, hi) in r.items():
        if lo is not None and hi is not None:
            assert lo <= hi, f"{g}: {lo} > {hi}"


def test_missing_a1_falls_back():
    """A1 缺失时回退内置表，不抛异常。"""
    r, notes = parse_grade_ranges(None)
    assert r == GRADE_RANGES
    assert any("内置" in n for n in notes)
