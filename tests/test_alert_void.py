# -*- coding: utf-8 -*-
"""test_alert_void.py — 加急预警的「末端终止（作废单）」判定
=============================================================
背景（2026-09-28 口径修正）：「终止」此前不在 `OK_STATUS` 里 → 被当成「未结束」，
卡点落到**那张已终止的单据**上、停滞天数从终止日算起（实测 G448 报「Part2 停滞
447 天」、501 报「Part2 停滞 340 天」），都进了催办清单 —— 误催办。

判据（② 与承接单识别在离线脚本有同源实现）：
  ① 该 SKU 没有任何「审批中」单据（流程真的停住了，不是在跑）
  ② 最后一个有单据的 Part 里，单据状态**全部**为「终止」
命中 → 不进 P0/P1/P2 催办，改列 `voidRows`（不催办、仅备查）。

真机证据（本文件 ③ 的集成测兜底）：
  · 501 → G501：Part2 终止于 10-23 14:46，G501 开单 14:48（差 2 分 47 秒）且已闭环
  · G448 → G481：Part2 终止于 07-08 09:28，G481 开单 09:30（差 1 分 49 秒）且已闭环

不变量：P0/P1/P2 的**判据本身一行未改**（只改了「谁进这张单」）；已闭环 SKU 照旧不列入。
"""

import importlib.util
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]                       # …/数据后台系统
REPO = ROOT.parent                                               # …/work-main
sys.path.insert(0, str(ROOT / "backend"))

from app.routers import alerts                                    # noqa: E402

OFFLINE_SCRIPT = REPO / "流程复盘" / "route_check_sys.py"


def _dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def _doc(status, create=None, update=None, finish=None):
    """后端形态的单据（alerts 只用到这 4 个键）。"""
    return {"status": status, "create": create, "update": update, "finish": finish}


def _be(parts, model="TestModel"):
    return {"model": model, "parts": parts}


def _row(status, create="", update="", finish=""):
    """离线形态的单据行（Excel 原始列名，dtype=str）。"""
    return {"审批状态": status, "创建时间": create,
            "更新时间": update, "完成时间": finish}


def _fmt16(v):
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d %H:%M")
    return str(v or "")[:16]


# ---------------------------------------------------------------------------
# ① 纯逻辑：后端判据
# ---------------------------------------------------------------------------
def test_void_tail_terminated_is_void():
    """Part1 已结束 + Part2 终止（无后续 Part）→ 作废，终止环节=Part2。"""
    data = _be({"Part1": [_doc("已结束", finish=_dt("2025-07-08 09:06:47"))],
                "Part2": [_doc("终止", finish=_dt("2025-07-08 09:28:12"))]})
    ok, part, at = alerts._void_info(data)
    assert ok is True
    assert part == "Part2"
    assert _fmt16(at) == "2025-07-08 09:28"


def test_void_all_terminated_single_part():
    """只有 Part1 且终止 → 作废。"""
    data = _be({"Part1": [_doc("终止", finish=_dt("2026-02-02 14:53:39"))]})
    ok, part, at = alerts._void_info(data)
    assert (ok, part) == (True, "Part1")


def test_void_false_when_any_running_doc():
    """更早的 Part 还有「审批中」单据 → 流程在跑，不作废。"""
    data = _be({"Part1": [_doc("审批中", create=_dt("2025-07-07 15:20:08"))],
                "Part2": [_doc("终止", finish=_dt("2025-07-08 09:28:12"))]})
    assert alerts._void_info(data)[0] is False


def test_void_false_when_tail_part_not_all_terminated():
    """末尾 Part 是「已结束 + 终止」混合 → 不作废（只是其中一张废单）。"""
    data = _be({"Part1": [_doc("已结束")],
                "Part2": [_doc("已结束"), _doc("终止")]})
    assert alerts._void_info(data)[0] is False


def test_void_false_when_tail_part_finished():
    """末尾 Part 已结束 → 走原来的未闭环判据，不作废。"""
    data = _be({"Part1": [_doc("已结束")],
                "Part2": [_doc("已结束")]})
    assert alerts._void_info(data)[0] is False


def test_void_false_when_no_docs():
    assert alerts._void_info(_be({}))[0] is False


def test_void_skips_running_check_on_later_part_only():
    """末尾 Part 之前全终止、但末尾 Part 本身还在跑 → 不作废。"""
    data = _be({"Part1": [_doc("终止")],
                "Part2": [_doc("审批中", create=_dt("2026-01-01 10:00:00"))]})
    assert alerts._void_info(data)[0] is False


