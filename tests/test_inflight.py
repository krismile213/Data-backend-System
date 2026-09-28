# -*- coding: utf-8 -*-
"""test_inflight.py — 事中引擎（段状态机 / 快照 diff / 日报 / 上报清单）。

三层：
  1. 纯逻辑单测 —— seg_state 状态机（today 可注入）、快照写入/替换/diff、
     日报 xlsx 生成（openpyxl 读回断言）。不碰 DB，任何环境必跑。
  2. 集成测 —— compute_inflight / compute_alerts_today 依赖真实批次数据：
     未导入时 skip（只读，绝不重建批次）。

口径锚点（docs/system_design.md §3.1）：
  target=10、ratio=0.85 → elapsed 5=进行中 / 9=预警 / 11=超期；
  终点已有时间的段 = 已完成，**不进预警区**。
"""

import io
import os
import sys
from datetime import date, datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import inflight_service as svc                     # noqa: E402
from app.reporting import daily_report as _daily_xlsx       # noqa: E402

TODAY = date(2026, 9, 22)


def _row(**kw):
    """构造一个最小段行（review_sku_segments 产出的子集）。"""
    base = {"dept": "运营", "seg": "取数→签样", "from": "取数", "to": "签样",
            "target": 10, "t0": None, "t1": None, "judge": "—缺时间",
            "gap": False, "noShip": False, "assessed": True,
            "owner": "张三", "executor": "李四"}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# seg_state 状态机
# ---------------------------------------------------------------------------
def _dt(s):
    return datetime.strptime(s, "%Y-%m-%d")


def test_seg_running_below_warn_line():
    """elapsed=5 ≤ 10×0.85=8.5 → 进行中。"""
    r = _row(t0=_dt("2026-09-17"))
    st = svc.seg_state(r, today=TODAY, ratio=0.85)
    assert st["state"] == svc.ST_RUNNING
    assert st["elapsed"] == 5
    assert st["warnLine"] == 8.5
    assert st["over"] is None


def test_seg_warn_in_85_band():
    """elapsed=9 ∈ (8.5, 10] → 预警（P1-2 核心带）。"""
    r = _row(t0=_dt("2026-09-13"))
    st = svc.seg_state(r, today=TODAY, ratio=0.85)
    assert st["state"] == svc.ST_WARN
    assert st["elapsed"] == 9
    assert st["over"] is None


def test_seg_over():
    """elapsed=11 > 10 → 超期，over=1。"""
    r = _row(t0=_dt("2026-09-11"))
    st = svc.seg_state(r, today=TODAY, ratio=0.85)
    assert st["state"] == svc.ST_OVER
    assert st["over"] == 1


def test_seg_done_never_warns():
    """终点已有时间 → 已完成；即使耗时远超目标也不进预警/超期（不误报）。"""
    r = _row(t0=_dt("2026-06-01"), t1=_dt("2026-09-01"), judge="✗超92天")
    st = svc.seg_state(r, today=TODAY, ratio=0.85)
    assert st["state"] == svc.ST_DONE
    assert st["elapsed"] is None and st["over"] is None


def test_seg_pending_and_gap():
    """起点无时间 → 未开始；gap 段 → 留空（不预警）。"""
    assert svc.seg_state(_row(), today=TODAY)["state"] == svc.ST_PENDING
    assert svc.seg_state(_row(gap=True, target=None), today=TODAY)["state"] \
        == svc.ST_GAP


def test_seg_noship_counts_elapsed():
    """渠道未发货占位行（noShip）：t1 是"今天"占位 → 按进行中口径累计。"""
    r = _row(t0=_dt("2026-06-01"), t1=TODAY, noShip=True, target=7)
    st = svc.seg_state(r, today=TODAY, ratio=0.85)
    assert st["state"] == svc.ST_OVER
    assert st["elapsed"] == 113 and st["over"] == 106


def test_seg_unassessed_no_threshold():
    """未考核段（assessed=False / target=None）：只报进度，不判阈值。"""
    r = _row(t0=_dt("2026-01-01"), target=None, assessed=False)
    st = svc.seg_state(r, today=TODAY, ratio=0.85)
    assert st["state"] == svc.ST_RUNNING and st["warnLine"] is None


