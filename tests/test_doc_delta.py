# -*- coding: utf-8 -*-
"""test_doc_delta.py — 未闭环 SKU 单据增量追踪模型（T10）。

三层：
  1. 纯函数 —— 单据键 / 节点指纹 / 三类增量的 diff（不碰文件、不碰 DB，任何环境必跑）；
  2. 状态机 —— 基线建立、指纹幂等、suspect 标记、保留期裁剪（状态文件指到 tmp_path）；
  3. 集成 —— 真实批次数据下 build_docs 与复盘未闭环集合一致；无数据自动 skip。

口径锚点（backend/app/doc_delta_service.py 头部）：
  · 单据键 = SKU|Part|审批编号（**绝不用 part_doc.id**，重导入会整批重建）
  · 新单据的动作**不**计入 newNodes（否则一张新单被数成 1 单 + N 动作）
  · 首次拍快照只建基线、不产生事件（否则 504 张单会一次性全报成"新增"）
"""

import io
from datetime import datetime, timedelta

import pytest

from app import doc_delta_service as svc


# ---------------------------------------------------------------------------
# 1. 键与指纹
# ---------------------------------------------------------------------------
def test_doc_key_prefers_approval_no():
    assert svc.doc_key("G506", "Part6", "20250722155730", None) == \
        "G506|Part6|20250722155730"


def test_doc_key_falls_back_to_create_time():
    """审批编号缺失时用创建时间兜底 —— 仍比自增 id 稳定。"""
    k = svc.doc_key("G506", "Part6", "", datetime(2026, 9, 28, 9, 30, 0))
    assert k == "G506|Part6|c:2026-09-28 09:30:00"
    assert svc.split_key(k) == ("G506", "Part6", "c:2026-09-28 09:30:00")


def test_split_key_handles_sku_without_pipe():
    assert svc.split_key("G506|Part6|123") == ("G506", "Part6", "123")
    assert svc.split_key("G506|Part6|") == ("G506", "Part6", "")


def test_node_sig_keeps_three_segments():
    sig = svc.node_sig({"action": "产品营销素材加工",
                        "action_time": datetime(2025, 7, 22, 15, 57, 30),
                        "operator": "林宇炫"})
    assert sig == "产品营销素材加工|2025-07-22 15:57:30|林宇炫"
    assert svc.split_sig(sig) == ("产品营销素材加工", "2025-07-22 15:57:30", "林宇炫")


def test_node_sig_tolerates_missing_time_and_operator():
    """动作时间缺失（如抄送节点）也要保持三段式，不能把 operator 顶到时间位。"""
    sig = svc.node_sig({"action": "抄送", "action_time": None, "operator": ""})
    assert sig == "抄送||"
    assert svc.split_sig(sig) == ("抄送", "", "")


# ---------------------------------------------------------------------------
# 2. diff 的三类增量
# ---------------------------------------------------------------------------
def _doc(status="审批中", create="2026-09-01 09:00:00", nodes=()):
    return {"s": status, "c": create, "n": list(nodes)}


def test_diff_empty_when_nothing_changed():
    d = {"G506|Part1|1": _doc(nodes=["提交申请|2026-09-01 09:00:00|张三"])}
    out = svc.diff_docs(d, {k: {**v, "n": list(v["n"])} for k, v in d.items()})
    assert out["newDocs"] == [] and out["newNodes"] == []
    assert out["statusChanges"] == [] and out["newSkus"] == []
    assert out["closedSkus"] == []


def test_diff_new_doc_nodes_not_double_counted():
    """核心契约：新单据的动作不计入 newNodes（否则增量动作被灌水）。"""
    prev = {}
    cur = {"G506|Part6|9": _doc(nodes=["寄送拍摄团队|2026-09-01 10:00:00|李杰华",
                                       "素材接收|2026-09-01 10:30:00|李杰华",
                                       "产品营销素材加工|2026-09-01 11:00:00|林宇炫"])}
    out = svc.diff_docs(prev, cur)
    assert out["summary"]["newDocs"] == 1
    assert out["summary"]["newNodes"] == 0
    assert out["newDocs"][0]["nodeCount"] == 3, "新单据要带节点数，信息不能丢"


