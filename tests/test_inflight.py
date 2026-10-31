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
# 当前停留判定（2026-10-06：总表「最差环节」改「当前停留」+ 距超时天数）
# ---------------------------------------------------------------------------
def test_pick_stay_latest_started_unfinished():
    """进行中：最近启动未完成的段 = 当前停留；距超时 = 目标 − 已停留（负=已超期）。"""
    rows = [
        _row(seg="提案→取数", dept="产品",
             t0=datetime(2026, 8, 1), t1=datetime(2026, 8, 20), target=75),
        _row(seg="取数→签样", dept="运营",
             t0=datetime(2026, 9, 1), t1=None, target=10),
    ]
    state_rows = [(r, svc.seg_state(r, today=TODAY)) for r in rows]
    stay, st, dl = svc._pick_stay(state_rows)
    assert stay["seg"] == "取数→签样"
    assert st["state"] == svc.ST_OVER
    # 2026-09-01 → 09-22 已 21 天，目标 10 → 已超 11 天
    assert dl == -11.0


def test_pick_stay_all_pending():
    """全部未开始：停在第一段，已停留 0 天，距超时 = 目标天。"""
    rows = [_row(seg="提案→取数", dept="产品", t0=None, t1=None, target=7)]
    state_rows = [(r, svc.seg_state(r, today=TODAY)) for r in rows]
    stay, st, dl = svc._pick_stay(state_rows)
    assert stay["seg"] == "提案→取数"
    assert st["state"] == svc.ST_PENDING
    assert dl == 7.0


def test_pick_stay_all_done_still_open():
    """全部完成但仍未闭环（复盘口径缺终点）：取最晚完成段，距超时 = None。"""
    rows = [
        _row(seg="A", dept="产品", t0=datetime(2026, 8, 1),
             t1=datetime(2026, 8, 5), target=10),
        _row(seg="B", dept="运营", t0=datetime(2026, 8, 5),
             t1=datetime(2026, 8, 9), target=7),
    ]
    state_rows = [(r, svc.seg_state(r, today=TODAY)) for r in rows]
    stay, _st, dl = svc._pick_stay(state_rows)
    assert stay["seg"] == "B"
    assert dl is None


def test_pick_stay_running_without_target():
    """停留段未设目标天 → 距超时 = None（只报停留，不做到期判定）。"""
    rows = [_row(seg="X", dept="后勤", t0=datetime(2026, 9, 1),
                 t1=None, target=None)]
    state_rows = [(r, svc.seg_state(r, today=TODAY)) for r in rows]
    stay, _st, dl = svc._pick_stay(state_rows)
    assert stay["seg"] == "X"
    assert dl is None


# ---------------------------------------------------------------------------
# 甘特记录（_gantt_records：未完成段延伸至今日 / 未开始段跳过 / neutral 标记）
# ---------------------------------------------------------------------------
def test_gantt_records_extends_unfinished():
    data = {
        "items": [{"sku": "G1", "model": "M1", "status": "超期",
                   "staySeg": "取数→签样", "stayDaysLeft": -11.0}],
        "skuDetails": {"G1": {"segments": [
            {"dept": "产品", "seg": "提案→取数", "t0": "2026-08-01 09:00:00",
             "t1": "2026-08-20 10:00:00", "days": "19.0", "target": 75,
             "state": "已完成", "judge": "✓", "executor": "张三",
             "gap": False, "noShip": False},
            {"dept": "运营", "seg": "取数→签样", "t0": "2026-09-01 09:00:00",
             "t1": None, "days": None, "target": 10, "state": "超期",
             "elapsed": 21, "warnLine": 8.5, "judge": "—缺时间",
             "executor": "李四", "gap": False, "noShip": False},
            {"dept": "__GAP__", "seg": "留空段", "t0": None, "t1": None,
             "days": None, "target": None, "state": "留空", "judge": "—留空",
             "executor": "", "gap": True, "noShip": False},
        ]}},
    }
    recs = svc._gantt_records(data, today=date(2026, 10, 6))
    assert len(recs) == 1
    assert recs[0]["sku"] == "G1"
    rows = recs[0]["rows"]
    assert len(rows) == 2              # 未开始段（t0 空）不画，跳过
    done, running = rows
    assert done["days"] == "19.0" and done["neutral"] is False
    # 未完成段：条形延伸至今日 12 点，判定改写为事中人话（不再是"—缺时间"）
    assert running["t1"] == datetime(2026, 10, 6, 12, 0)
    assert running["judge"].startswith("✗超期(已21天)")
    assert running["days"] == 21


