# -*- coding: utf-8 -*-
"""test_sku_hold.py — 「人工登记（免催办）」的单一真源与各处消费口径
======================================================================
背景（2026-09-28）：`G495`、`G524~G528` 这 6 个 SKU **业务上已决定不再走完流程**
（货早就在售），但钉钉单据还挂在半路 → 复盘报「缺节点」、加急预警把它当卡点催办
（实测停在 Part8/Part9，停滞 153~236 天）→ **多余异常**。

- G495：卡 Part8 两张「手机上架确认」（2026-03-25 起悬挂「审批中」），且空运/海运超底线（曾判 P0）；
- G524~G528：卡 Part9「新品环节检查」，自 2026-02 起停滞。（用户口径：Part8 那两张不计入时效。）

真源 = `资产文件\\SKU登记表.xlsx` 的 **`人工登记`** 子表（`SKU|说明|登记日期`）
  → MySQL `sku_hold`（`etl/products_loader.py` 的 `import_registry()` 导入，全量覆盖）
  → `app/sku_hold.py`（唯一读写入口，带 DB 指纹缓存）

⚠️ 与另外两条「不催办」通道**互相独立**，不能互相覆盖：
  · 人工登记（本模块）= 业务登记「不再走完」，**没有承接号**（人工决策，最先判）；
  · 换号（`sku_alias.py`）= 旧号停下、**新号承接**；
  · 末端终止 = **数据推断**（末尾 Part 全「终止」且无在跑单据）。
判定次序：**人工登记 > 换号 > 末端终止 > 催办**。

本文件覆盖三层：
  ① 纯逻辑：子表解析（表头容错/缺表不抛）+ 业务校验（空值/重复/不在清单）
  ② 跨实现对齐：后端 `sku_hold.read_hold_from_wb` ↔ 离线 `route_check_sys.read_hold_map`
  ③ 集成：真数据下 `holdRows` 与催办/作废/换号四类**互斥**，review 侧 `noChase`/`hold` 生效

不变量：P0/P1/P2 与作废/换号判据**一行未改**；登记表为空时行为与改造前完全一致。
"""

import importlib.util
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]                       # …/数据后台系统
REPO = ROOT.parent                                               # …/work-main
sys.path.insert(0, str(ROOT / "backend"))

from app import sku_hold                                         # noqa: E402
from app.routers import alerts                                   # noqa: E402

OFFLINE_SCRIPT = REPO / "流程复盘" / "route_check_sys.py"
# 真源 = 独立登记表（2026-09-30 从商品资料.xlsx 迁出）
REAL_XLSX = Path(r"C:\Users\dell\Desktop\work-main\资产文件\SKU登记表.xlsx")

HDR = ("SKU", "说明", "登记日期")