def test_diff_new_node_on_existing_doc():
    key = "G506|Part6|9"
    prev = {key: _doc(nodes=["素材接收|2026-09-01 10:30:00|李杰华"])}
    cur = {key: _doc(nodes=["素材接收|2026-09-01 10:30:00|李杰华",
                            "产品营销素材加工|2026-09-02 11:00:00|林宇炫"])}
    out = svc.diff_docs(prev, cur)
    assert out["summary"]["newDocs"] == 0
    assert out["summary"]["newNodes"] == 1
    assert out["newNodes"][0]["action"] == "产品营销素材加工"
    assert out["newNodes"][0]["operator"] == "林宇炫"
    assert out["newNodes"][0]["at"] == "2026-09-02 11:00:00"


def test_diff_detects_status_change():
    key = "G506|Part1|1"
    prev = {key: _doc(status="审批中")}
    cur = {key: _doc(status="已结束")}
    out = svc.diff_docs(prev, cur)
    assert out["summary"]["statusChanges"] == 1
    assert out["statusChanges"][0] == {"sku": "G506", "part": "Part1",
                                       "approvalNo": "1", "from": "审批中",
                                       "to": "已结束"}
    assert out["summary"]["newDocs"] == 0


def test_diff_new_and_closed_skus():
    prev = {"G1|Part1|1": _doc(), "G2|Part1|2": _doc()}
    cur = {"G2|Part1|2": _doc(), "G3|Part1|3": _doc()}
    out = svc.diff_docs(prev, cur)
    assert out["newSkus"] == ["G3"]
    assert out["closedSkus"] == ["G1"]
    assert out["summary"]["docCount"] == 2
    assert out["summary"]["skuCount"] == 2


def test_diff_reimport_same_approval_no_is_not_new():
    """重导入（DB 行被重建）不该产生增量：键里没有自增 id。"""
    a = {"G506|Part6|20250722155730": _doc()}
    b = {"G506|Part6|20250722155730": _doc()}
    out = svc.diff_docs(a, b)
    assert out["summary"]["newDocs"] == 0
    assert out["summary"]["newNodes"] == 0


# ---------------------------------------------------------------------------
# 3. 状态机：基线 / 幂等 / suspect / 保留期
# ---------------------------------------------------------------------------
@pytest.fixture()
def temp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "STATE_FILE", tmp_path / "doc_delta_state.json")
    return svc.STATE_FILE


def _fake_build(docs, skus):
    return lambda category=None, review_data=None: (docs, skus)


def test_baseline_records_no_event(temp_state, monkeypatch):
    """首次拍只建基线：不能让 504 张历史单据一次性变成「新增」。"""
    docs = {"G506|Part1|1": _doc(), "G507|Part1|2": _doc()}
    monkeypatch.setattr(svc, "build_docs", _fake_build(docs, {"G506", "G507"}))
    monkeypatch.setattr("app.routers.review._live_batch_ids", lambda cat=None: {1})

    r = svc.record_doc_snapshot("手机膜", fingerprint="fp1")
    assert r["recorded"] is True
    assert "基线" in r["reason"]
    assert svc._load()["events"] == []
    st = svc._load()["state"]["手机膜"]
    assert st["docCount"] == 2 and st["skuCount"] == 2


def test_same_fingerprint_is_idempotent(temp_state, monkeypatch):
    docs = {"G506|Part1|1": _doc()}
    monkeypatch.setattr(svc, "build_docs", _fake_build(docs, {"G506"}))
    monkeypatch.setattr("app.routers.review._live_batch_ids", lambda cat=None: {1})

    svc.record_doc_snapshot("手机膜", fingerprint="fp1")
    calls = []
    monkeypatch.setattr(svc, "build_docs",
                        lambda category=None, review_data=None: (
                            calls.append(1) or docs, {"G506"}))
    r = svc.record_doc_snapshot("手机膜", fingerprint="fp1")
    assert r["recorded"] is False
    assert calls == [], "指纹没变就不该再重算（省掉一次全量 review）"
    assert svc._load()["events"] == []


