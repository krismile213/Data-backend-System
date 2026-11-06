# -*- coding: utf-8 -*-
"""日报纯轨时间列 + 未发货渠道占位行（2026-11-06）契约测试。

口径（用户确认，照搬复盘页 G600 定稿）：
  · 时间列拆**纯轨四列**：实际·起点/实际·终点（系统业务列）＋
    audit·起点/audit·终点（审批上报）；两轨并列、互不兜底，
    **实际缺失留空**，绝不拿 audit 值冒充（旧「起点时间+来源」混轨已废）。
  · 判定轨（已进行/耗时/超期）不动 —— 双轨只回答"判定站不站得住"。
  · 货件运输 sheet 表尾补「未发货渠道」占位行：红字待登记 / emag 灰字不催
    （2026-10-15 口径「有发货才标记」）。
"""

import io

from openpyxl import load_workbook

from app import reporting


def _item(sku, owner="李杰华", **kw):
    it = {"sku": sku, "model": f"MODEL-{sku}", "status": "超期",
          "staySeg": "取数→签样", "staySegDept": "运营",
          "stayElapsed": 30, "stayTarget": 10, "stayDaysLeft": -20,
          "warnSegs": [], "multiRound": False, "stayOver": True,
          "overSegs": [], "rootCause": {}, "owner": owner}
    it.update(kw)
    return it


def _alert(sku, **kw):
    row = {"sku": sku, "dept": "运营", "seg": "取数→签样",
           "overDays": 20, "elapsed": 30, "target": 10, "owner": "李杰华",
           "start": "2026-04-15 09:00(audit)", "src0": "audit",
           "end": None, "src1": "缺失",
           # 实际轨空（该段没有系统业务列）→ audit 兜底
           "startReal": None, "endReal": None,
           "auditFrom": "2026-04-15 09:00", "auditTo": None,
           "fallback": True}
    row.update(kw)
    return row


def _gen(items, alerts=None, shipments=None, digested=None):
    alerts = alerts or {"todayNewOverdue": [], "ongoingOverdue": [],
                        "warnings": []}
    return load_workbook(io.BytesIO(reporting.daily_report(
        category="手机膜", computed_at="2026-11-06 22:00",
        date_str="2026-11-06", items=items, alerts=alerts, gantts=[],
        shipments=shipments, digested=digested)))


def _head(ws, col=1, n=13):
    return [ws.cell(row=3, column=c).value for c in range(col, n + 1)]


def _row_of(ws, sku, col=1):
    for r in range(4, ws.max_row + 1):
        if ws.cell(row=r, column=1).value == sku:
            return [ws.cell(row=r, column=c).value for c in range(1, 14)]
    return None


# ---------------------------------------------------------------------------
# 纯轨四列
# ---------------------------------------------------------------------------
def test_overdue_sheet_pure_track_columns():
    """超期明细：表头是纯轨四列，实际空就空、audit 独立、兜底只写说明。"""
    wb = _gen([_item("G538")],
              alerts={"todayNewOverdue": [], "ongoingOverdue": [_alert("G538")],
                      "warnings": []})
    ws = wb["超期明细"]
    assert _head(ws, 8, 12) == ["实际·起点", "实际·终点",
                               "audit·起点", "audit·终点", "说明"]
    assert not any(h in ("起点时间", "起点来源", "终点时间", "终点来源")
                   for h in _head(ws)), "混轨列不能残留"
    row = _row_of(ws, "G538")
    assert row[7] in (None, ""), "实际缺失必须留空，不能用 audit 值冒充"
    assert row[8] in (None, "")
    assert row[9] == "2026-04-15 09:00", "audit 轨独立出值"
    assert row[10] in (None, ""), "终点未完成 → audit·终点也空"
    assert "兜底" in (row[11] or "")
    # 判定轨不动
    assert row[3] == 30 and row[5] == 20


