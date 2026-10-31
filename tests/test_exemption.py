# -*- coding: utf-8 -*-
"""
test_exemption.py — 「不再追踪」豁免判定单一真源（exemption.py）的口径测试
================================================================================
背景（2026-10-06 口径合并）：事中日报此前只有「全部单据终止」才排除，
人工登记 / 换号 / 末端终止的 SKU 全部混进「在研追踪」。本测试钉住三件事：

  ① 纯函数 —— classify 四分支 + void_info 的两种单据键名兼容 + 原因文案；
  ② 事中引擎 —— 豁免 SKU 移出 items/skuDetails/快照，exempted 备查不吞数据；
  ③ 单据增量 —— build_docs 的 open_skus 剔除豁免 SKU（判定用去重前全量单据）。
"""

import io
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app import exemption                                        # noqa: E402
from app import doc_delta_service as svc_delta                   # noqa: E402
from app import inflight_service as svc_inflight                 # noqa: E402
from app import reporting                                        # noqa: E402


def _dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def _doc(status, create=None, update=None, finish=None):
    """alerts 形状的单据。"""
    return {"status": status, "create": create, "update": update, "finish": finish}


def _doc_review(status, create=None, update=None, finish=None):
    """review 形状的单据（*_time 键名）。"""
    return {"status": status, "create_time": create, "update_time": update,
            "finish_time": finish}


def _part_docs(part, docs):
    """给单据补上 review 形状必需键（part/approval_no/create_time/nodes），
    让同一份夹具既能过 exemption（只用 status/时间）也能过 _dedupe_parts。"""
    return [dict(d, part=part, approval_no=f"NO-{part}-{i}",
                 create_time=d.get("create"), nodes=[])
            for i, d in enumerate(docs)]


# ---------------------------------------------------------------------------
# ① 纯函数
# ---------------------------------------------------------------------------
def test_classify_hold_beats_everything():
    """人工登记最先判：即使 SKU 同时满足末端终止，也归 hold。"""
    holds = {"G495": {"reason": "渠道取消", "registeredAt": "2026-09-28"}}
    parts = {"Part1": [_doc("已结束")],
             "Part8": [_doc("终止", finish=_dt("2026-02-02 10:00:00"))]}
    kind, info = exemption.classify("G495", parts, holds=holds, aliases={})
    assert kind == "hold"
    assert info["reason"] == "渠道取消"


def test_classify_alias_second():
    """换号次之：无人工登记但已换号 → alias，且 info 带新号。"""
    aliases = {"G493": {"new": "G601", "note": "", "effectiveAt": ""}}
    parts = {"Part1": [_doc("审批中", create=_dt("2025-06-01 09:00:00"))]}
    kind, info = exemption.classify("G493", parts, holds={}, aliases=aliases)
    assert kind == "alias"
    assert info["new"] == "G601"


def test_classify_void_tail_terminated():
    """末端终止：前面 Part 已结束 + 末尾 Part 全终止 + 无在跑单据 → void。"""
    parts = {"Part1": [_doc("已结束", finish=_dt("2025-07-08 09:06:47"))],
             "Part2": [_doc("终止", finish=_dt("2025-07-08 09:28:12"))]}
    kind, info = exemption.classify("G448", parts, holds={}, aliases={})
    assert kind == "void"
    assert info["part"] == "Part2"
    assert info["terminatedAt"] == _dt("2025-07-08 09:28:12")


def test_classify_active_when_running():
    """还有审批中单据、无任何登记 → active（照常追踪）。"""
    parts = {"Part1": [_doc("已结束")],
             "Part8": [_doc("审批中", create=_dt("2026-03-25 09:00:00"))]}
    kind, _ = exemption.classify("G506", parts, holds={}, aliases={})
    assert kind == "active"


def test_void_info_accepts_review_key_names():
    """两种单据键名（alerts 的 create/update/finish 与 review 的 *_time）判据一致。"""
    a = {"Part1": [_doc("已结束", finish=_dt("2025-07-08 09:06:47"))],
         "Part2": [_doc("终止", finish=_dt("2025-07-08 09:28:12"))]}
    b = {"Part1": [_doc_review("已结束", finish=_dt("2025-07-08 09:06:47"))],
         "Part2": [_doc_review("终止", finish=_dt("2025-07-08 09:28:12"))]}
    assert exemption.void_info(a) == exemption.void_info(b)
    assert exemption.void_info(b)[0] is True


def test_void_info_tolerates_doc_without_time():
    """终止单据缺时间也不炸（旧实现 max() 撞 None 会 TypeError）。"""
    parts = {"Part1": [_doc("终止")]}
    ok, part, at = exemption.void_info(parts)
    assert ok is True and part == "Part1" and at is None