def test_increment_event_written_once(temp_state, monkeypatch):
    v1 = {"G506|Part1|1": _doc()}
    monkeypatch.setattr("app.routers.review._live_batch_ids", lambda cat=None: {1})
    monkeypatch.setattr(svc, "build_docs", _fake_build(v1, {"G506"}))
    svc.record_doc_snapshot("手机膜", fingerprint="fp1")

    v2 = {**v1, "G506|Part9|2": _doc(nodes=["提交申请|2026-09-28 09:00:00|张三"])}
    monkeypatch.setattr(svc, "build_docs", _fake_build(v2, {"G506"}))
    r = svc.record_doc_snapshot("手机膜", fingerprint="fp2")
    assert r["recorded"] is True and r["event"]["summary"]["newDocs"] == 1

    ev = svc._load()["events"]
    assert len(ev) == 1 and ev[0]["fingerprint"] == "fp2"
    assert ev[0]["baseline"] is False


def test_suspect_flag_on_wholesale_baseline_change(temp_state, monkeypatch):
    """整批单据被替换（号段变了）要打 suspect，而不是静默当成真增量。"""
    monkeypatch.setattr("app.routers.review._live_batch_ids", lambda cat=None: {1})
    v1 = {f"G{i}|Part1|{i}": _doc() for i in range(60)}
    monkeypatch.setattr(svc, "build_docs", _fake_build(v1, {f"G{i}" for i in range(60)}))
    svc.record_doc_snapshot("手机膜", fingerprint="fp1")

    v2 = {f"N{i}|Part1|9{i}": _doc() for i in range(60)}
    monkeypatch.setattr(svc, "build_docs", _fake_build(v2, {f"N{i}" for i in range(60)}))
    r = svc.record_doc_snapshot("手机膜", fingerprint="fp2")
    assert r["event"]["suspect"] is True


def test_small_change_is_not_suspect(temp_state, monkeypatch):
    monkeypatch.setattr("app.routers.review._live_batch_ids", lambda cat=None: {1})
    v1 = {f"G{i}|Part1|{i}": _doc() for i in range(60)}
    monkeypatch.setattr(svc, "build_docs", _fake_build(v1, {f"G{i}" for i in range(60)}))
    svc.record_doc_snapshot("手机膜", fingerprint="fp1")

    v2 = dict(v1)
    v2["G0|Part9|99"] = _doc()
    monkeypatch.setattr(svc, "build_docs", _fake_build(v2, {f"G{i}" for i in range(60)}))
    r = svc.record_doc_snapshot("手机膜", fingerprint="fp2")
    assert r["event"]["suspect"] is False


def test_no_live_batch_skips_snapshot(temp_state, monkeypatch):
    """批次目录被移走时不能拍空基线 —— 否则下一轮会把全部单据报成新增。"""
    monkeypatch.setattr("app.routers.review._live_batch_ids", lambda cat=None: set())
    monkeypatch.setattr(svc, "build_docs", _fake_build({}, set()))
    r = svc.record_doc_snapshot("手机膜", fingerprint="fp1")
    assert r["recorded"] is False
    assert "无活批次" in r["reason"]
    assert svc._load()["state"] == {}


def test_prune_events_by_retention_and_cap(monkeypatch):
    monkeypatch.setattr(svc, "EVENT_RETENTION_DAYS", 10)
    monkeypatch.setattr(svc, "EVENT_MAX", 3)
    old = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    new = datetime.now().strftime("%Y-%m-%d")
    events = [{"date": old}] + [{"date": new, "i": i} for i in range(5)]
    kept = svc._prune(events)
    assert len(kept) == 3
    assert all(e["date"] == new for e in kept)
    assert [e["i"] for e in kept] == [2, 3, 4], "保留最新"


