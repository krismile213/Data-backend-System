# -*- coding: utf-8 -*-
"""test_review_sample_cycle.py — 样机流转「签样→拍摄交付」与节点双时间契约

背景（2026-11-06 用户口径）：
  · 寄送/素材这些节点常年只有 audit（钉钉补录上报时间），没有系统业务日期。
    旧口径直接拿 audit 当实际，会出现 **负段** —— G600 实测：
    寄送 audit 2026-11-05 17:11、拍摄交付系统列 2026-10-22 → 「寄送→拍摄交付」= -14 天。
  · 因此新增两条契约：
    ① `/api/review/sku/{code}` 的 nodes 必须**同时**带 sys / audit / 动作三种原始时间，
       让「系统空、实际是 audit」看得见，而不是被一个 adopted 值糊住；
    ② 「签样→拍摄交付」按**系统业务日期**算间隔，< SAMPLE_CYCLE["limit"] 为合格，
       任一端缺失 → 空白（days=None），**不拿 audit 补录时间顶替**。
"""

from datetime import datetime

import pytest

from app.category_rules import rules_for
from app.routers.review import _sample_cycle
from app.seg_rules import SAMPLE_CYCLE


# ---------------------------------------------------------------------------
# 真源一致性：合格线必须等于两段目标之和（改目标天就必须复核这条线）
# ---------------------------------------------------------------------------
def test_sample_cycle_limit_equals_segment_targets():
    """15 天 = 签样→寄送(5) + 寄送→拍摄交付(10)，两个品类都得对上。

    这条断言的作用不是"测数字"，而是**防止真源分叉**：将来谁改了 SEGS 里的目标天
    却没复核合格线，这里立刻红。手机壳继承手机膜的目标天（PROVISIONAL），所以也适用。
    """
    for cat in ("手机膜", "手机壳"):
        rs = rules_for(cat)
        targets = {row[1]: row[4] for row in rs.segs}
        total = sum(targets.get(s) or 0 for s in SAMPLE_CYCLE["segments"])
        assert total == SAMPLE_CYCLE["limit"], (
            f"{cat} 两段目标之和 {total} ≠ 合格线 {SAMPLE_CYCLE['limit']}")


# ---------------------------------------------------------------------------
# 间隔计算：只认系统业务日期
# ---------------------------------------------------------------------------
def _nodes(sys_a=None, sys_b=None, audit_a=None, audit_b=None):
    return {
        "签样": {"sys": sys_a, "audit": audit_a},
        "拍摄交付": {"sys": sys_b, "audit": audit_b},
    }


def test_sample_cycle_counts_business_dates():
    """G600 复现：签样 10-12、拍摄交付 10-22 → 10 天，合格（<15）。"""
    cyc = _sample_cycle(_nodes(datetime(2026, 10, 12), datetime(2026, 10, 22)))
    assert cyc["days"] == 10
    assert cyc["ok"] is True
    assert cyc["limit"] == SAMPLE_CYCLE["limit"]
    assert cyc["start"] == "2026-10-12" and cyc["end"] == "2026-10-22"
    assert cyc["missing"] == []


def test_sample_cycle_is_blank_when_business_date_missing():
    """任一端没有系统业务日期 → 空白，不拿 audit 顶（补录会把间隔算成负数）。"""
    cyc = _sample_cycle(_nodes(
        datetime(2026, 10, 12), None,
        audit_a=datetime(2026, 10, 13), audit_b=datetime(2026, 11, 5)))
    assert cyc["days"] is None and cyc["ok"] is None
    assert cyc["missing"] == ["拍摄交付"]
    assert cyc["start"] == "2026-10-12" and cyc["end"] is None


def test_sample_cycle_over_limit_fails():
    """超线：签样 10-01、拍摄交付 10-20 → 19 天 → 不合格。"""
    cyc = _sample_cycle(_nodes(datetime(2026, 10, 1), datetime(2026, 10, 20)))
    assert cyc["days"] == 19
    assert cyc["ok"] is False