def test_exempt_reason_copy():
    """三类豁免的原因文案都说人话。"""
    assert "人工登记" in exemption.exempt_reason(
        "hold", {"reason": "渠道取消"})
    assert "G601" in exemption.exempt_reason("alias", {"new": "G601"})
    s = exemption.exempt_reason("void", {"part": "Part8",
                                         "terminatedAt": _dt("2026-01-05 10:00:00")})
    assert "Part8" in s and "2026-01-05" in s
    assert exemption.exempt_reason("active", {}) == ""


# ---------------------------------------------------------------------------
# ② 事中引擎集成：豁免 SKU 移出追踪、exempted 备查
# ---------------------------------------------------------------------------
def _mk_info(model, docs_by_part):
    """compute_inflight 消费的 by_sku 行（_load_all 形状）。"""
    rec = {"model": model, "parts": docs_by_part, "has_finished": False,
           "all_terminated": True}
    for docs in docs_by_part.values():
        for d in docs:
            if d["status"] == "已结束":
                rec["has_finished"] = True
            if d["status"] != "终止":
                rec["all_terminated"] = False
    return rec


@pytest.fixture()
def patch_inflight_min(monkeypatch):
    """把 compute_inflight 的外部依赖打桩到最小：两个 SKU，无复盘 detail。"""
    holds = {"G495": {"reason": "渠道取消", "registeredAt": "2026-09-28"}}
    by_sku = {
        # 人工登记：还挂着审批中单据 —— 旧口径会把它当在研（回归点）
        "G495": _mk_info("Samsung S26 Ultra", {
            "Part1": _part_docs("Part1", [_doc("已结束", finish=_dt("2026-01-10 09:00:00"))]),
            "Part8": _part_docs("Part8", [_doc("审批中", create=_dt("2026-03-25 09:00:00"))])}),
        # 末端终止：前面已结束、末尾全终止 —— 旧口径 detail 有效前会被当在研
        "G448": _mk_info("Poco F7", {
            "Part1": _part_docs("Part1", [_doc("已结束", finish=_dt("2025-07-08 09:06:47"))]),
            "Part2": _part_docs("Part2", [_doc("终止", finish=_dt("2025-07-08 09:28:12"))])}),
        # 正常在研
        "G506": _mk_info("Xiaomi 17", {
            "Part1": _part_docs("Part1", [_doc("审批中", create=_dt("2026-08-01 09:00:00"))])}),
    }
    monkeypatch.setattr(svc_inflight, "_load_all", lambda cat: by_sku)
    monkeypatch.setattr("app.routers.review.get_review_data",
                        lambda cat: {"skuDetails": {}})
    monkeypatch.setattr("app.routers.review.fingerprint_for",
                        lambda cat: "fp-test")
    monkeypatch.setattr("app.rule_service.get_rules", lambda: ({}, "v"))
    monkeypatch.setattr("app.routers.review.resolve_part1_rounds",
                        lambda docs, ruleset=None: (None, [], {}))
    monkeypatch.setattr("app.routers.review.review_sku_segments",
                        lambda sku, docs, rules, ruleset=None: ([], {}, []))
    monkeypatch.setattr("app.causal.locate",
                        lambda *a, **k: None)
    monkeypatch.setattr(exemption, "load_registries", lambda: (holds, {}))
    return by_sku


def test_compute_inflight_exemptions_out_of_items(patch_inflight_min):
    """豁免 SKU（hold/void）不进 items/skuDetails，active 照常；exempted 备查。"""
    data = svc_inflight.compute_inflight("手机膜")
    skus = {x["sku"] for x in data["items"]}
    assert "G495" not in skus, "人工登记 SKU 不得出现在在研追踪"
    assert "G448" not in skus, "末端终止 SKU 不得出现在在研追踪"
    assert "G506" in skus
    assert data["inResearchCount"] == len(data["items"]) == 1
    assert data["exemptCount"] == 2
    kinds = {x["sku"]: x["kind"] for x in data["exempted"]}
    assert kinds == {"G495": "hold", "G448": "void"}
    assert all(x["reason"] for x in data["exempted"])
    assert "G495" not in data["skuDetails"]
    assert "G506" in data["skuDetails"]
    # 快照也只收 active SKU —— 豁免 SKU 不再产生「进度」噪声
    assert "G495" not in data["_snap_nodes"]
    assert "G506" in data["_snap_nodes"]


def test_daily_report_exempt_sheet(tmp_path):
    """exempts 非空 → 第 6 sheet「豁免备查」；空 → 保持 5 sheets。"""
    empty_alerts = {"todayNewOverdue": [], "ongoingOverdue": [], "warnings": []}
    base = dict(category="手机膜", computed_at="x", date_str="d",
                items=[], alerts=empty_alerts, gantts=[])
    from openpyxl import load_workbook

    with_ex = reporting.daily_report(**base, exempts=[
        {"sku": "G495", "model": "S26U", "kind": "hold",
         "reason": "人工登记：渠道取消（不列入追踪）"}])
    wb = load_workbook(io.BytesIO(with_ex))
    assert len(wb.sheetnames) == 8 and wb.sheetnames[-1] == "豁免备查"
    ws6 = wb["豁免备查"]
    assert ws6.cell(row=4, column=1).value == "G495"
    assert ws6.cell(row=4, column=3).value == "人工登记"

    without = reporting.daily_report(**base)
    assert len(load_workbook(io.BytesIO(without)).sheetnames) == 5