# ---------------------------------------------------------------------------
# 快照：写入 / 同日替换 / 逐日 diff（临时 state 文件，不碰真实数据）
# ---------------------------------------------------------------------------
@pytest.fixture()
def snap_file(tmp_path, monkeypatch):
    f = tmp_path / "inflight_snapshots.json"
    monkeypatch.setattr(svc, "_SNAP_FILE", f)
    return f


def test_snapshot_dedup_and_replace(snap_file):
    """同指纹同日去重；同日异指纹 → 替换当日张（每日每品类只留最新）。"""
    a = svc.record_inflight_snapshot("手机膜", "fp1", nodes_map={"G1": {"取数": "2026-09-01 10:00:00"}})
    b = svc.record_inflight_snapshot("手机膜", "fp1", nodes_map={"G1": {"取数": "2026-09-01 10:00:00"}})
    # 每次调用都从磁盘重载 → 对象身份不同，但内容必须一致且只有一张
    assert a == b and len(svc._load_snaps()) == 1
    svc.record_inflight_snapshot("手机膜", "fp2", nodes_map={"G1": {"签样": "2026-09-02 09:00:00"}})
    snaps = svc._load_snaps()
    assert len(snaps) == 1 and snaps[0]["fingerprint"] == "fp2"
    # 品类隔离：手机壳的快照不顶掉手机膜的
    svc.record_inflight_snapshot("手机壳", "fp-shell", nodes_map={"K1": {"提案": "2026-09-03 09:00:00"}})
    assert len(svc._load_snaps()) == 2


def test_progress_diff(snap_file):
    """newNodes = 当日有值 ∧ 前一日无值（P1-1 diff 口径）。"""
    svc.record_inflight_snapshot("手机膜", "fp1",
                                 nodes_map={"G1": {"取数": "2026-09-01 10:00:00"}})
    # 同日替换导致上面只剩一张 —— 手工追加历史日期张，直接写文件
    snaps = svc._load_snaps()
    snaps[0]["date"] = "2026-09-01"
    snaps.append({"date": "2026-09-02", "at": "09:00", "fingerprint": "fp1",
                  "category": "手机膜",
                  "nodes": {"G1": {"取数": "2026-09-01 10:00:00",
                                   "签样": "2026-09-02 09:00:00"}}})
    svc._save_snaps(snaps)

    out = svc.progress_for("手机膜", sku="G1", days=7)
    by_date = {p["date"]: p for p in out}
    assert by_date["2026-09-01"]["newNodes"] == [
        {"sku": "G1", "node": "取数", "at": "2026-09-01 10:00:00"}]
    # 次日：取数已有 → 不算新；签样新出现 → 算新
    assert [(n["node"]) for n in by_date["2026-09-02"]["newNodes"]] == ["签样"]


def test_prev_day_nodes_lookup(snap_file):
    """_prev_day_nodes 取「< 今天的最近一张」同品类快照。"""
    snaps = [
        {"date": "2026-09-01", "at": "09:00", "fingerprint": "f0",
         "category": "手机膜", "nodes": {"G1": {"取数": "2026-09-01 10:00:00"}}},
        {"date": "2026-09-02", "at": "09:00", "fingerprint": "f1",
         "category": "手机膜", "nodes": {"G1": {"取数": "2026-09-01 10:00:00",
                                                "签样": "2026-09-02 09:00:00"}}},
    ]
    svc._save_snaps(snaps)
    prev = svc._prev_day_nodes("手机膜", today_str="2026-09-03")
    assert prev["G1"]["签样"] == "2026-09-02 09:00:00"
    assert svc._prev_day_nodes("手机壳", today_str="2026-09-03") == {}