# ---------------------------------------------------------------------------
# 日报 xlsx（P0-4；2026-10-06 起 Sheet4 = 在研SKU甘特图）
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
              "staySeg": "取数→签样", "staySegDept": "运营",
              "stayElapsed": 12, "stayTarget": 10, "stayDaysLeft": -2,
              "warnSegs": [], "multiRound": False, "stayOver": True,
              # 2026-10-19：状态列只认当前停留环节；本列仍统计全部超期段，
              # 用「当前·/已过·」区分超的是现在这段还是已经走过的那段
              "overSegs": [{"seg": "取数→签样", "dept": "运营", "overDays": 2,
                            "isCurrent": True}],
              "rootCause": {"type": "搁置", "seg": "取数→签样"}, "owner": "李四"},
             # G2：当前环节还剩 3 天、超的是已走过的「签样→寄送」→ 状态=预警
             {"sku": "G2", "model": "M2", "status": "预警",
              "staySeg": "采购→本地仓到货", "staySegDept": "后勤",
              "stayElapsed": 5, "stayTarget": 8, "stayDaysLeft": 3,
              "warnSegs": [], "multiRound": False, "stayOver": False,
              "overSegs": [{"seg": "签样→寄送", "dept": "产品", "overDays": 2,
                            "isCurrent": False}],
              "rootCause": {}, "owner": "王五"}]
    content = _daily_xlsx(category="手机膜", computed_at="2026-09-22 17:00",
                          date_str="2026-09-22", items=items, alerts=alerts,
                          gantts=[])
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(content))
    # 2026-10-29：固定 sheet 从 5 张 → 7 张（+本月已消化超期 / 本月绩效视角，
    # 修复"完成即蒸发"并提供按责任人汇总；两张恒产，空数据也有占位说明行）
    assert wb.sheetnames == ["在研SKU状态总表", "超期明细", "风险预警",
                             "在研SKU甘特图", "异常上报清单",
                             "本月已消化超期", "本月绩效视角"]
    ws1, ws4, ws5 = (wb["在研SKU状态总表"], wb["在研SKU甘特图"],
                     wb["异常上报清单"])
    # 2026-10-29 总表加图例行（A2）：表头 3→4、数据行 4→5
    assert ws1.cell(row=5, column=1).value == "G1"
    assert ws1.cell(row=5, column=3).value == "超期"
    # 第 8 列 = 距超时（负 → 「已超N天」人话，不再是裸数字）
    assert ws1.cell(row=5, column=8).value == "已超2天"
    assert "已超" in str(ws1.cell(row=5, column=8).value)
    # 第 9 列 = 超期环节（2026-10-19）：写明"超的是哪一段"，与第 8 列各说各段，
    # 消除「状态=超期」+「距超时=剩N天」同行的观感矛盾
    assert ws1.cell(row=4, column=9).value == "超期环节"
    assert ws1.cell(row=4, column=10).value == "预警段数"
    assert ws1.cell(row=5, column=9).value == "当前·取数→签样 超2天"
    # G2：超的是已走过的那段 → 状态必须是「预警」（当前环节还剩 3 天），
    # 第 9 列用「已过·」标出来，信息不丢但不误标整行超期
    assert ws1.cell(row=6, column=1).value == "G2"
    assert ws1.cell(row=6, column=3).value == "预警"
    assert ws1.cell(row=6, column=8).value == "剩3天"
    assert ws1.cell(row=6, column=9).value == "已过·签样→寄送 超2天"
    # 甘特 sheet：空记录也要有占位（不抛异常）
    assert "甘特" in ws4.title
    # 上报清单：先当日新超期后持续超期，类型列可区分；
    # 2026-10-29 起 overDays ≥30 的持续超期升级「长期未决」（G2 的 30 天正好覆盖）
    assert ws5.cell(row=4, column=1).value == "当日新超期"
    assert ws5.cell(row=4, column=2).value == "G1"
    assert ws5.cell(row=5, column=1).value == "长期未决·≥30天"
    assert ws5.cell(row=5, column=2).value == "G2"


