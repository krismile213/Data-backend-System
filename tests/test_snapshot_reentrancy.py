# -*- coding: utf-8 -*-
"""
test_snapshot_reentrancy.py — KPI 快照的「同线程重入」防死锁回归
=================================================================
背景（2026-09-22 实测复现，曾在真机上把整个后端永久卡死）：

    get_review_data → record_snapshot → _build_kpi
      → compute_transport / _get_alerts_cached / compute_scenarios
      → get_review_data（同线程再次进入）

只要**嵌套的那次 get_review_data 未命中缓存**，就会在同一线程再次调用
record_snapshot；而 record_snapshot 当时用的是非重入 Lock 且已在锁内 ——
本线程自己撞死自己的锁，`history._lock` 永不释放 → 此后所有 /api/history、
所有复盘重算全部挂死。

触发条件正是「按品类分规则」引入的：单槽缓存下，计算手机壳时嵌套进来的
默认品类（手机膜）必然未命中。因此这里守住四条：

  ① record_snapshot 同线程重入不得死锁（10s 内必须返回）；
  ② 重入时不得重复写快照（跳过即可，之后由 /api/history 自愈补拍）；
  ③ review 缓存与 alerts 缓存都按品类分槽：切换品类不互相打掉、不重复计算；
  ④ 快照的**指纹 / 数据 / 品类标记三者必须同品类**（跨品类串味是静默错误）。

测试完全离线：compute_review / 指纹函数 / 规则版本 / 状态文件全部替换。
"""

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers import history, review          # noqa: E402

TIMEOUT = 10.0


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """把复盘缓存 / 快照文件 / 计算入口全部换成本地可控的替身。"""
    # 快照落盘改到 tmp，绝不能碰 backend/state/kpi_snapshots.json
    monkeypatch.setattr(history, "_STATE_FILE", tmp_path / "kpi_snapshots.json")
    # 锁按**原类型**新建（RLock 仍是 RLock），保证用例之间互不污染
    monkeypatch.setattr(history, "_lock", type(history._lock)())
    monkeypatch.setattr(history, "_reentry", threading.local())
    monkeypatch.setattr(review, "_cache_lock", type(review._cache_lock)())
    monkeypatch.setattr(review, "_cache", {"fp": None, "data": None})
    monkeypatch.setattr(review, "_cache_by_cat", {})

    calls = []

    def fake_compute_review(cat):
        calls.append(cat)
        return {"rows": [], "segStats": [], "cat": cat}

    monkeypatch.setattr(review, "compute_review", fake_compute_review)
    # ⚠️ 必须接受位置参数：_db_fingerprint 自 2026-09-22 起按品类取指纹，
    #    fingerprint_for 会以 _db_fingerprint(cat) 调用它。
    monkeypatch.setattr(review, "_db_fingerprint", lambda *a, **k: "db")
    monkeypatch.setattr(review, "_product_fingerprint", lambda: "prod")
    monkeypatch.setattr("app.rule_service.get_rules", lambda: (None, 1))
    return calls


def _run_in_thread(fn):
    """跑 fn 并返回 (是否在超时内结束, 异常)。死锁时返回 (False, None)。"""
    box = {}

    def worker():
        try:
            fn()
            box["ok"] = True
        except BaseException as e:      # noqa: BLE001
            box["err"] = e

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(TIMEOUT)
    return (not t.is_alive()), box.get("err")


def test_reentrant_snapshot_does_not_deadlock(isolated, monkeypatch):
    """嵌套的 get_review_data 未命中缓存时，record_snapshot 必须不死锁。"""
    def fake_build_kpi(review_data, category=None):
        # 关键：清空缓存，强制嵌套那次 get_review_data 重算 →
        # 重算完成又会调 record_snapshot（同线程、锁已持有）
        review._cache_by_cat.clear()
        review.get_review_data()
        return {}

    monkeypatch.setattr(history, "_build_kpi", fake_build_kpi)

    finished, err = _run_in_thread(review.get_review_data)
    assert finished, "死锁：record_snapshot 同线程重入未被拦住（>10s 未返回）"
    assert err is None, f"重入路径抛异常: {err!r}"