# ---------------------------------------------------------------------------
# 4. 查询与导出
# ---------------------------------------------------------------------------
def test_window_rows_moves_action_time_to_actionAt(temp_state):
    """节点行自带的 at 是动作时间，被事件时刻覆盖前必须挪到 actionAt。"""
    svc._save({
        "version": 1, "state": {},
        "events": [{
            "date": "2026-09-28", "at": "2026-09-28 14:55:00",
            "category": "手机膜", "baseline": False,
            "summary": {"newDocs": 0, "newNodes": 1, "statusChanges": 0},
            "newDocs": [], "newNodes": [{"sku": "G506", "part": "Part6",
                                         "approvalNo": "9",
                                         "action": "素材接收",
                                         "at": "2026-09-28 10:30:00",
                                         "operator": "李杰华"}],
            "statusChanges": [], "newSkus": [], "closedSkus": [],
        }]})
    out = svc.window_rows("手机膜", days=30)
    n = out["newNodes"][0]
    assert n["actionAt"] == "2026-09-28 10:30:00", "动作时间不能被事件时刻吃掉"
    assert n["at"] == "2026-09-28 14:55:00", "at 统一表示事件时刻"


def test_doc_delta_shape_when_no_state(temp_state):
    d = svc.doc_delta("手机膜", days=30)
    assert d["exists"] is False
    assert d["last"] is None
    assert d["state"] is None
    assert d["window"]["summary"]["newDocs"] == 0
    assert d["window"]["byDay"] == []
    assert d["events"] == []
    assert d["scope"].startswith("未闭环 SKU")
    assert d["settings"]["retentionDays"] == svc.EVENT_RETENTION_DAYS


def test_doc_delta_aggregates_window(temp_state):
    """窗口聚合必须自洽：汇总 = 各日之和 = 各 SKU 之和。"""
    ev1_nodes = [{"sku": "G506", "part": "Part3", "approvalNo": "1",
                  "action": "本地仓到货检查", "at": "2026-09-27 09:00:00",
                  "operator": "仓管"}]
    ev2_nodes = [{"sku": "G506", "part": "Part6", "approvalNo": "1",
                  "action": "素材接收", "at": "2026-09-28 10:30:00",
                  "operator": "李杰华"},
                 {"sku": "G506", "part": "Part7", "approvalNo": "1",
                  "action": "上架", "at": "2026-09-28 11:00:00",
                  "operator": "运营"}]
    svc._save({
        "version": 1,
        "state": {"手机膜": {"at": "2026-09-28 14:55:00", "fingerprint": "f",
                          "skuCount": 1, "docCount": 1,
                          "docs": {"G506|Part1|1": _doc()}}},
        "events": [
            {"date": "2026-09-27", "at": "2026-09-27 09:25:00", "category": "手机膜",
             "summary": {"newDocs": 2, "newNodes": 1, "statusChanges": 0,
                         "newSkus": 0, "closedSkus": 0},
             "newDocs": [{"sku": "G506", "part": "Part1", "approvalNo": "1"},
                         {"sku": "G506", "part": "Part2", "approvalNo": "2"}],
             "newNodes": ev1_nodes, "statusChanges": [],
             "newSkus": [], "closedSkus": []},
            {"date": "2026-09-28", "at": "2026-09-28 14:55:00", "category": "手机膜",
             "summary": {"newDocs": 1, "newNodes": 2, "statusChanges": 0,
                         "newSkus": 1, "closedSkus": 0},
             "newDocs": [{"sku": "G507", "part": "Part1", "approvalNo": "3"}],
             "newNodes": ev2_nodes, "statusChanges": [],
             "newSkus": ["G507"], "closedSkus": []},
        ]})
    d = svc.doc_delta("手机膜", days=30)
    s = d["window"]["summary"]
    assert s["newDocs"] == 3 and s["newNodes"] == 3 and s["snapshots"] == 2
    assert [r["date"] for r in d["window"]["byDay"]] == ["2026-09-27", "2026-09-28"]
    assert d["last"]["at"] == "2026-09-28 14:55:00"
    by = {r["sku"]: r for r in d["window"]["bySku"]}
    assert by["G506"]["newDocs"] == 2 and by["G506"]["newNodes"] == 3
    assert by["G507"]["newDocs"] == 1 and by["G507"]["newNodes"] == 0
    # 自洽性：SKU 维度求和必须等于汇总（两个口径不能各算各的）
    assert sum(r["newDocs"] for r in by.values()) == s["newDocs"]
    assert sum(r["newNodes"] for r in by.values()) == s["newNodes"]
    assert sum(r["newDocs"] for r in d["window"]["byDay"]) == s["newDocs"]

    only = svc.doc_delta("手机膜", days=30, sku="G507")
    assert [r["sku"] for r in only["window"]["bySku"]] == ["G507"]
    assert len(only["last"]["newDocs"]) == 1
    assert only["last"]["newNodes"] == []