def test_daily_report_empty():
    """全空输入也要能出文件（各 sheet 有占位行，不抛异常）。

    2026-10-29：+2 固定 sheet（本月已消化超期 / 本月绩效视角）→ 7 张。
    """
    content = _daily_xlsx(category="手机壳", computed_at="x", date_str="d",
                          items=[], alerts={"todayNewOverdue": [],
                                            "ongoingOverdue": [],
                                            "warnings": []},
                          gantts=[])
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(content))
    assert len(wb.sheetnames) == 7


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
                          items=[], alerts=empty_alerts, gantts=[],
                          provisional=True,
                          targets_source="继承手机膜口径（待业务校准）")
    a2 = load_workbook(io.BytesIO(content))["在研SKU状态总表"]["A2"].value or ""
    assert "待校准" in a2 and "继承手机膜口径" in a2

    # 手机膜（provisional 缺省 False）不得出现待校准警示（A2 是新增图例行）
    plain = _daily_xlsx(category="手机膜", computed_at="x", date_str="d",
                        items=[], alerts=empty_alerts, gantts=[])
    a2_plain = load_workbook(io.BytesIO(plain))["在研SKU状态总表"]["A2"].value or ""
    assert "待校准" not in a2_plain
    assert "标注" in a2_plain          # 2026-10-29：A2 恒为标注图例行


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
        # 当前停留环节必须在；距超时=负 → 已超期，正 → 剩 N 天
        assert x.get("staySeg")
        if x.get("stayDaysLeft") is not None:
            assert x["stayDaysLeft"] == round(x["stayDaysLeft"], 1)
    # 排序：距超时升序（负=已超期最前、无目标沉底）——最紧急的排最前
    lefts = [x["stayDaysLeft"] for x in d["items"]
             if x["stayDaysLeft"] is not None]
    assert lefts == sorted(lefts)


def test_inflight_g506_queryable(inflight_data, review_g506_done):
    """验收锚点（T18 业务口径取代旧 §7 T03「必须在研」）：G506 必须**可查**。

    G506 三部门段级已全部完成（18 段：17 已完成 + 1 留空），已发货货件全部到货
    → 按 T18 业务口径属**在售品**：移出在研清单（免追踪，单列 transportExempted）。
    但「事中档案」GET /api/inflight/sku/{code} 必须仍能打开（用户明确要求可随时回查）。
    保留原用例对 partStat.running 的覆盖意图：Part8/Part9 仍挂「审批中」单据（文书未收尾）。
    """
    if not review_g506_done:
        pytest.skip("G506 未导入或未完结，锚点不适用")
    det = inflight_data["skuDetails"].get("G506")
    assert det is not None, "G506 必须可查（在售品免追踪后仍进 skuDetails）"
    assert det["partStat"]["Part8"]["running"] >= 1 \
        or det["partStat"]["Part9"]["running"] >= 1
    # 全段完成 + 运输已闭环 → 免追踪：不在 items，进 transportExempted
    assert "G506" not in {x["sku"] for x in inflight_data["items"]}
    assert "G506" in {x["sku"] for x in inflight_data["transportExempted"]}


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