def test_sample_cycle_boundary_is_exclusive():
    """合格线是**严格小于**：正好 15 天算超线（用户口径「＜15 天就是合格」）。"""
    cyc = _sample_cycle(_nodes(datetime(2026, 10, 1), datetime(2026, 10, 16)))
    assert cyc["days"] == SAMPLE_CYCLE["limit"]
    assert cyc["ok"] is False


# ---------------------------------------------------------------------------
# 接口契约：双时间必须在 payload 里（回归 G600「寄送被 audit 顶替」看不见）
# ---------------------------------------------------------------------------
def test_sku_payload_exposes_system_and_audit_times():
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    r = c.get("/api/review/sku/G600", params={"category": "手机膜"})
    if r.status_code == 404:
        pytest.skip("当前库无 G600（数据批次差异）")
    j = r.json()

    for name, nd in j["nodes"].items():
        for k in ("sys", "audit", "action", "at", "src", "sysField"):
            assert k in nd, f"{name} 缺字段 {k}"

    # G600 的寄送：表单没有「寄送拍摄团队日期」列 → sys 必须为 None，
    # audit 有值。这正是旧实现里「实际=audit」的现场，必须能看出来。
    send = j["nodes"]["寄送"]
    assert send["sys"] is None
    assert send["audit"] is not None

    cyc = j["sampleCycle"]
    assert cyc["from"] == "签样" and cyc["to"] == "拍摄交付"
    assert cyc["days"] == 10 and cyc["ok"] is True


# ---------------------------------------------------------------------------
# 单点实现：复盘与日报必须调同一个函数（2026-11-06 收口）
# ---------------------------------------------------------------------------
def test_single_entry_point_shared_by_review_and_report():
    """复盘用的 `_sample_cycle` 必须**就是** app.sample_cycle.compute。

    为什么钉这条：本功能第一版在 review 里算一遍、日报另算一遍，改 15 天线
    只生效一处（和 09:20/14:50、warnCount 一样的病）。这里用对象同一性断言，
    谁再复制一份实现立刻红。
    """
    from app import sample_cycle
    from app.routers import review

    assert review._sample_cycle is sample_cycle.compute


# ---------------------------------------------------------------------------
# 展示分档：「流程没到」≠「单据建了没填」——不能都打成待补
# ---------------------------------------------------------------------------
def test_label_separates_not_delivered_from_missing_data():
    from app.sample_cycle import label

    ok = label({"limit": 15, "days": 10, "ok": True,
                "missing": [], "recordedMissing": []})
    assert ok["judge"] == "✓合格" and ok["tone"] == "ok"

    bad = label({"limit": 15, "days": 19, "ok": False,
                 "missing": [], "recordedMissing": []})
    assert bad["judge"].startswith("✗超线") and bad["tone"] == "bad"

    # 正好 15 天：不合格，但不能写成「超线0天」（读起来像没超）
    edge = label({"limit": 15, "days": 15, "ok": False,
                  "missing": [], "recordedMissing": []})
    assert edge["judge"] == "✗超线"

    # 拍摄交付还没发生 → 未交付（灰），不是数据问题
    pending = label({"limit": 15, "days": None, "ok": None, "from": "签样",
                     "to": "拍摄交付", "missing": ["拍摄交付"],
                     "recordedMissing": []})
    assert pending["judge"] == "未交付" and pending["tone"] == "muted"

    # 单据已建、只有 audit 补录、系统列空 → 待补（红），该催填表
    need = label({"limit": 15, "days": None, "ok": None, "from": "签样",
                  "to": "拍摄交付", "missing": ["拍摄交付"],
                  "recordedMissing": ["拍摄交付"]})
    assert need["judge"] == "待补" and need["tone"] == "bad"

    # 签样都没做 → 未签样
    nosign = label({"limit": 15, "days": None, "ok": None, "from": "签样",
                    "to": "拍摄交付", "missing": ["签样", "拍摄交付"],
                    "recordedMissing": []})
    assert nosign["judge"] == "未签样"