def test_report_xlsx_five_sheets():
    from openpyxl import load_workbook

    from app.reporting import doc_delta_report

    delta = {"scope": svc.SCOPE_DESC,
             "window": {"days": 30, "since": "2026-08-29",
                        "summary": {"newDocs": 1, "newNodes": 1, "statusChanges": 0,
                                    "newSkus": 0, "closedSkus": 0, "snapshots": 2},
                        "bySku": [{"sku": "G506", "model": "X", "devType": "常规开发",
                                   "reason": "缺节点", "docCount": 5, "newDocs": 1,
                                   "newNodes": 1, "statusChanges": 0,
                                   "lastChangeAt": "2026-09-28 14:55:00"}]},
             "last": {}}
    rows = {"newDocs": [{"sku": "G506", "part": "Part6", "approvalNo": "9",
                         "status": "审批中", "createAt": "2026-09-28 11:00:00",
                         "nodeCount": 2, "at": "2026-09-28 14:55:00"}],
            "newNodes": [{"sku": "G506", "part": "Part3", "approvalNo": "1",
                          "action": "本地仓到货检查", "actionAt": "2026-09-28 09:00:00",
                          "operator": "仓管", "at": "2026-09-28 14:55:00"}],
            "statusChanges": [{"sku": "G506", "part": "Part1", "approvalNo": "1",
                               "from": "审批中", "to": "已结束",
                               "at": "2026-09-28 14:55:00"}],
            "newSkus": [], "closedSkus": []}
    blob = doc_delta_report(delta, rows, category="手机膜")
    wb = load_workbook(io.BytesIO(blob))
    assert wb.sheetnames == ["新增单据", "单据新动作", "状态变化",
                             "未闭环SKU汇总", "口径说明"]
    assert wb["新增单据"]["A5"].value == "SKU"
    assert wb["新增单据"]["A6"].value == "G506"
    # 手机壳要带待校准提示
    blob2 = doc_delta_report(delta, rows, category="手机壳", provisional=True,
                             targets_source="继承手机膜名义值")
    wb2 = load_workbook(io.BytesIO(blob2))
    assert any("待校准" in str(c.value or "")
               for row in wb2["口径说明"].iter_rows() for c in row)


# ---------------------------------------------------------------------------
# 5. 集成（真实数据；无批次则 skip）
# ---------------------------------------------------------------------------
def _has_data():
    try:
        from app.config import list_batches
        return bool(list_batches("手机膜"))
    except Exception:                                    # noqa: BLE001
        return False


@pytest.mark.skipif(not _has_data(), reason="无手机膜批次数据（未导入）")
def test_build_docs_matches_review_open_skus():
    """真实数据下：单据表的 SKU 集合必须与复盘未闭环集合逐一致，键里带审批编号。"""
    from app.routers.review import get_review_data

    docs, open_skus = svc.build_docs("手机膜")
    rows = get_review_data("手机膜")["rows"]
    expect = {r["sku"] for r in rows if not r["valid"]}
    assert open_skus == expect
    if docs:
        got = {svc.split_key(k)[0] for k in docs}
        assert got == expect, "单据表里不能混进已闭环 SKU"
        assert all(k.count("|") >= 2 for k in docs)
        sample = next(iter(docs.values()))
        assert set(sample) == {"s", "c", "n"}