def test_alerts_today_yesterday_regress(monkeypatch):
    """2026-10-29 修复回归：「昨日是否已超期」必须用**昨日**的日期判定。

    G607 真实场景：段起点被批次回填提前一天（昨日快照 t0=今天-13 天，今日 t0
    已回填为今天-14 天），今日 elapsed=14 > 目标 12、一出现就超 2 天 ——
    旧实现拿**今日**日期减昨日 t0（=13 > 12）误判"昨日已超" → 归入持续超期，
    「当日新超期」永远打不出来（实测 10-27~10-29 三天全标持续超期）。
    修复后：昨日(今天-1) − 昨日t0(今天-13) = 12 ≤ 12 → 昨日未超 → 当日新超期。
    日期全部按 date.today() 动态构造，不依赖真实日历。
    """
    from datetime import timedelta
    _today = date.today()
    _t0_prev = _today - timedelta(days=13)     # 昨日快照里的段起点（回填前）
    _t0_now = _today - timedelta(days=14)      # 今日批次回填后的段起点
    det = {
        "segments": [{"dept": "产品", "seg": "取数→签样", "from": "取数",
                      "start": "2026-10-15(系统)", "src0": "系统",
                      "end": None, "src1": None}],
        "nodes": {"取数": _t0_now.strftime("%Y-%m-%d %H:%M:%S")},
        "overdues": [{"dept": "产品", "seg": "取数→签样", "elapsed": 14,
                      "target": 12, "overDays": 2, "owner": "李杰华"}],
        "warnings": [],
    }
    data = {"skuDetails": {"G607": det}, "warnRatio": 0.85}
    monkeypatch.setattr(
        svc, "_prev_day_nodes",
        lambda cat, today_str=None: {
            "G607": {"取数": _t0_prev.strftime("%Y-%m-%d %H:%M:%S")}})
    alerts = svc.compute_alerts_today(None, data=data)
    assert [(r["sku"], r["seg"]) for r in alerts["todayNewOverdue"]] == \
        [("G607", "取数→签样")]
    assert alerts["ongoingOverdue"] == []
    # 段时间追溯字段（2026-10-29 系统时间/audit 字段需求）随行透出
    row = alerts["todayNewOverdue"][0]
    assert row["src0"] == "系统" and row["start"] == "2026-10-15(系统)"
    assert row["end"] is None and row["src1"] is None


def test_alerts_today_ongoing_when_yesterday_over(monkeypatch):
    """对照组：昨日（today-1）− 昨日 t0 > target → 持续超期（口径不松）。"""
    from datetime import timedelta
    _today = date.today()
    _t0_prev = _today - timedelta(days=15)     # 昨日 elapsed = 14 > 12 → 已超
    _t0_now = _today - timedelta(days=15)      # 今日同一起点（无回填）
    det = {
        "segments": [{"dept": "运营", "seg": "取数→签样", "from": "取数",
                      "start": "x", "src0": "系统", "end": None, "src1": None}],
        "nodes": {"取数": _t0_now.strftime("%Y-%m-%d %H:%M:%S")},
        "overdues": [{"dept": "运营", "seg": "取数→签样", "elapsed": 15,
                      "target": 12, "overDays": 3, "owner": ""}],
        "warnings": [],
    }
    data = {"skuDetails": {"G1": det}, "warnRatio": 0.85}
    monkeypatch.setattr(
        svc, "_prev_day_nodes",
        lambda cat, today_str=None: {
            "G1": {"取数": _t0_prev.strftime("%Y-%m-%d %H:%M:%S")}})
    alerts = svc.compute_alerts_today(None, data=data)
    assert [(r["sku"], r["seg"]) for r in alerts["ongoingOverdue"]] == \
        [("G1", "取数→签样")]
    assert alerts["todayNewOverdue"] == []


# ---------------------------------------------------------------------------
# 2026-10 增量：段级时间轴「开始/结束」两列 + 运输闭环免追踪顶层键
# ---------------------------------------------------------------------------
def test_seg_public_exposes_start_end():
    """_seg_public 必带 start/end（前端段级时间轴「开始」「结束」两列取值，
    前端**不用** t0/t1）。start/end 串尾已自带时间来源标注。"""
    row = _row(seg="取数→签样", start="2026-09-01(系统)", end="2026-09-10(动作)",
               t0=_dt("2026-09-01"), t1=_dt("2026-09-10"))
    pub = svc._seg_public(row, svc.seg_state(row, today=TODAY))
    assert pub["start"] == "2026-09-01(系统)"
    assert pub["end"] == "2026-09-10(动作)"
    assert pub["t0"] == "2026-09-01 00:00:00"
    assert pub["t1"] == "2026-09-10 00:00:00"