def test_compute_marks_recorded_but_missing_system_date():
    """有 audit 却无 sys 的节点要落进 recordedMissing（供上面那档判定用）。"""
    cyc = _sample_cycle({
        "签样": {"sys": datetime(2026, 10, 12)},
        "拍摄交付": {"sys": None, "audit": datetime(2026, 11, 5, 17, 33)},
    })
    assert cyc["missing"] == ["拍摄交付"]
    assert cyc["recordedMissing"] == ["拍摄交付"]
    assert cyc["days"] is None


# ---------------------------------------------------------------------------
# 日报：4 列必须真的落到「在研SKU状态总表」，且甘特图不动
# ---------------------------------------------------------------------------
def _report_blob(**kw):
    import io

    from openpyxl import load_workbook

    from app.reporting import daily_report

    empty = {"todayNewOverdue": [], "ongoingOverdue": [], "warnings": []}
    item = {"sku": "G600", "model": "M1", "status": "进行中",
            "staySeg": "采购→本地仓到货", "staySegDept": "后勤",
            "stayElapsed": 3, "stayTarget": 8, "stayDaysLeft": 5,
            "warnSegs": [], "overSegs": [], "multiRound": False,
            "rootCause": {}, "owner": "夏富富"}
    blob = daily_report(category="手机膜", computed_at="x", date_str="2026-11-06",
                        items=[item], alerts=empty, gantts=[], **kw)
    return load_workbook(io.BytesIO(blob))


def test_daily_report_appends_sample_cycle_columns():
    wb = _report_blob(sample_cycles={
        "G600": {"from": "签样", "to": "拍摄交付", "limit": 15,
                 "start": "2026-10-12", "end": "2026-10-22",
                 "days": 10, "ok": True, "missing": [], "recordedMissing": []}})
    ws = wb["在研SKU状态总表"]
    heads = [ws.cell(row=4, column=c).value for c in range(16, 20)]
    assert heads == ["签样(系统)", "拍摄交付(系统)", "样机流转(天)", "流转判定"]
    assert ws.cell(row=5, column=16).value == "2026-10-12"
    assert ws.cell(row=5, column=17).value == "2026-10-22"
    assert ws.cell(row=5, column=18).value == "10天"
    assert ws.cell(row=5, column=19).value == "✓合格"
    # 甘特图列数/结构不受影响：仍只有「部门 + 每段一列」那套，没有被塞进样机列
    assert "在研SKU甘特图" in wb.sheetnames


def test_daily_report_without_sample_cycles_keeps_old_width():
    """不传 sample_cycles → 表宽仍是 15 列（老调用方行为不变）。"""
    ws = _report_blob()["在研SKU状态总表"]
    assert ws.cell(row=4, column=15).value == "素材"
    assert ws.cell(row=4, column=16).value is None


def test_daily_report_leaves_dates_blank_when_not_delivered():
    """用户口径「实际没有就写空」：缺系统日期时两个日期格必须为空，判定写未交付。"""
    wb = _report_blob(sample_cycles={
        "G600": {"from": "签样", "to": "拍摄交付", "limit": 15,
                 "start": "2026-10-28", "end": None,
                 "days": None, "ok": None, "missing": ["拍摄交付"],
                 "recordedMissing": []}})
    ws = wb["在研SKU状态总表"]
    assert ws.cell(row=5, column=16).value == "2026-10-28"
    assert not ws.cell(row=5, column=17).value, "拍摄交付没有系统日期就该留空"
    assert not ws.cell(row=5, column=18).value, "间隔算不出来就留空"
    assert ws.cell(row=5, column=19).value == "未交付"