# ---------------------------------------------------------------------------
# 日报 xlsx（P0-4：5 sheets）
# ---------------------------------------------------------------------------
def test_daily_report_sheets():
    alerts = {
        "todayNewOverdue": [{"sku": "G1", "dept": "运营", "seg": "取数→签样",
                             "overDays": 2, "elapsed": 12, "target": 10,
                             "owner": "李四"}],
        "ongoingOverdue": [{"sku": "G2", "dept": "后勤", "seg": "采购→发货末批",
                            "overDays": 30, "elapsed": 40, "target": 10,
                            "owner": "王五"}],
        "warnings": [{"sku": "G3", "dept": "产品", "seg": "提案→取数",
                      "elapsed": 9, "target": 10, "ratio": 0.85,
                      "owner": "赵六"}],
    }
    items = [{"sku": "G1", "model": "M1", "status": "超期",
              "worstSeg": "取数→签样", "worstSegDept": "运营",
              "worstElapsed": 12, "worstTarget": 10, "worstOverDays": 2,
              "warnSegs": [], "multiRound": False,
              "rootCause": {"type": "搁置", "seg": "取数→签样"}, "owner": "李四"}]
    progress = [{"date": "2026-09-22",
                 "newNodes": [{"sku": "G1", "node": "签样",
                               "at": "2026-09-22 09:00:00"}]}]
    content = _daily_xlsx(category="手机膜", computed_at="2026-09-22 17:00",
                          date_str="2026-09-22", items=items, alerts=alerts,
                          progress=progress)
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(content))
    assert wb.sheetnames == ["在研SKU状态总表", "超期明细", "风险预警",
                             "今日进度变化", "异常上报清单"]
    ws1, ws5 = wb["在研SKU状态总表"], wb["异常上报清单"]
    assert ws1.cell(row=4, column=1).value == "G1"
    assert ws1.cell(row=4, column=3).value == "超期"
    assert ws1.cell(row=4, column=8).value == 2
    # 上报清单：先当日新超期后持续超期，类型列可区分
    assert ws5.cell(row=4, column=1).value == "当日新超期"
    assert ws5.cell(row=4, column=2).value == "G1"
    assert ws5.cell(row=5, column=1).value == "持续超期"
    assert ws5.cell(row=5, column=2).value == "G2"


def test_daily_report_empty():
    """全空输入也要能出文件（各 sheet 有占位行，不抛异常）。"""
    content = _daily_xlsx(category="手机壳", computed_at="x", date_str="d",
                          items=[], alerts={"todayNewOverdue": [],
                                            "ongoingOverdue": [],
                                            "warnings": []},
                          progress=[])
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(content))
    assert len(wb.sheetnames) == 5


def test_report_prefix_is_daily_not_review():
    """日报前缀必须是「事中日报_」。

    旧名「复盘报告_」与单 SKU 的「复盘报告 md」撞名，而文件内容其实是事中日报
    （5 sheets）——改名后这里钉死前缀，避免回退。
    """
    assert svc._REPORT_PREFIX == "事中日报_"
    assert svc._LEGACY_REPORT_PREFIX == "复盘报告_"


def test_provisional_warning_lands_on_sheet1():
    """provisional=True（手机壳）时，日报 Sheet1 第 2 行必须打口径警示。

    日报是要拿去催办/上报的——目标天尚未业务校准时，必须写在脸上。
    """
    from openpyxl import load_workbook

    empty_alerts = {"todayNewOverdue": [], "ongoingOverdue": [], "warnings": []}
    content = _daily_xlsx(category="手机壳", computed_at="x", date_str="d",
                          items=[], alerts=empty_alerts, progress=[],
                          provisional=True,
                          targets_source="继承手机膜口径（待业务校准）")
    a2 = load_workbook(io.BytesIO(content))["在研SKU状态总表"]["A2"].value or ""
    assert "待校准" in a2 and "继承手机膜口径" in a2

    # 手机膜（provisional 缺省 False）不得出现该行，否则是真误报
    plain = _daily_xlsx(category="手机膜", computed_at="x", date_str="d",
                        items=[], alerts=empty_alerts, progress=[])
    assert load_workbook(io.BytesIO(plain))["在研SKU状态总表"]["A2"].value is None


def test_legacy_report_file_is_swept(tmp_path, monkeypatch):
    """改名后，同一天的旧名文件必须被随迁删除，否则成了无人引用的孤儿
    （既不在索引里、保留期清理看不到它，也没有任何下载入口）。"""
    from app import config as cfg

    monkeypatch.setattr(cfg, "DATA_ROOTS", {"手机膜": tmp_path}, raising=False)
    old = tmp_path / "复盘报告_20260924.xlsx"
    old.write_bytes(b"old")
    keep = tmp_path / "复盘报告_20260923.xlsx"      # 不是同一天 → 不能删
    keep.write_bytes(b"keep")

    assert svc._drop_legacy_report(tmp_path, "20260924") == str(old)
    assert not old.exists()
    assert keep.exists()