def test_reentrant_snapshot_is_skipped_not_duplicated(isolated, monkeypatch):
    """重入那次必须直接返回，不能多写一张快照。"""
    seen = {"nested": None}

    def fake_build_kpi(review_data, category=None):
        review._cache_by_cat.clear()
        review.get_review_data()
        # 嵌套调用已发生，此时快照数应仍为 0（尚未 append）
        seen["nested"] = len(history._load()["snapshots"])
        return {}

    monkeypatch.setattr(history, "_build_kpi", fake_build_kpi)

    finished, err = _run_in_thread(lambda: review.get_review_data())
    assert finished and err is None
    snaps = history._load()["snapshots"]
    assert len(snaps) == 1, f"应只写 1 张快照，实际 {len(snaps)} 张"
    assert seen["nested"] == 0, "嵌套调用不得抢先写入快照"


def test_category_switch_keeps_both_cache_slots(isolated):
    """按品类分槽：手机膜与手机壳各自缓存，来回切换不重复计算。"""
    calls = isolated
    review.get_review_data()              # 默认 → 手机膜
    review.get_review_data("手机壳")
    review.get_review_data()              # 再要手机膜：应命中，不再计算
    review.get_review_data("手机壳")        # 再要手机壳：应命中
    assert calls.count("手机膜") == 1, f"手机膜被重复计算: {calls}"
    assert calls.count("手机壳") == 1, f"手机壳被重复计算: {calls}"


def test_fingerprint_is_per_category_not_mirror(isolated, monkeypatch):
    """指纹必须按**请求的品类**取，不能读"最近一次计算"的全进程镜像。"""
    monkeypatch.setattr(history, "_build_kpi", lambda rd, category=None: {})
    review.get_review_data("手机壳")           # 把镜像搅成手机壳
    assert history._current_fingerprint("手机膜").startswith("手机膜|")
    assert history._current_fingerprint("手机壳").startswith("手机壳|")
    assert history._current_fingerprint().startswith("手机膜|")   # 缺省=默认品类


def test_snapshot_does_not_cross_contaminate_categories(isolated, monkeypatch):
    """算过手机壳之后取默认品类快照：指纹与数据都必须是手机膜。"""
    monkeypatch.setattr(history, "_build_kpi",
                        lambda rd, category=None: {"cat": category})
    review.get_review_data("手机壳")
    snap = history.maybe_snapshot()            # 缺省品类
    assert snap["fingerprint"].startswith("手机膜|"), snap["fingerprint"]
    assert snap["category"] == "手机膜"
    assert snap["kpi"]["cat"] == "手机膜", "快照里的 KPI 来自错误品类"


def test_snapshot_per_category_when_scoped(isolated, monkeypatch):
    """显式指定品类取快照：指纹 / kpi / category 三者都要是该品类。

    （2026-09-22 起已放开品类作用域 —— 前提是 _build_kpi 的运输/预警/场景看板
    都按品类分发，本用例正是守住这个前提：_build_kpi 收到的 category 必须听话。）
    """
    got = {}

    def fake_build_kpi(rd, category=None):
        got["category"] = category
        return {"cat": category}

    monkeypatch.setattr(history, "_build_kpi", fake_build_kpi)
    review.get_review_data("手机壳")
    snap = history.maybe_snapshot("手机壳")
    assert snap["fingerprint"].startswith("手机壳|"), snap["fingerprint"]
    assert snap["category"] == "手机壳"
    assert got["category"] == "手机壳", "_build_kpi 未收到品类 → 看板会串味"
    assert snap["kpi"]["cat"] == "手机壳"

    # 两个品类各自一张快照，互不覆盖
    history.maybe_snapshot("手机膜")
    snaps = history._load()["snapshots"]
    assert len(snaps) == 2, f"两个品类应各留一张，实际 {len(snaps)} 张"
    heads = {s["fingerprint"].split("|")[0] for s in snaps}
    assert heads == {"手机膜", "手机壳"}


def test_history_endpoint_survives_category_switch(isolated, monkeypatch):
    """真按接口路径走一遍：先算手机壳，再请求 /api/history 的 maybe_snapshot。"""
    monkeypatch.setattr(history, "_build_kpi", lambda rd, category=None: {})
    finished, err = _run_in_thread(lambda: review.get_review_data("手机壳"))
    assert finished and err is None
    finished2, err2 = _run_in_thread(history.maybe_snapshot)
    assert finished2, "maybe_snapshot 在算过手机壳之后挂死"
    assert err2 is None, f"maybe_snapshot 抛异常: {err2!r}"