def test_successor_found_when_reopened_later():
    by = {"501": _be({"Part1": [_doc("已结束", create=_dt("2025-10-16 17:50:37"))]},
                     model="Xiaomi Redmi Note 15 Pro 5G"),
          "G501": _be({"Part1": [_doc("已结束", create=_dt("2025-10-23 14:48:47"))]},
                      model="Xiaomi Redmi Note 15 Pro 5G")}
    succ = alerts._find_successor("501", "Xiaomi Redmi Note 15 Pro 5G",
                                  _dt("2025-10-23 14:46:00"), by, {"G501": True})
    assert succ == {"sku": "G501", "firstAt": "2025-10-23 14:48", "valid": True}
    assert alerts._successor_note(succ) == "已由 G501 于 2025-10-23 14:48 重开（承接单已闭环）"


def test_successor_none_when_no_later_doc():
    """同型号但开单更早（不是「重开」）→ 不认承接单。"""
    by = {"X": _be({"Part1": [_doc("终止", finish=_dt("2025-05-01 10:00:00"))]}, model="M"),
          "Y": _be({"Part1": [_doc("已结束", create=_dt("2025-04-01 10:00:00"))]}, model="M")}
    assert alerts._find_successor("X", "M", _dt("2025-05-01 10:00:00"), by, {"Y": True}) is None


def test_successor_none_for_other_model():
    by = {"X": _be({"Part1": [_doc("终止")]}, model="M"),
          "Y": _be({"Part1": [_doc("已结束", create=_dt("2026-01-01 10:00:00"))]}, model="Z")}
    assert alerts._find_successor("X", "M", _dt("2025-05-01 10:00:00"), by, {}) is None


def test_successor_note_without_successor():
    assert alerts._successor_note(None).startswith("无同型号承接单")


def test_successor_picks_earliest_of_many():
    by = {"X": _be({"Part1": [_doc("终止")]}, model="M"),
          "Y2": _be({"Part1": [_doc("已结束", create=_dt("2026-03-01 10:00:00"))]}, model="M"),
          "Y1": _be({"Part1": [_doc("已结束", create=_dt("2026-02-01 10:00:00"))]}, model="M")}
    succ = alerts._find_successor("X", "M", _dt("2026-01-01 10:00:00"), by, {})
    assert succ["sku"] == "Y1"