def test_inflight_exposes_transport_keys(inflight_data):
    """顶层运输键 + 在研 item 运输字段 + 运输在途·逐票（T18）。"""
    d = inflight_data
    assert isinstance(d["transportExemptCount"], int)
    assert isinstance(d["transportExempted"], list)
    assert d["transportExemptCount"] == len(d["transportExempted"])
    assert d["transportDef"]["l4Name"] == "采购→发货末批"
    for x in d["transportExempted"]:
        assert x["kind"] == "运输闭环" and x["sku"] and x["reason"]
        assert x["closure"] in ("closed", "none")
        # 免追踪 SKU 仍须可查「事中档案」
        assert x["sku"] in d["skuDetails"]
    # 在研 item 的运输字段
    for it in d["items"]:
        assert it["shipClosure"] in ("closed", "pending", "none")
        assert isinstance(it["pendingCountries"], list)
        assert isinstance(it["notShippedHubs"], list)
        sc = it["shipCounts"]
        assert set(sc) == {"shipped", "arrived", "pending"}
    # 运输在途·逐票：结构与「仅收有未到货票/有未发货渠道」约束
    tit = d["transportInTransit"]
    assert isinstance(tit, list)
    for e in tit:
        assert e["sku"] and e["closure"] in ("closed", "pending", "none")
        assert isinstance(e["tickets"], list)
        assert isinstance(e["unshippedHubs"], list)
        assert e["closure"] == "pending" or e["unshippedHubs"], \
            "仅收有未到货票 / 有未发货渠道的 SKU"
        for t in e["tickets"]:
            assert set(t) == {"hub", "trk", "ship", "arr", "state"}
            assert t["state"] in ("已到货", "未到货")
            # 日期已转字符串（不得泄漏 datetime）
            assert t["ship"] is None or isinstance(t["ship"], str)
            assert t["arr"] is None or isinstance(t["arr"], str)


def test_gantt_label_columns_have_dates():
    """在研甘特学复盘 dates_mode（2026-10-19 用户要求"把日期也带上"）。

    标签列：A部门 B环节 C起点 D终点 E耗时 F目标 G判定 H执行人，日历轴从 I(9)。
    未完成段的终点是"延伸到今日"的填充值 → 必须写「进行中」，不能伪造完成日期。
    """
    from openpyxl import load_workbook

    from app.reporting import G_CAL0       # 顶部已把 daily_report 别名成 _daily_xlsx

    gantts = [{"sku": "G9", "model": "M9", "status": "预警", "staySeg": "采购→上架",
               "stayDaysLeft": 3,
               "rows": [
                   {"dept": "产品", "seg": "取数→签样",
                    "t0": datetime(2026, 10, 12, 9, 30), "t1": datetime(2026, 10, 14),
                    "days": 2, "target": 12, "judge": "✓", "owner": "李四",
                    "open": False, "neutral": False},
                   {"dept": "运营", "seg": "采购→上架",
                    "t0": datetime(2026, 10, 15), "t1": datetime(2026, 10, 19, 12),
                    "days": 4, "target": 7, "judge": "⏳进行中(已4天)",
                    "owner": "张三", "open": True, "neutral": False}]}]
    wb = load_workbook(io.BytesIO(_daily_xlsx(
        category="手机膜", computed_at="2026-10-19 10:00", date_str="2026-10-19",
        items=[], alerts={"todayNewOverdue": [], "ongoingOverdue": [],
                          "warnings": []}, gantts=gantts)))
    ws = wb["在研SKU甘特图"]
    assert G_CAL0 == 9                     # 标签列 8 列，日历从 I
    # 区块：3=标题 4=月份 5=日期 6/7=两条条形
    assert ws.cell(row=6, column=3).value == "10-12 09:30"   # 起点（带时刻）
    assert ws.cell(row=6, column=4).value == "10-14"         # 终点（纯日期）
    assert ws.cell(row=6, column=5).value == 2               # 耗时
    assert ws.cell(row=6, column=6).value == "≤12"           # 目标
    assert ws.cell(row=6, column=7).value == "✓"             # 判定
    assert ws.cell(row=6, column=8).value == "李四"          # 执行人
    assert ws.cell(row=7, column=4).value == "进行中"        # 未完成段不伪造日期
    assert ws.cell(row=7, column=8).value == "张三"
    # 日历轴首格在 I 列（日期数字），且冻结在 I3
    assert isinstance(ws.cell(row=5, column=G_CAL0).value, int)
    assert ws.freeze_panes == "I3"
