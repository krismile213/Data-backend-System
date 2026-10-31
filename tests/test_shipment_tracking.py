# -*- coding: utf-8 -*-
"""
test_shipment_tracking.py — 货件明细全量 / 事中日报 5 天留观
============================================================
2026-11-01 用户口径修正：
  · **货件明细（后台货件运输页）必须全量保留** —— 到货多久都要在明细里，
    5 天只作为「追踪状态」标记（留观 N/5 天 → 已归档(到货 N 天)），不得隐藏；
  · **事中日报「货件运输」sheet 仍只列追踪中的票**（到货 ≤5 天），
    报表要短、明细要全，两者不再共用同一条截断逻辑。

本文件钉住：
  ① transport_closure 纯函数边界（5 天临界 / 已运输天数三形态 / 到货至今钳 0）；
  ② inflight `_shipment_tracking_rows`（日报口径：>5 天丢弃、noShip 排除）；
  ③ `compute_transport`（明细口径：全量返回 + archived 标记 + KPI 分层计数）。
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
# ② 事中日报口径：只列追踪中的真货件
# ---------------------------------------------------------------------------
def test_tracking_rows_keep_5d_drop_beyond():
    ships = [
        _ship("T-5D", arr=_dt(TODAY - timedelta(days=5))),    # 临界 5 天 → 保留
        _ship("T-6D", arr=_dt(TODAY - timedelta(days=6))),    # 超 5 天 → 丢
        _ship("T-TRANSIT", ship=_dt(TODAY - timedelta(days=7))),  # 在途 → 保留
        _ship("", no_ship=True),                               # 未发货占位 → 丢
    ]
    rows = svc_inflight._shipment_tracking_rows("GA", "M-GA", ships, TODAY)
    assert [r["trk"] for r in rows] == ["T-5D", "T-TRANSIT"]
    assert rows[0]["state"] == "已到货" and rows[0]["keep"] == 5
    assert rows[1]["state"] == "在途" and rows[1]["keep"] is None
    # 天数走 elapsed：到货票 = 发货→到货；在途票 = 发货→今天
    assert rows[1]["elapsed"] == 7


# ---------------------------------------------------------------------------
# ③ 明细口径：compute_transport 全量返回
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