# ---------------------------------------------------------------------------
# ③ 单据增量：open_skus 剔除豁免 SKU（判定用去重前全量单据）
# ---------------------------------------------------------------------------
def test_build_docs_drops_exempt_skus(monkeypatch):
    holds = {"G495": {"reason": "渠道取消", "registeredAt": "2026-09-28"}}
    raw = [
        # G495：复盘 invalid（缺节点）+ 人工登记 → 旧口径混进追踪（回归点）
        {"sku": "G495", "part": "Part8", "approval_no": "A1",
         "create_time": _dt("2026-03-25 09:00:00"), "status": "审批中",
         "nodes": []},
        # G501：复盘 invalid、末端终止（Part2 全终止、无在跑）→ 剔除
        {"sku": "G501", "part": "Part1", "approval_no": "B1",
         "create_time": _dt("2025-07-08 09:06:00"), "status": "已结束",
         "nodes": []},
        {"sku": "G501", "part": "Part2", "approval_no": "B2",
         "create_time": _dt("2025-07-08 09:20:00"), "status": "终止",
         "finish_time": _dt("2025-07-08 09:28:12"), "nodes": []},
        # G506：复盘 invalid、无豁免 → 留在追踪集合
        {"sku": "G506", "part": "Part1", "approval_no": "C1",
         "create_time": _dt("2026-08-01 09:00:00"), "status": "审批中",
         "nodes": []},
    ]
    review = {"rows": [{"sku": "G495", "valid": False},
                       {"sku": "G501", "valid": False},
                       {"sku": "G506", "valid": False}]}
    monkeypatch.setattr("app.routers.review._load_sku_docs", lambda cat: raw)
    monkeypatch.setattr("app.routers.review.get_review_data",
                        lambda cat: review)
    monkeypatch.setattr(exemption, "load_registries", lambda: (holds, {}))

    docs, open_skus = svc_delta.build_docs("手机膜")
    assert "G495" not in open_skus and "G501" not in open_skus
    assert "G506" in open_skus
    assert {k.split("|")[0] for k in docs} == {"G506"}


# ---------------------------------------------------------------------------
# ④ 回归锁：既有三通道 kind 集合不变（「运输闭环」是**另一条**下游通道，
#    不得混进 exemption.classify —— 否则豁免语义与判定次序被污染）
# ---------------------------------------------------------------------------
def test_exemption_kind_set_unchanged():
    """exemption.classify 的返回值域恒为 {hold, alias, void, active}。"""
    holds = {"G1": {"reason": "r"}}
    aliases = {"G2": {"new": "G9"}}
    running = {"Part1": [_doc("审批中", create=_dt("2026-01-01 09:00:00"))]}
    void_parts = {"Part1": [_doc("已结束")],
                  "Part2": [_doc("终止", finish=_dt("2026-01-02 09:00:00"))]}
    assert exemption.classify("G1", running, holds=holds, aliases={})[0] == "hold"
    assert exemption.classify("G2", running, holds={}, aliases=aliases)[0] == "alias"
    assert exemption.classify("G3", void_parts, holds={}, aliases={})[0] == "void"
    assert exemption.classify("G4", running, holds={}, aliases={})[0] == "active"
    # 「运输闭环」绝不从 exemption 产出（它走 app/transport_closure.py）
    for sku, parts in (("G1", running), ("G3", void_parts), ("G4", running)):
        kind, _ = exemption.classify(sku, parts, holds=holds, aliases=aliases)
        assert kind in ("hold", "alias", "void", "active"), kind
    assert exemption.exempt_reason("运输闭环", {}) == ""


def test_daily_report_transport_exempt_sheet():
    """日报「豁免备查」sheet 接受 kind="运输闭环"（沿用 6 sheets 逻辑，不新增空 sheet）。"""
    from openpyxl import load_workbook

    empty_alerts = {"todayNewOverdue": [], "ongoingOverdue": [], "warnings": []}
    base = dict(category="手机膜", computed_at="x", date_str="d",
                items=[], alerts=empty_alerts, gantts=[])
    content = reporting.daily_report(**base, exempts=[
        {"sku": "G700", "model": "M7", "kind": "运输闭环",
         "closure": "closed", "reason": "运输已闭环：已发货货件全部到货（免追踪）"}])
    wb = load_workbook(io.BytesIO(content))
    assert len(wb.sheetnames) == 8 and wb.sheetnames[-1] == "豁免备查"
    ws6 = wb["豁免备查"]
    assert ws6.cell(row=4, column=1).value == "G700"
    assert ws6.cell(row=4, column=3).value == "运输闭环"