def test_legacy_sweep_refuses_outside_data_root(tmp_path, monkeypatch):
    """不在数据根之下的同名文件绝不能被删（防索引/路径被改坏后误删）。"""
    from app import config as cfg

    monkeypatch.setattr(cfg, "DATA_ROOTS", {"手机膜": tmp_path / "elsewhere"},
                        raising=False)
    victim = tmp_path / "复盘报告_20260924.xlsx"
    victim.write_bytes(b"x")
    assert svc._drop_legacy_report(tmp_path, "20260924") is None
    assert victim.exists()


# ---------------------------------------------------------------------------
# 集成（依赖真实批次数据；只读，未导入即 skip）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def inflight_data():
    try:
        data = svc.compute_inflight(None)
    except Exception as e:                       # pragma: no cover
        pytest.skip(f"事中计算不可用（多半是 DB/数据未就绪）: {e}")
    if not data["items"] and data["inResearchCount"] == 0:
        pytest.skip("无在研 SKU（数据未导入或全部已完结）")
    return data


def test_inflight_structure(inflight_data):
    """关键字段齐全：KPI 计数与 items 一致。"""
    d = inflight_data
    assert d["inResearchCount"] == len(d["items"])
    assert d["overdueSkuCount"] == sum(1 for x in d["items"]
                                       if x["status"] == svc.ST_OVER)
    assert d["warnCount"] == sum(1 for x in d["items"]
                                 if x["status"] == svc.ST_WARN)
    assert d["multiRoundCount"] == sum(1 for x in d["items"]
                                       if x["multiRound"])
    for x in d["items"]:
        assert x["sku"] and x["status"] in (svc.ST_RUNNING, svc.ST_WARN,
                                            svc.ST_OVER)
        if x["status"] == svc.ST_OVER:
            assert x["worstOverDays"] and x["worstOverDays"] > 0


def test_inflight_g506_still_in_research(inflight_data, review_g506_done):
    """验收锚点（设计 §7 T03）：/api/inflight/sku/G506 必须可查。

    G506 三部门全完成（复盘有效），但 Part8 有 10 张、Part9 有 2 张单据
    仍挂「审批中」→ 按设计口径「三部门未全完成 或 任一 Part 审批中」
    它属于**在研**：文书未收尾恰恰是事中要催的事。
    """
    if not review_g506_done:
        pytest.skip("G506 未导入或未完结，锚点不适用")
    assert "G506" in {x["sku"] for x in inflight_data["items"]}
    det = svc.get_inflight_data(None)["skuDetails"].get("G506")
    assert det is not None
    assert det["partStat"]["Part8"]["running"] >= 1 \
        or det["partStat"]["Part9"]["running"] >= 1


@pytest.fixture(scope="module")
def review_g506_done():
    """G506 是否已完结（三部门全完成）。查不到 → False（断言自动不生效）。"""
    try:
        from app.routers.review import compute_review
        det = compute_review()["skuDetails"].get("G506")
        return bool(det and det.get("valid"))
    except Exception:
        return False


def test_alerts_today_shape(inflight_data):
    """上报清单结构：新超期 + 持续超期 + 预警，字段齐且超期数为正。"""
    alerts = svc.compute_alerts_today(None, data=inflight_data)
    for key in ("todayNewOverdue", "ongoingOverdue", "warnings"):
        assert isinstance(alerts[key], list)
    for r in alerts["todayNewOverdue"] + alerts["ongoingOverdue"]:
        assert r["overDays"] and r["overDays"] > 0 and r["sku"] and r["seg"]
    # 新超期与持续超期互斥：同一 (sku, seg) 不得同时出现在两边
    new_keys = {(r["sku"], r["seg"]) for r in alerts["todayNewOverdue"]}
    ong_keys = {(r["sku"], r["seg"]) for r in alerts["ongoingOverdue"]}
    assert not (new_keys & ong_keys)