# ---------------------------------------------------------------------------
# ② 跨实现对齐：后端 _void_info ↔ 离线 route_check_sys.void_of
# ---------------------------------------------------------------------------
def _load_offline():
    """加载离线脚本（依赖 pandas/openpyxl；缺依赖时由调用方 skip）。"""
    if not OFFLINE_SCRIPT.exists():
        raise FileNotFoundError(f"离线脚本不存在: {OFFLINE_SCRIPT}")
    sys.path.insert(0, str(OFFLINE_SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("route_check_sys_t12", OFFLINE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# (说明, 后端 parts, 离线 parts, 期望 void, 期望 part)
_PARITY_CASES = [
    ("末端终止", {"Part1": [_doc("已结束", finish=_dt("2025-07-08 09:06:47"))],
                "Part2": [_doc("终止", finish=_dt("2025-07-08 09:28:12"))]},
     {"Part1": [_row("已结束", finish="2025-07-08 09:06:47")],
      "Part2": [_row("终止", finish="2025-07-08 09:28:12")]}, True, "Part2"),
    ("仅Part1终止", {"Part1": [_doc("终止", finish=_dt("2026-02-02 14:53:39"))]},
     {"Part1": [_row("终止", finish="2026-02-02 14:53:39")]}, True, "Part1"),
    ("尾部仍在跑", {"Part1": [_doc("已结束")],
                  "Part2": [_doc("审批中", create=_dt("2026-01-01 10:00:00"))]},
     {"Part1": [_row("已结束", finish="2025-01-01 10:00:00")],
      "Part2": [_row("审批中", create="2026-01-01 10:00:00")]}, False, None),
    ("更早Part在跑", {"Part1": [_doc("审批中", create=_dt("2025-07-07 15:20:08"))],
                    "Part2": [_doc("终止", finish=_dt("2025-07-08 09:28:12"))]},
     {"Part1": [_row("审批中", create="2025-07-07 15:20:08")],
      "Part2": [_row("终止", finish="2025-07-08 09:28:12")]}, False, None),
    ("尾部混合", {"Part1": [_doc("已结束")], "Part2": [_doc("已结束"), _doc("终止")]},
     {"Part1": [_row("已结束", finish="2025-01-01 10:00:00")],
      "Part2": [_row("已结束", finish="2025-02-01 10:00:00"),
                _row("终止", finish="2025-03-01 10:00:00")]}, False, None),
    ("无单据", {}, {}, False, None),
]


@pytest.mark.parametrize("name,be_parts,off_parts,want_void,want_part",
                         _PARITY_CASES, ids=[c[0] for c in _PARITY_CASES])
def test_cross_impl_void_verdict_matches(name, be_parts, off_parts, want_void, want_part):
    """同一场景两侧判定必须一致（含终止环节与终止时刻，分钟精度）。"""
    try:
        offline = _load_offline()
    except Exception as exc:                                   # noqa: BLE001
        pytest.skip(f"离线脚本不可加载（缺 pandas/openpyxl 等）: {exc}")

    be = alerts._void_info(_be(be_parts))
    off = offline.void_of(off_parts)
    assert be[0] == want_void == off[0], f"{name}: 判定不一致 be={be[0]} off={off[0]}"
    assert be[1] == want_part and off[1] == want_part, f"{name}: 终止环节不一致"
    assert _fmt16(be[2]) == str(off[2] or "")[:16], (
        f"{name}: 终止时刻不一致 be={_fmt16(be[2])} off={off[2]}")


def test_cross_impl_successor_matches():
    """承接单识别两侧同结果（含「分钟精度」的显示串）。"""
    try:
        offline = _load_offline()
    except Exception as exc:                                   # noqa: BLE001
        pytest.skip(f"离线脚本不可加载: {exc}")

    model = "Xiaomi Redmi Note 15 Pro 5G"
    at = "2025-10-23 14:46:00"
    be_by = {"501": _be({"Part1": [_doc("终止", finish=_dt(at))]}, model=model),
             "G501": _be({"Part1": [_doc("已结束", create=_dt("2025-10-23 14:48:47"))]},
                         model=model)}
    off_index = [{"SKU": "501", "型号": model, "首个单据": "2025-10-16 17:50:37",
                  "有效": False},
                 {"SKU": "G501", "型号": model, "首个单据": "2025-10-23 14:48:47",
                  "有效": True}]

    be_succ = alerts._find_successor("501", model, _dt(at), be_by, {"G501": True})
    off_succ = offline.successor_of("501", model, at, off_index)
    assert be_succ is not None and off_succ is not None
    assert be_succ["sku"] == off_succ["SKU"] == "G501"
    assert be_succ["firstAt"] == off_succ["首个单据"] == "2025-10-23 14:48"
    assert alerts._successor_note(be_succ) == offline.successor_note(off_succ)


# ---------------------------------------------------------------------------
# ③ 集成：真数据（未导入时 skip，只读）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def real_alerts():
    try:
        res = alerts.compute_alerts("手机膜")
    except Exception as exc:                                   # noqa: BLE001
        pytest.skip(f"真实数据不可用: {exc}")
    if not res.get("totalSkus"):
        pytest.skip("无批次数据")
    return res


def test_integration_void_rows_are_self_consistent(real_alerts):
    """每条 voidRows 都必须真的满足判据，且与催办清单互斥。"""
    by = alerts._load_all_docs("手机膜")
    warned = {r["sku"] for r in real_alerts["rows"]}
    void = {r["sku"] for r in real_alerts["voidRows"]}

    assert not (warned & void), f"作废单泄漏进催办清单: {sorted(warned & void)}"
    for r in real_alerts["voidRows"]:
        ok, part, _at = alerts._void_info(by[r["sku"]])
        assert ok is True, f"{r['sku']} 在 voidRows 但不满足判据"
        assert part == r["part"], f"{r['sku']} 终止环节不符"
        assert r["successorNote"], f"{r['sku']} 缺结论文案"

    # 反向：催办清单里不该有被判为作废的
    for r in real_alerts["rows"]:
        assert alerts._void_info(by[r["sku"]])[0] is False, (
            f"{r['sku']} 应属作废却仍在催办")


def test_integration_counts_add_up(real_alerts):
    """计数口径：催办 = P0+P1+P2；作废单另计，不混入 alertCount。"""
    res = real_alerts
    assert res["alertCount"] == res["p0"] + res["p1"] + res["p2"]
    assert res["voidCount"] == len(res["voidRows"])
