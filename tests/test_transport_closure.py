# -*- coding: utf-8 -*-
"""
test_transport_closure.py — 「运输闭环免追踪」单一真源（transport_closure.py）口径测试
================================================================================
背景（2026-10 需求）：物流到货不是可催办动作 —— 已发货货件全部到货（或压根没发货）
时，运输域不应再作为「在研/催办」卡点。本模块把判定抽成纯函数，两个消费方（事中日报
inflight / 加急预警 alerts）共用。本文件钉住三件事：

  ① 纯函数 —— classify_transport 四分支（none/closed/pending + 多票聚合）、
     is_transport_segment 三种命中、字段名容错、notShippedHubs 不参与 closure；
  ② 事中引擎 —— 在售品（全段已完成 + 运输无未到货）→ 移出 items 进 transportExempted
     （但 detail 仍可查、不进快照）；
     运输闭环 + 有非运输在研段 → 仍在研（剔除运输域卡点重算 status）；
     运输未发货 / 未闭环 → **保留**并标注（G597 回归锁）；人工登记仍最先短路；
  ③ 加急预警 —— `_shipments` 委托 `review.build_shipments` 后与旧实现 parity；
     运输闭环门把「只剩运输域原因」的行移入 shippedExemptRows。
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import inflight_service as svc_inflight                 # noqa: E402
from app import transport_closure as tc                          # noqa: E402
from app.routers import alerts                                   # noqa: E402


def _dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# ① 纯函数 classify_transport
# ---------------------------------------------------------------------------
def _ship(trk, hub, ship=None, arr=None, no_ship=False):
    return {"trk": trk, "hub": hub, "ship": ship, "arr": arr, "noShip": no_ship}


def test_classify_none_when_no_shipped():
    """无任何票已发货（只有 noShip 占位）→ none；占位渠道进 notShippedHubs。"""
    out = tc.classify_transport([
        _ship("", "东南亚仓", no_ship=True),
        _ship("", "欧洲仓", no_ship=True)])
    assert out["closure"] == "none"
    assert out["shippedCount"] == 0 and out["arrivedCount"] == 0
    assert out["pendingHubs"] == []
    assert out["notShippedHubs"] == ["东南亚仓", "欧洲仓"]
    assert out["pendingCount"] == 0


def test_classify_closed_all_arrived():
    """有已发货票且全部到货 → closed。"""
    out = tc.classify_transport([
        _ship("T1", "美国", _dt("2026-03-01 00:00:00"), _dt("2026-04-01 00:00:00")),
        _ship("T2", "欧洲", _dt("2026-03-02 00:00:00"), _dt("2026-04-10 00:00:00"))])
    assert out["closure"] == "closed"
    assert out["shippedCount"] == 2 and out["arrivedCount"] == 2
    assert out["shippedHubs"] == ["美国", "欧洲"] and out["pendingHubs"] == []
    assert out["pendingCount"] == 0


def test_classify_pending_lists_countries():
    """任一已发货票未到货 → pending，且该票国家进 pendingHubs。"""
    out = tc.classify_transport([
        _ship("T1", "美国", _dt("2026-03-01 00:00:00"), _dt("2026-04-01 00:00:00")),
        _ship("T2", "欧洲", _dt("2026-03-02 00:00:00"))])
    assert out["closure"] == "pending"
    assert out["pendingHubs"] == ["欧洲"]
    assert out["pendingCount"] == 1
    assert out["arrivedCount"] == 1 and out["shippedCount"] == 2


def test_multishipment_hub_pending():
    """同仓两票、只一票到货 → pending，且该仓进 pendingHubs（仓级展示去重）。"""
    out = tc.classify_transport([
        _ship("T1", "美国FBA", _dt("2026-03-01 00:00:00"), _dt("2026-04-01 00:00:00")),
        _ship("T2", "美国FBA", _dt("2026-03-05 00:00:00"))])
    assert out["closure"] == "pending"
    assert out["pendingHubs"] == ["美国FBA"]
    assert out["shippedHubs"] == ["美国FBA"]
    assert out["pendingCount"] == 1


def test_noship_hub_ignored():
    """noShip 占位渠道**绝不**参与 closure 判定（有已发货票全到货时仍判 closed）。"""
    out = tc.classify_transport([
        _ship("T1", "美国", _dt("2026-03-01 00:00:00"), _dt("2026-04-01 00:00:00")),
        _ship("", "东南亚仓", no_ship=True)])
    assert out["closure"] == "closed"
    assert out["notShippedHubs"] == ["东南亚仓"]
    assert out["pendingHubs"] == []
    # noShip 渠道不是"已发货票"，不计入 shipped
    assert out["shippedCount"] == 1


def test_classify_field_name_tolerance():
    """字段名容错：中文键名（货件号/渠道仓到货时间…）同样识别。"""
    out = tc.classify_transport([
        {"货件号": "T9", "发往渠道仓库": "欧洲", "发货日期": "2026-03-01",
         "渠道仓到货时间": None},
        {"货件号": "T8", "发往渠道仓库": "美国", "发货时间": "2026-03-02",
         "到货时间": "2026-04-01"}])
    assert out["shippedCount"] == 2 and out["arrivedCount"] == 1
    assert out["closure"] == "pending" and out["pendingHubs"] == ["欧洲"]


def test_is_transport_segment():
    """运输域判定：部门（运输/4国）/ L4 段名 / 「·未发货」后缀 命中；普通段不命中。"""
    l4 = "采购→发货末批"
    assert tc.is_transport_segment("运输", "空运·运输", l4) is True
    assert tc.is_transport_segment("4国", "英国·运输", l4) is True
    assert tc.is_transport_segment("后勤", l4, l4) is True
    assert tc.is_transport_segment("后勤", "东南亚仓·未发货", l4) is True
    assert tc.is_transport_segment("运营", "素材加工→上架", l4) is False
    assert tc.is_transport_segment("产品", "取数→签样", l4) is False


def test_transport_exempt_reason_copy():
    """免追踪原因文案说人话，且区分三种 closure。"""
    assert "免追踪" in tc.transport_exempt_reason("closed", [], [])
    assert "未闭环" in tc.transport_exempt_reason("pending", ["美国"], [])
    assert "未形成卡点" in tc.transport_exempt_reason("none", [], ["东南亚仓"])
    assert "东南亚仓" in tc.transport_exempt_reason("none", [], ["东南亚仓"])


# ---------------------------------------------------------------------------
# ② 事中引擎：运输闭环门
# ---------------------------------------------------------------------------
_PAST = _dt("2025-01-01 09:00:00")          # 极早起点 → 必然超期
_RECENT = datetime.now() - timedelta(days=3)


def _idoc(part, raw, no, status="已结束"):
    return {"part": part, "approval_no": no, "status": status,
            "create_time": _dt("2025-06-01 09:00:00"),
            "update_time": None, "finish_time": None, "raw": raw, "nodes": []}


def _mk_info(model, parts):
    rec = {"model": model, "parts": parts, "has_finished": False,
           "all_terminated": True}
    for docs in parts.values():
        for d in docs:
            if d["status"] == "已结束":
                rec["has_finished"] = True
            if d["status"] != "终止":
                rec["all_terminated"] = False
    return rec


def _seg(dept, seg, t0, t1=None, target=5, no_ship=False):
    return {"dept": dept, "seg": seg, "from": "A", "to": "B", "target": target,
            "t0": t0, "t1": t1, "judge": "—", "gap": False, "noShip": no_ship,
            "assessed": True, "owner": "X", "executor": "Y",
            "start": None, "end": None, "days": None}


_P5 = {"Part5": [_idoc("Part5", {"货件号": "T1", "发往渠道仓库": "美国",
                              "发货日期": "2026-03-01 00:00:00"}, "P5-1")]}
_P8 = {"Part8": [_idoc("Part8", {"货件号": "T1", "到货渠道仓": "美国",
                              "渠道仓到货时间": "2026-04-01 00:00:00"}, "P8-1")]}
# GC：已发货未到货（单号 T2 无 Part8 记录）
_P5P = {"Part5": [_idoc("Part5", {"货件号": "T2", "发往渠道仓库": "美国",
                               "发货日期": "2026-03-01 00:00:00"}, "P5-2")]}
# GN：渠道未发货（Part5 已立项渠道但无货件号 → noShip 占位 → closure=none）
_P5N = {"Part5": [_idoc("Part5", {"发往渠道仓库": "东南亚仓"}, "P5-N")]}

_SEGS_BY_SKU = {
    # GA：所有段已完成 + 运输已闭环 → 在售品（免追踪）
    "GA": [_seg("运输", "空运·运输", _PAST, t1=_dt("2025-02-01 09:00:00"))],
    # GB：运输域有在跑段（超期）+ 非运输在研段（素材→上架，进行中）→ 保留，
    #     运输域卡点被剔除、行打标 transportExempt
    "GB": [_seg("运输", "空运·运输", _PAST),
           _seg("运营", "素材加工→上架", _RECENT, target=60)],
    # GC：运输未闭环（pending）→ 保留 + 标注未到货国家
    "GC": [_seg("运营", "素材加工→上架", _RECENT, target=60)],
    # GH：人工登记（最先短路）
    "GH": [_seg("运输", "空运·运输", _PAST)],
    # GN：渠道未发货（closure=none + 「·未发货」在跑段）→ **必须保留**（本次 bug 回归锁）
    "GN": [_seg("后勤", "东南亚仓·未发货", _PAST, no_ship=True)],
}

_HOLDS = {"GH": {"reason": "渠道取消", "registeredAt": "2026-09-28"}}


@pytest.fixture()
def patch_inflight(monkeypatch):
    """打桩 compute_inflight 的外部依赖，用真实 build_shipments/classify_transport。"""
    by_sku = {
        "GA": _mk_info("M-GA", {**_P5, **_P8}),
        "GB": _mk_info("M-GB", {**_P5, **_P8}),
        "GC": _mk_info("M-GC", dict(_P5P)),
        "GH": _mk_info("M-GH", {**_P5, **_P8}),
        "GN": _mk_info("M-GN", dict(_P5N)),
    }
    monkeypatch.setattr(svc_inflight, "_load_all", lambda cat: by_sku)
    monkeypatch.setattr("app.routers.review.get_review_data",
                        lambda cat: {"skuDetails": {}})
    monkeypatch.setattr("app.routers.review.resolve_part1_rounds",
                        lambda docs, ruleset=None: (None, [], {}))
    monkeypatch.setattr(
        "app.routers.review.review_sku_segments",
        lambda sku, docs, rules, ruleset=None: (_SEGS_BY_SKU.get(sku, []), {}, []))
    monkeypatch.setattr("app.causal.locate", lambda *a, **k: None)
    monkeypatch.setattr("app.rule_service.get_rules", lambda: ({}, "v"))
    monkeypatch.setattr("app.exemption.load_registries", lambda: (_HOLDS, {}))
    return by_sku


def test_inflight_transport_exempt_removed(patch_inflight):
    """在售品（全段已完成 + 运输无未到货）→ 移出 items 进 transportExempted；
    ⚠️ 但 detail **仍须可查**（skuDetails）—— 用户要求被免追踪的 SKU 仍能打开「事中档案」
    （随时回查 G487）；只是不进 items / 不进 _snap_nodes / 不进 KPI。"""
    data = svc_inflight.compute_inflight("手机膜")
    skus = {x["sku"] for x in data["items"]}
    assert "GA" not in skus, "在售品（全段完成）应免追踪"
    assert "GA" in data["skuDetails"], "免追踪 SKU 仍须可查「事中档案」"
    det = data["skuDetails"]["GA"]
    assert det["transportExempt"] is True and det["exemptKind"] == "运输闭环"
    assert "GA" not in data["_snap_nodes"], "免追踪 SKU 不进快照"
    tex = {x["sku"]: x for x in data["transportExempted"]}
    assert "GA" in tex and tex["GA"]["kind"] == "运输闭环"
    assert tex["GA"]["closure"] == "closed"
    assert data["transportExemptCount"] == len(data["transportExempted"]) >= 1
    assert all(x["reason"] for x in data["transportExempted"])


def test_inflight_keeps_unshipped(patch_inflight):
    """回归锁（本次 bug 的核心）：运输未发货 —— closure=none + 一条「·未发货」在跑段 ——
    **必须保留追踪**，绝不能被当成"在售品"免掉。
    业务实况（G597）：发货期限没过、压根没发货，凭什么丢开在研。"""
    data = svc_inflight.compute_inflight("手机膜")
    items = {x["sku"]: x for x in data["items"]}
    assert "GN" in items, "渠道未发货（有在跑段）→ 不得免追踪"
    gn = items["GN"]
    assert gn["shipClosure"] == "none"
    assert gn["notShippedHubs"] == ["东南亚仓"]
    assert gn["transportExempt"] is False
    assert all(x["sku"] != "GN" for x in data["transportExempted"])


def test_inflight_keeps_when_nontransport_pending(patch_inflight):
    """运输闭环但仍有非运输在研段 → 仍在研；运输域卡点剔除后重算 status。"""
    data = svc_inflight.compute_inflight("手机膜")
    items = {x["sku"]: x for x in data["items"]}
    assert "GB" in items, "运输闭环但有非运输在研段 → 仍要盯"
    gb = items["GB"]
    assert gb["transportExempt"] is True
    # 唯一的超期段是运输段 → 剔除后不判超期（进行中）
    assert gb["status"] == svc_inflight.ST_RUNNING
    # 当前停留不再是运输段
    assert gb["staySeg"] == "素材加工→上架"
    det = data["skuDetails"]["GB"]
    assert det["transportExempt"] is True and det["shipClosure"] == "closed"
    assert det["overdues"] == []          # 运输域超期段已剔除


def test_inflight_pending_annotates_countries(patch_inflight):
    """运输未闭环（pending）→ 保留追踪并标注未到货国家。"""
    data = svc_inflight.compute_inflight("手机膜")
    items = {x["sku"]: x for x in data["items"]}
    assert "GC" in items
    assert items["GC"]["pendingCountries"] == ["美国"]
    det = data["skuDetails"]["GC"]
    assert det["shipClosure"] == "pending"
    assert det["pendingCountries"] == ["美国"]
    assert "美国" in det["transportReason"]
    # pending 不是免追踪
    assert all(x["sku"] != "GC" for x in data["transportExempted"])


def test_hold_beats_shipped(patch_inflight):
    """判定次序：人工登记 > 运输闭环 —— hold SKU 进 exempted，不进 transportExempted。"""
    data = svc_inflight.compute_inflight("手机膜")
    assert "GH" not in {x["sku"] for x in data["items"]}
    kinds = {x["sku"]: x["kind"] for x in data["exempted"]}
    assert kinds.get("GH") == "hold"
    assert all(x["sku"] != "GH" for x in data["transportExempted"])


# ---------------------------------------------------------------------------
# ③ 加急预警：货件构建器 parity + 运输闭环门
# ---------------------------------------------------------------------------
def _legacy_shipments(sku_data, rs):
    """2026-10 前 `alerts._shipments` 的**旧实现**（date 级 days、无排序）——
    仅作 parity 参照，证明委托 `review.build_shipments` 后消费语义零漂移。"""
    A = alerts
    mode_fn = rs.transport_mode if rs is not None else A.transport_mode
    cls_fn = rs.ship_class if rs is not None else A._ship_cls
    p5, p8 = {}, {}
    no_trk_hubs, hubs_with_trk = [], set()
    for d in sku_data["parts"].get("Part5") or []:
        raw = d["raw"]
        trk = A._clean(raw.get("货件号"))
        hub = A._clean(raw.get("发往渠道仓库"))
        if not trk:
            if hub and hub not in no_trk_hubs:
                no_trk_hubs.append(hub)
            continue
        hubs_with_trk.add(hub)
        sd = A._parse_dt(A._clean(raw.get("发货日期")))
        if trk not in p5 or (sd and not p5[trk][1]):
            p5[trk] = (hub, sd)
    for d in sku_data["parts"].get("Part8") or []:
        raw = d["raw"]
        trk = A._clean(raw.get("货件号"))
        ad = A._parse_dt(A._clean(raw.get("渠道仓到货时间")))
        if trk and (trk not in p8 or (ad and not p8[trk])):
            p8[trk] = ad
    items = []
    for trk, (hub, sd) in p5.items():
        arr = p8.get(trk)
        mode, tgt = mode_fn(hub)
        items.append({"trk": trk, "hub": hub, "ship": sd, "arr": arr,
                      "days": A._days(sd, arr), "mode": mode, "target": tgt,
                      "cls": cls_fn(mode), "noShip": False})
    for hub in no_trk_hubs:
        if hub in hubs_with_trk:
            continue
        mode, tgt = mode_fn(hub)
        items.append({"trk": "", "hub": hub, "ship": None, "arr": None,
                      "days": None, "mode": mode, "target": tgt,
                      "cls": cls_fn(mode), "noShip": True})
    return items


def _sorted(items):
    return sorted(items, key=lambda x: (x["trk"] or "zzz", x["hub"] or ""))


def test_alerts_shipments_builder_parity():
    """`alerts._shipments`（委托 review.build_shipments）与旧实现逐条一致。"""
    from app.category_rules import rules_for
    rs = rules_for("手机膜")
    sku_data = {"parts": {
        "Part5": [
            _idoc("Part5", {"货件号": "T1", "发往渠道仓库": "美国FBA",
                            "发货日期": "2026-03-01"}, "P5-1"),
            _idoc("Part5", {"货件号": "T2", "发往渠道仓库": "欧洲",
                            "发货日期": "2026-03-02"}, "P5-2"),
            _idoc("Part5", {"发往渠道仓库": "东南亚仓"}, "P5-3"),   # 无货件号 → noShip
        ],
        "Part8": [
            _idoc("Part8", {"货件号": "T1", "到货渠道仓": "美国FBA",
                            "渠道仓到货时间": "2026-04-01"}, "P8-1"),
        ],
    }}
    new = _sorted(alerts._shipments(sku_data, rs))
    old = _sorted(_legacy_shipments(sku_data, rs))
    assert new == old, f"委托后与旧实现不一致：\nnew={new}\nold={old}"


def test_alerts_shipped_exempt_rows(monkeypatch):
    """运输闭环门：只剩运输域原因的 SKU 进 shippedExemptRows；有非运输原因的保留。"""
    from app import rule_service

    # SH1：唯一原因是「空运超底线」（运输域）→ 免追踪
    # SH2：卡点 Part1（审批中停滞，非运输域）→ 保留
    def _adoc(status, raw=None, create=_dt("2026-01-01 09:00:00"),
              finish=None, times=None):
        return {"id": id(raw), "status": status, "create": create,
                "update": None, "finish": finish, "raw": raw or {},
                "times": times or {}}

    by_sku = {
        "SH1": {"model": "M1", "parts": {
            "Part1": [_adoc("已结束", {"手机数据取得日期": "2026-01-01 00:00:00"})],
            "Part5": [_adoc("已结束", {"货件号": "T1", "发往渠道仓库": "美国",
                                       "发货日期": "2026-01-02 00:00:00"})],
            "Part8": [_adoc("已结束", {"货件号": "T1", "到货渠道仓": "美国",
                                       "渠道仓到货时间": "2026-05-01 00:00:00"})],
        }},
        "SH2": {"model": "M2", "parts": {
            "Part1": [_adoc("审批中", {"手机数据取得日期": "2026-01-01 00:00:00"})],
        }},
    }
    monkeypatch.setattr(alerts, "_load_all_docs", lambda cat: by_sku)
    monkeypatch.setattr("app.routers.review.get_review_data",
                        lambda cat: {"skuDetails": {
                            "SH1": {"valid": False}, "SH2": {"valid": False}}})
    monkeypatch.setattr("app.sku_alias.load_aliases", lambda *a, **k: {})
    monkeypatch.setattr("app.sku_hold.load_holds", lambda *a, **k: {})
    monkeypatch.setattr(alerts, "get_rules",
                        lambda: (rule_service._default_rules(), 1))

    res = alerts.compute_alerts("手机膜")
    warned = {r["sku"] for r in res["rows"]}
    exempt = {r["sku"]: r for r in res["shippedExemptRows"]}

    assert "SH1" in exempt, "只剩运输域原因、运输已闭环 → 应进 shippedExemptRows"
    assert "SH1" not in warned, "免追踪 SKU 不得再进 P0/P1/P2"
    assert exempt["SH1"]["closure"] == "closed"
    assert res["shippedExemptCount"] == len(res["shippedExemptRows"])
    assert res["alertCount"] == res["p0"] + res["p1"] + res["p2"]

    assert "SH2" in warned, "有非运输域原因（Part1 停滞）→ 应保留催办"
    assert "SH2" not in exempt