def _wb(rows, header=HDR, prelude=()):
    """造一个只含『人工登记』子表的工作簿，供 read_hold_from_wb 直接消费。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sku_hold.HOLD_SHEET
    for line in prelude:
        ws.append(list(line))
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    return wb


# ---------------------------------------------------------------------------
# ① 纯逻辑：子表解析
# ---------------------------------------------------------------------------
def test_read_hold_from_wb_basic():
    """正常两行 → 结构清洗（去空白、SKU 转大写），说明/日期原样带走。"""
    wb = _wb([("G495", "业务上不需走完", "2026-09-28"),
              (" g524 ", "老单停半路", "2026-09-28")])
    rows, notes = sku_hold.read_hold_from_wb(wb)
    assert [r["sku"] for r in rows] == ["G495", "G524"]
    assert rows[0]["reason"] == "业务上不需走完"
    assert rows[0]["registeredAt"] == "2026-09-28"
    assert notes == []


def test_read_hold_missing_sheet_returns_note_not_raise():
    """没有子表 → ([], [提示])，商品资料仍能正常导入（绝不抛）。"""
    wb = Workbook()
    wb.active.title = "商品资料"
    rows, notes = sku_hold.read_hold_from_wb(wb)
    assert rows == []
    assert notes and sku_hold.HOLD_SHEET in notes[0]


def test_header_tolerates_fullwidth_space_and_case():
    """表头含全角空格/不同写法 → 仍能按包含匹配定位列。"""
    wb = _wb([("G495", "r", "2026-09-28")],
             header=(" SKU\u3000", "豁免 说明", "登记 日期"))
    rows, _ = sku_hold.read_hold_from_wb(wb)
    assert rows and rows[0]["sku"] == "G495"
    assert rows[0]["reason"] == "r" and rows[0]["registeredAt"] == "2026-09-28"


def test_header_found_after_prelude_rows():
    """表头前有说明行（手工习惯）→ 仍能在前 6 行内找到表头。"""
    wb = _wb([("G495", "r", "2026-09-28")],
             prelude=[("本表登记业务上已不需走完流程的 SKU", None, None),
                      (None, None, None)])
    rows, _ = sku_hold.read_hold_from_wb(wb)
    assert rows and rows[0]["sku"] == "G495"


def test_header_not_found_within_scan_window():
    """表头超出行扫描窗口 → 返回空 + 明确告警（不是静默丢掉）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sku_hold.HOLD_SHEET
    for i in range(sku_hold.MAX_HDR_SCAN + 1):
        ws.append([f"说明{i}", "", ""])
    ws.append(list(HDR))
    ws.append(["G495", "r", "2026-09-28"])
    rows, notes = sku_hold.read_hold_from_wb(wb)
    assert rows == [] and notes and "未找到" in notes[0]


def test_blank_rows_skipped():
    wb = _wb([("G495", "r", ""), (None, None, None), ("", "", "")])
    rows, _ = sku_hold.read_hold_from_wb(wb)
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# ① 纯逻辑：业务校验
# ---------------------------------------------------------------------------
def _row(sku, reason=""):
    return {"sku": sku, "reason": reason, "registeredAt": ""}


def test_validate_rejects_empty_sku():
    clean, notes = sku_hold.validate_hold_rows([_row(""), _row("G495")])
    assert [r["sku"] for r in clean] == ["G495"]
    assert any("缺 SKU" in n for n in notes)


def test_validate_duplicate_keeps_last():
    clean, notes = sku_hold.validate_hold_rows(
        [_row("G495", "旧说法"), _row("G495", "新说法")])
    assert len(clean) == 1 and clean[0]["reason"] == "新说法"
    assert any("重复登记" in n for n in notes)


def test_validate_unknown_sku_warns_but_keeps():
    """SKU 不在商品资料清单里 → 只告警，不拦（可能只是还没录进商品资料）。"""
    clean, notes = sku_hold.validate_hold_rows(
        [_row("G999")], known_skus=["G495", "G524"])
    assert len(clean) == 1
    assert any("不在商品资料 SKU 清单" in n for n in notes)


def test_validate_suspicious_format_warns():
    clean, notes = sku_hold.validate_hold_rows([_row("整句当编码用")])
    assert len(clean) == 1
    assert any("格式可疑" in n for n in notes)


def test_validate_ok_has_no_notes():
    clean, notes = sku_hold.validate_hold_rows(
        [_row("G495")], known_skus=["G495"])
    assert len(clean) == 1 and notes == []