def test_report_sheet_pure_track_columns():
    """异常上报清单：同样拆四列，货件超期行只有实际轨（无审批轨）。"""
    wb = _gen([_item("G538")],
              alerts={"todayNewOverdue": [_alert("G538")],
                      "ongoingOverdue": [], "warnings": []},
              shipments=[{"sku": "G590", "model": "M", "trk": "T1",
                          "hub": "亚马逊美国", "mode": "空运",
                          "ship": "2026-10-01", "arr": None,
                          "elapsed": 36, "target": 15, "state": "在途"}])
    ws = wb["异常上报清单"]
    assert _head(ws, 8, 12) == ["实际·起点", "实际·终点",
                               "audit·起点", "audit·终点", "说明"]
    # 货件超期行：发货日期落实际轨，audit 两列空
    ship_row = None
    for r in range(4, ws.max_row + 1):
        if ws.cell(row=r, column=1).value == "货件超期":
            ship_row = [ws.cell(row=r, column=c).value for c in range(1, 13)]
    assert ship_row is not None, "货件超期行应存在（36 > 15）"
    assert ship_row[7] == "2026-10-01"
    assert ship_row[9] in (None, "") and ship_row[10] in (None, "")
    assert "Part5" in (ship_row[11] or "")


def test_digested_sheet_pure_track_columns():
    """本月已消化超期：四列 + 耗时/目标/超期照旧，说明列标兜底。"""
    wb = _gen([], digested=[{
        "sku": "G603", "model": "M", "dept": "产品", "seg": "签样→寄送",
        "start": "2026-10-26(系统)", "src0": "系统",
        "end": "2026-11-29(系统)", "src1": "系统",
        "startReal": "2026-10-26", "endReal": "2026-11-29",
        "auditFrom": None, "auditTo": None, "fallback": False,
        "days": 34, "target": 5, "over": 29, "owner": "李杰华"}])
    ws = wb["本月已消化超期"]
    assert _head(ws, 5, 13) == ["实际·起点", "实际·终点", "audit·起点",
                               "audit·终点", "耗时(天)", "目标(天)",
                               "超期(天)", "责任人", "说明"]
    row = _row_of(ws, "G603")
    assert row[4] == "2026-10-26" and row[5] == "2026-11-29"
    assert row[6] in (None, "") and row[7] in (None, "")
    assert row[8] == 34 and row[10] == 29, "判定轨（耗时/超期）不受影响"
    assert row[12] in (None, ""), "无兜底 → 说明留空"


# ---------------------------------------------------------------------------
# 未发货渠道占位行
# ---------------------------------------------------------------------------
def _shipment(sku, hub="亚马逊美国", arr=None):
    return {"sku": sku, "model": f"MODEL-{sku}", "trk": "T1", "hub": hub,
            "mode": "空运", "ship": "2026-10-01", "arr": arr,
            "elapsed": 10, "target": 15, "state": "在途"}


def test_unshipped_hubs_listed_at_sheet_tail():
    """已立项未发货的地区要在表里出现，且区分 emag（不催）与待登记。"""
    wb = _gen([_item("G600", notShippedHubs=["emag罗马尼亚", "亚马逊日本"]),
               _item("G602", notShippedHubs=["东南亚泰国"])],
              shipments=[_shipment("G600", "亚马逊美国")])
    ws = wb["货件运输"]
    cells = [(ws.cell(row=r, column=1).value, ws.cell(row=r, column=4).value,
              ws.cell(row=r, column=10).value)
             for r in range(4, ws.max_row + 1)]
    unshipped = {hub: st for sku, hub, st in cells if st and st.startswith("未发货")}
    assert unshipped.get("emag罗马尼亚") == "未发货·emag不催"
    assert unshipped.get("亚马逊日本") == "未发货·待登记"
    assert unshipped.get("东南亚泰国") == "未发货·待登记"
    # 已发货票仍在（占位行只在表尾追加，不打乱主表）
    assert any(hub == "亚马逊美国" and st == "在途" for sku, hub, st in cells)


def test_unshipped_section_absent_when_all_shipped():
    """全部渠道都已发货 → 不产生表尾分隔行。"""
    wb = _gen([_item("G600", notShippedHubs=[])],
              shipments=[_shipment("G600", arr="2026-10-05")])
    ws = wb["货件运输"]
    assert not any(str(ws.cell(row=r, column=1).value or "").startswith("以下为")
                   for r in range(4, ws.max_row + 1))
