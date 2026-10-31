# -*- coding: utf-8 -*-
"""test_causal.py — 核心原因「漏报嫌疑 / 搁置」判据回归（P0-5）。

背景（2026-10-19）：用户看 1019 事中表，G599/G600/G601 三支的核心原因都被标成
「漏报嫌疑(取数→签样)」。核对原始 Part 表：取数 2026-09-28 → 签样 2026-10-12，
而 **Part2 单据 2026-09-29 就建了**（正落在窗口内），只是状态为「已结束」。

旧实现 `mid` 用的是 `cts`（已排除 status=已结束 的单据），于是"窗口内有没有下游
动作"看起来是空的 → 一律判漏报。但 `cts` 的排除是为 **down_start（启动信号）**
服务的：已结束单据的建单时间会把 170 天停滞压成 0 天（G506 教训）。两件事混用
一份数据，就把"活接了但拖着"误判成"没人上报"。

修法：`mid` 改看 **全部** 单据（all_cts），按日历日严格开区间
（t0.date() < 建单日 < t1.date()）—— 建单日 == 下游完成日 视为"这次完成本身"，
因此 G506 的 Part2 单（与采购同日 2026-04-30）仍判漏报嫌疑，基准不被改坏。
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import causal  # noqa: E402

T0 = datetime(2026, 9, 28)     # 取数
T1 = datetime(2026, 10, 12)    # 签样


def _seg(days=14, target=12):
    """取数→签样 一段（done：t1 有值，days=14 > 目标 12 → 进根因候选）。"""
    return [{"seg": "取数→签样", "dept": "产品", "from": "取数", "to": "签样",
             "t0": T0, "t1": T1, "days": days, "target": target,
             "executor": "李杰华"}]


_NODES = {"取数": {"adopted": T0}, "签样": {"adopted": T1}}


def _rc(part2_docs):
    return causal.locate(_seg(), _NODES, {"Part2": part2_docs}, min_days=7)


def test_finished_downstream_doc_counts_as_started():
    """下游单据在窗口内已建（哪怕已结束）→ 判「搁置」，不得报漏报嫌疑。

    G599/G600/G601 实物：Part2 单 2026-09-29 建，落在 取数09-28 ~ 签样10-12 窗口内。
    """
    rc = _rc([{"create_time": datetime(2026, 9, 29, 16, 43),
               "status": "已结束"}])
    assert rc["type"] == "搁置"
    assert rc["suspect"] is False
    assert "2026-09-29" in rc["evidence"]
    assert "期间无任何单据" not in rc["evidence"]


def test_no_downstream_doc_is_omission():
    """窗口内真的一张单据都没有 → 漏报嫌疑（G506 形态）。"""
    rc = _rc([])
    assert rc["type"] == "漏报嫌疑"
    assert rc["suspect"] is True
    assert rc["waitDays"] == 14


def test_same_day_creation_is_not_in_window():
    """建单日 == 下游完成日 → 视为"这次完成本身"，不进窗口，仍判漏报嫌疑。

    G506 基准：签样 2025-11-11 → 采购 2026-04-30，Part2 单也是 2026-04-30 建。
    """
    rc = _rc([{"create_time": datetime(2026, 10, 12, 15, 0),
               "status": "已结束"}])
    assert rc["type"] == "漏报嫌疑"
    assert rc["suspect"] is True


def test_old_doc_does_not_flatten_wait():
    """建单早于上游完成的历史单：不压 wait，也不误判成"接了活"。"""
    rc = _rc([{"create_time": datetime(2026, 9, 1),
               "status": "已结束"}])
    assert rc["type"] == "漏报嫌疑"
    assert rc["waitDays"] == 14      # 仍按 签样日 计，不是 0


def test_in_flight_doc_still_uses_create_time_for_wait():
    """仍在进行的单据：wait 用建单时间（启动信号），不受"全部单据"改动影响。"""
    rc = _rc([{"create_time": datetime(2026, 10, 1),
               "status": "审批中"}])
    assert rc["type"] == "搁置"
    assert rc["waitDays"] == 3       # 10-01 − 09-28