# ---------------------------------------------------------------------------
# ② 跨实现对齐：后端 ↔ 离线 route_check_sys
# ---------------------------------------------------------------------------
def _load_offline():
    if not OFFLINE_SCRIPT.exists():
        raise FileNotFoundError(f"离线脚本不存在: {OFFLINE_SCRIPT}")
    sys.path.insert(0, str(OFFLINE_SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("route_check_sys_t14", OFFLINE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cross_impl_same_map_on_synthetic(tmp_path):
    """同一份（临时）xlsx：两个读法必须得到完全相同的映射。"""
    try:
        offline = _load_offline()
    except Exception as exc:                                   # noqa: BLE001
        pytest.skip(f"离线脚本不可加载（缺 pandas/openpyxl 等）: {exc}")

    p = tmp_path / "商品资料_临时.xlsx"
    wb = _wb([("G495", "业务上不需走完", "2026-09-28"),
              ("G524", "老单停半路", "2026-09-28"),
              ("G495", "重复登记", "2026-09-28")])
    wb.save(p)

    be_rows, _ = sku_hold.read_hold_sheet(p)
    be = {r["sku"]: r["reason"] for r in be_rows}
    off = {k: v["说明"] for k, v in offline.read_hold_map(p).items()}
    assert be == off, f"后端 {be} ≠ 离线 {off}"


def test_cross_impl_ignores_title_row(tmp_path):
    """手工表标题行（含『SKU』『登记』字样）不得被误当表头 —— 两侧必须同判（都跳过）。

    手工维护的子表常见写法：第 1 行写标题「业务上不需走完流程的 SKU 登记表」，
    第 2 行留空，第 3 行才是 `SKU|说明|登记日期`。表头按**包含**匹配会把标题行认成
    表头（『SKU』『登记』都在标题里）→ 会静默吞掉整张表。后端与离线读法必须同时挡掉。
    """
    try:
        offline = _load_offline()
    except Exception as exc:                                   # noqa: BLE001
        pytest.skip(f"离线脚本不可加载（缺 pandas/openpyxl 等）: {exc}")

    p = tmp_path / "商品资料_标题行.xlsx"
    wb = _wb([("G495", "业务上不需走完", "2026-09-28")],
             prelude=[("业务上不需走完流程的 SKU 登记表", None, None),
                      (None, None, None)])
    wb.save(p)

    be_rows, _ = sku_hold.read_hold_sheet(p)
    be = {r["sku"]: r["reason"] for r in be_rows}
    off = {k: v["说明"] for k, v in offline.read_hold_map(p).items()}
    assert be == {"G495": "业务上不需走完"}, f"后端把标题行误判成表头：{be}"
    assert off == be, f"两侧不一致：后端 {be} / 离线 {off}"


def test_cross_impl_same_map_on_real_file():
    """真商品资料：两个读法逐键一致（含说明/登记日期）。"""
    if not REAL_XLSX.is_file():
        pytest.skip(f"商品资料不存在: {REAL_XLSX}")
    try:
        offline = _load_offline()
    except Exception as exc:                                   # noqa: BLE001
        pytest.skip(f"离线脚本不可加载: {exc}")

    be_rows, _ = sku_hold.read_hold_sheet(REAL_XLSX)
    be = {r["sku"]: (r["reason"], r["registeredAt"]) for r in be_rows}
    off = {k: (v["说明"], v["登记日期"])
           for k, v in offline.read_hold_map(REAL_XLSX).items()}
    assert be == off, f"真文件两侧读法不一致：\n后端 {be}\n离线 {off}"


def test_cross_impl_conclusion_matches():
    """结论文案两侧同源（后端常量 vs 离线函数）。"""
    try:
        offline = _load_offline()
    except Exception as exc:                                   # noqa: BLE001
        pytest.skip(f"离线脚本不可加载: {exc}")
    assert alerts.HOLD_NOTE == offline._hold_conclusion()


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


REAL_PAIR = {"G495", "G524", "G525", "G526", "G527", "G528"}


def test_integration_hold_rows_are_disjoint(real_alerts):
    """豁免 SKU 必须与催办清单、作废单、换号旧号**四类互斥**（消除多余异常的核心保证）。"""
    warned = {r["sku"] for r in real_alerts["rows"]}
    void = {r["sku"] for r in real_alerts["voidRows"]}
    alias = {r["sku"] for r in real_alerts["aliasRows"]}
    hold = {r["sku"] for r in real_alerts["holdRows"]}

    assert not (warned & hold), f"豁免 SKU 泄漏进催办清单: {sorted(warned & hold)}"
    assert not (void & hold), f"豁免 SKU 同时被当作废单: {sorted(void & hold)}"
    assert not (alias & hold), f"豁免 SKU 同时被当作换号旧号: {sorted(alias & hold)}"
    assert real_alerts["holdCount"] == len(real_alerts["holdRows"])


def test_integration_hold_rows_are_registered(real_alerts):
    """每条 holdRows 的 SKU 都必须真的登记在人工登记表里，且有结论/口径文案。"""
    registered = sku_hold.load_holds()
    for r in real_alerts["holdRows"]:
        assert r["sku"] in registered, f"{r['sku']} 在 holdRows 却不在人工登记表"
        assert r["reason"] == registered[r["sku"]]["reason"], f"{r['sku']} 说明不符"
        assert r["conclusion"], f"{r['sku']} 缺结论文案"
        assert r["note2"], "缺随行口径说明字号（note2）"


def test_integration_known_six(real_alerts):
    """真机登记：G495 + G524~G528 必须命中且带卡点/停滞（供人复核是不是真该豁免）。"""
    registered = sku_hold.load_holds()
    if not (REAL_PAIR & set(registered)):
        pytest.skip("人工登记表尚无本批次登记（本机未落盘）")
    by = {r["sku"]: r for r in real_alerts["holdRows"]}
    for sku in sorted(REAL_PAIR & set(registered)):
        assert sku in by, f"{sku} 未进 holdRows"
        assert by[sku]["stuckDays"] is not None, f"{sku} 缺停滞天数"
        assert by[sku]["lastDynamic"], f"{sku} 缺最近动态"


def test_integration_review_marks_nochase():
    """复盘侧：豁免 SKU noChase=True、hold 带说明、部门结论改写为「人工豁免·不催办」。"""
    from app.routers import review

    try:
        data = review.get_review_data("手机膜")
    except Exception as exc:                                   # noqa: BLE001
        pytest.skip(f"复盘数据不可用: {exc}")

    registered = sku_hold.load_holds()
    hits = [s for s in registered if s in data["skuDetails"]]
    if not hits:
        pytest.skip("本品类没有命中人工登记表的 SKU")

    for sku in hits:
        d = data["skuDetails"][sku]
        assert d["noChase"] is True, f"{sku} 应标 noChase"
        assert (d["hold"] or {}).get("reason"), f"{sku} 缺 hold.reason"
        assert "人工登记" in d["chaseReason"], f"{sku} chaseReason 不符：{d['chaseReason']}"
        assert d["aliasOut"] is None, f"{sku} 豁免时不应再挂换号（优先级更高）"
        if not d["valid"]:
            assert any("人工豁免" in str(v.get("note", ""))
                       for v in d["dept"].values()), f"{sku} 部门结论未改写"
    assert data["holds"]["outN"] >= len(hits)


def test_kpi_dict_exposes_all_referenced_keys():
    """KPI 快照必须真的带 `holdCount`（以及同批的 voidCount/aliasCount 等）。

    背景：`history._build_kpi` 里这个键曾因**同一文件并行编辑丢改动**而漏写，
    但 KPI 行与周报文案已经引用了它 → 页面/周报显示 `None` 却**静默不报错**。
    所以这里显式锁定「字典里必须有这些键」，而不是只测页面有没有那行。
    """
    from app.routers import history, review

    try:
        d = review.get_review_data("手机膜")
    except Exception as exc:                                   # noqa: BLE001
        pytest.skip(f"复盘数据不可用: {exc}")

    kpi = history._build_kpi(d, "手机膜")
    for key in ("voidCount", "aliasCount", "holdCount", "p0", "p1", "p2",
                "skuCount", "openSkus", "onTimeRate", "transportOverRate"):
        assert key in kpi, f"_build_kpi 未暴露 {key}：现有键 {sorted(kpi)}"

    assert kpi["holdCount"] == alerts.compute_alerts("手机膜")["holdCount"], \
        "KPI.holdCount 与 alerts.holdCount 不同源"


def test_no_hold_registered_means_unchanged_behavior(monkeypatch):
    """不变量：人工登记表为空时，alerts 不产生任何 holdRows（行为与改造前一致）。"""
    import app.sku_hold as sh

    monkeypatch.setattr(sh, "load_holds", lambda force=False: {})
    try:
        res = alerts.compute_alerts("手机膜")
    except Exception as exc:                                   # noqa: BLE001
        pytest.skip(f"真实数据不可用: {exc}")
    if not res.get("totalSkus"):
        pytest.skip("无批次数据")
    assert res["holdRows"] == [] and res["holdCount"] == 0
