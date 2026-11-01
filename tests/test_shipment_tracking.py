# -*- coding: utf-8 -*-
"""
test_shipment_tracking.py — 货件明细与事中日报「货件运输」sheet 的追踪口径
========================================================================
2026-11-01 用户口径（第二版，推翻了同日上午的「日报截断」结论）：
  · **两处都全量保留** —— 后台货件运输页 + 事中日报「货件运输」sheet，
    SKU 在研期间每一票都在，到货多久都不删行；
  · 5 天留观只降级为「追踪状态」文案：留观 N/5 天 → 已归档(到货 N 天)。
    理由（用户原话）：留观 5 天确实能减量，但会「增加重新查询的麻烦」，
    要的是「整个 SKU 彻底结束才不追踪」。

本文件钉住：
  ① transport_closure 纯函数边界（5 天临界 / 已运输天数三形态 / 到货至今钳 0）；
  ② inflight `_shipment_tracking_rows`（全量保留 + 状态文案 + 排序 + noShip 排除）；
  ③ 日报 sheet 渲染（归档票要真画出来，标题不能还写「超出不追踪」）；
  ④ `compute_transport`（明细口径：全量返回 + archived 标记 + KPI 分层计数）。
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import inflight_service as svc_inflight                 # noqa: E402
from app import transport_closure as tc                          # noqa: E402
from app.routers import transport as tr                          # noqa: E402


TODAY = date(2026, 11, 1)


def _dt(d):
    """date → 当天 08:00 的 datetime（模拟 Part5/Part8 的真实时间列）。"""
    return datetime(d.year, d.month, d.day, 8, 0, 0)


def _ship(trk, hub="美国仓", ship=None, arr=None, no_ship=False,
          mode="空运", cls="空运", target=15, days=None):
    return {"trk": trk, "hub": hub, "ship": ship, "arr": arr, "noShip": no_ship,
            "mode": mode, "cls": cls, "target": target, "days": days,
            "stallDays": None}


# ---------------------------------------------------------------------------
# ① 纯函数
# ---------------------------------------------------------------------------
def test_shipment_keep_boundary():
    """到货 5 天保留、6 天不保留；未到货/未发货（无到货日）恒保留。"""
    ship = _dt(TODAY - timedelta(days=20))
    assert tc.shipment_keep(ship, _dt(TODAY - timedelta(days=5)), TODAY) is True
    assert tc.shipment_keep(ship, _dt(TODAY - timedelta(days=6)), TODAY) is False
    assert tc.shipment_keep(ship, None, TODAY) is True
    assert tc.shipment_keep(None, None, TODAY) is True


def test_elapsed_days_three_forms():
    """已到货 = 发货→到货；在途 = 发货→今天；无发货日 = None；字符串同样可解析。"""
    ship = _dt(TODAY - timedelta(days=10))
    arr = _dt(TODAY - timedelta(days=3))
    assert tc.elapsed_days(ship, arr, TODAY) == 7
    assert tc.elapsed_days(ship, None, TODAY) == 10
    assert tc.elapsed_days(None, arr, TODAY) is None
    # 后台看板侧 review._fmt 产出的是字符串（YYYY-MM-DD / YYYY-MM-DD HH:MM）
    assert tc.elapsed_days("2026-10-22", "2026-10-29", TODAY) == 7
    assert tc.elapsed_days("2026-10-22 08:00", None, TODAY) == 10


def test_arrival_ago_days_clamps_future():
    assert tc.arrival_ago_days(_dt(TODAY - timedelta(days=4)), TODAY) == 4
    assert tc.arrival_ago_days(_dt(TODAY + timedelta(days=2)), TODAY) == 0
    assert tc.arrival_ago_days(None, TODAY) is None


# ---------------------------------------------------------------------------
# ② 事中日报口径：全量保留（5 天只改状态文案）
# ---------------------------------------------------------------------------
def test_tracking_rows_keep_all_beyond_5d_marked_archived():
    """超 5 天不再丢行 —— 只降级成「已归档(到货N天)」，这是本批的核心断言。

    旧口径下 T-6D 会被 shipment_keep 直接 continue 掉；若哪天有人把
    `if not shipment_keep(...): continue` 加回来，这条必须挂。
    """
    ships = [
        _ship("T-5D", arr=_dt(TODAY - timedelta(days=5))),      # 临界 5 天 → 留观
        _ship("T-6D", arr=_dt(TODAY - timedelta(days=6))),      # 超 5 天 → 已归档（保留）
        _ship("T-OLD", arr=_dt(TODAY - timedelta(days=40))),    # 很老 → 已归档（保留）
        _ship("T-TRANSIT", ship=_dt(TODAY - timedelta(days=7))),  # 在途
        _ship("", no_ship=True),                                 # 未发货占位 → 仍排除
    ]
    rows = svc_inflight._shipment_tracking_rows("GA", "M-GA", ships, TODAY)
    by = {r["trk"]: r for r in rows}
    assert set(by) == {"T-5D", "T-6D", "T-OLD", "T-TRANSIT"}, "除未发货占位外一票都不能少"

    assert by["T-5D"]["archived"] is False
    assert by["T-5D"]["track"] == "留观5/5天"
    assert by["T-6D"]["archived"] is True
    assert by["T-6D"]["track"] == "已归档(到货6天)"
    assert by["T-OLD"]["track"] == "已归档(到货40天)"
    assert by["T-TRANSIT"]["state"] == "在途"
    assert by["T-TRANSIT"]["track"] == "在途"
    assert by["T-TRANSIT"]["archived"] is False
    # 天数走 elapsed：到货票 = 发货→到货；在途票 = 发货→今天
    assert by["T-TRANSIT"]["elapsed"] == 7


def test_tracking_rows_sort_transit_then_watch_then_archived():
    """排序：在途最前（运得久在前）→ 留观（新到货在前）→ 已归档沉底。"""
    ships = [
        _ship("OLD-30", ship=_dt(TODAY - timedelta(days=60)),
              arr=_dt(TODAY - timedelta(days=30))),
        _ship("WATCH-4", ship=_dt(TODAY - timedelta(days=20)),
              arr=_dt(TODAY - timedelta(days=4))),
        _ship("TRANSIT-20", ship=_dt(TODAY - timedelta(days=20))),
        _ship("WATCH-1", ship=_dt(TODAY - timedelta(days=16)),
              arr=_dt(TODAY - timedelta(days=1))),
        _ship("TRANSIT-9", ship=_dt(TODAY - timedelta(days=9))),
    ]
    rows = svc_inflight._shipment_tracking_rows("GA", "M-GA", ships, TODAY)
    assert [r["trk"] for r in rows] == [
        "TRANSIT-20",   # 在途，运得久
        "TRANSIT-9",
        "WATCH-1",      # 留观，新到货
        "WATCH-4",
        "OLD-30",       # 已归档沉底
    ]


def test_shipment_rows_global_order_transit_before_archived():
    """多 SKU 拼接后必须再全局排一次：否则第一个 SKU 的老票占满第一屏。

    模拟真实形态：A 仓全是几个月前的老票（已归档），B 仓还有在途票。
    单 SKU 内部排序救不了这个 —— 拼接顺序决定第一屏，所以 compute_inflight
    返回前用 `_shipment_sort_key` 全局重排（见 "shipmentTracking": sorted(...)）。
    """
    a = svc_inflight._shipment_tracking_rows("GA", "M-GA", [
        _ship("A-OLD1", ship=_dt(TODAY - timedelta(days=200)),
              arr=_dt(TODAY - timedelta(days=190))),
        _ship("A-OLD2", ship=_dt(TODAY - timedelta(days=120)),
              arr=_dt(TODAY - timedelta(days=110))),
    ], TODAY)
    b = svc_inflight._shipment_tracking_rows("GB", "M-GB", [
        _ship("B-TRANSIT", ship=_dt(TODAY - timedelta(days=12))),
        _ship("B-WATCH", ship=_dt(TODAY - timedelta(days=9)),
              arr=_dt(TODAY - timedelta(days=2))),
    ], TODAY)

    rows = sorted(a + b, key=svc_inflight._shipment_sort_key)
    # 归档组内按「新归档在前」：A-OLD2 到货 110 天 < A-OLD1 到货 190 天
    assert [r["trk"] for r in rows] == ["B-TRANSIT", "B-WATCH", "A-OLD2", "A-OLD1"]
    # 泛化断言：任何归档行都不能出现在非归档行之前
    rank = [2 if r["archived"] else (0 if r["state"] == "在途" else 1) for r in rows]
    assert rank == sorted(rank)


def test_tracking_rows_report_sheet_keeps_archived():
    """日报「货件运输」sheet 必须把归档票也画出来（标题不再写「超出不追踪」）。"""
    import io

    from openpyxl import load_workbook

    from app import reporting

    empty_alerts = {"todayNewOverdue": [], "ongoingOverdue": [], "warnings": []}
    rows = svc_inflight._shipment_tracking_rows("GA", "M-GA", [
        _ship("T-TRANSIT", ship=_dt(TODAY - timedelta(days=7))),
        _ship("T-WATCH", ship=_dt(TODAY - timedelta(days=18)),
              arr=_dt(TODAY - timedelta(days=3))),
        _ship("T-ARCH", ship=_dt(TODAY - timedelta(days=45)),
              arr=_dt(TODAY - timedelta(days=30))),
    ], TODAY)

    blob = reporting.daily_report(
        category="手机膜", computed_at="x", date_str="d",
        items=[], alerts=empty_alerts, gantts=[], shipments=rows)
    wb = load_workbook(io.BytesIO(blob))
    assert "货件运输" in wb.sheetnames

    ws = wb["货件运输"]
    assert "超出不追踪" not in str(ws["A1"].value), "标题不能再宣称不追踪"
    assert "全量保留" in str(ws["A1"].value)

    trks = [ws.cell(row=r, column=3).value for r in range(4, ws.max_row + 1)]
    states = [ws.cell(row=r, column=10).value for r in range(4, ws.max_row + 1)]
    assert trks == ["T-TRANSIT", "T-WATCH", "T-ARCH"], "归档票不能被丢掉"
    assert states == ["在途", "留观3/5天", "已归档(到货30天)"]


# ---------------------------------------------------------------------------
# ④ 明细口径：compute_transport 全量返回
# ---------------------------------------------------------------------------
def _det(ships):
    return {"shipments": ships, "devType": "常规开发", "valid": True}


def _patch_review(monkeypatch):
    """打桩 get_review_data，四类票各一张：归档 / 留观 / 在途 / 未发货。"""
    data = {
        "computedAt": "2026-11-01 08:00",
        "rows": [{"sku": "GA", "model": "M-GA", "bottleneck": None}],
        "skuDetails": {"GA": _det([
            _ship("T-OLD",
                  ship=_dt(TODAY - timedelta(days=40)),
                  arr=_dt(TODAY - timedelta(days=30)), days=10),
            _ship("T-NEW",
                  ship=_dt(TODAY - timedelta(days=18)),
                  arr=_dt(TODAY - timedelta(days=3)), days=15),
            _ship("T-TRANSIT",
                  ship=_dt(TODAY - timedelta(days=7)), days=None),
            _ship("", hub="东南亚仓", no_ship=True),
        ])},
    }
    monkeypatch.setattr(tr, "get_review_data", lambda cat: data)
    return data


class _Today(date):
    """固定 today，避免测试跨零点漂移。"""

    @classmethod
    def today(cls):
        return TODAY


def test_transport_detail_keeps_archived(monkeypatch):
    """明细必须全量：到货 30 天的老票也在 items 里，且排到最后。"""
    _patch_review(monkeypatch)
    monkeypatch.setattr(tr, "date", _Today)

    out = tr.compute_transport("手机膜")
    assert len(out["items"]) == 4, "明细必须全量保留（含归档票）"

    by_trk = {i["trk"]: i for i in out["items"] if i["trk"]}
    assert by_trk["T-OLD"]["archived"] is True
    assert by_trk["T-OLD"]["track"] == "已归档(到货30天)"
    assert by_trk["T-NEW"]["archived"] is False
    assert by_trk["T-NEW"]["track"] == "留观3/5天"
    assert by_trk["T-TRANSIT"]["track"] == "在途"
    assert out["items"][-1]["trk"] == "T-OLD", "归档票应排在追踪中之后"


def test_transport_kpi_layers(monkeypatch):
    """KPI：total/arrived 含归档；tracking 只数追踪中；noShip 单列。"""
    _patch_review(monkeypatch)
    monkeypatch.setattr(tr, "date", _Today)

    k = tr.compute_transport("手机膜")["kpi"]
    assert k["total"] == 3              # 不含 noShip 占位
    assert k["arrived"] == 2            # T-OLD + T-NEW
    assert k["notArrived"] == 1         # T-TRANSIT
    assert k["archived"] == 1           # T-OLD
    assert k["tracking"] == 2           # T-NEW + T-TRANSIT
    assert k["noShip"] == 1
    assert k["retentionDays"] == tc.TRACK_ARRIVED_DAYS == 5


def test_transport_aggregates_include_archived(monkeypatch):
    """渠道/方式聚合与明细同口径（全量），不再因归档掉票。"""
    _patch_review(monkeypatch)
    monkeypatch.setattr(tr, "date", _Today)

    out = tr.compute_transport("手机膜")
    hub = next(h for h in out["byHub"] if h["hub"] == "美国仓")
    assert hub["total"] == 3 and hub["arrived"] == 2
    air = next(m for m in out["byMode"] if m["cls"] == "空运")
    assert air["total"] == 3
