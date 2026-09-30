# -*- coding: utf-8 -*-
"""test_sku_alias.py — 「换号（旧号 → 新号）」的单一真源与三处消费口径
=====================================================================
背景（2026-09-28）：G493/G494 在流程中途换了编码（G493→G510、G494→G533）。旧号停用后，
它的钉钉流程**永远停在中途** → 复盘报「缺节点」、加急预警把它当卡点催办 → **多余异常**。

为什么只认人工维护、不做自动推导（已实测，别再试）：
  ① 流程单据里没有结构化痕迹：G493/G494 自己的单据完全不提 G510/G533；
  ② 商品资料装配表的「共用<旧号>」是**刀模/神器共用**（全库 125 个 SKU 都有），推导必误伤；
  ③ `参考SKU` 列是「上一代同系列产品」（G593 参考 G511），语义不是换号。

真源 = `资产文件\\SKU登记表.xlsx` 的 **`SKU换号`** 子表
  → MySQL `sku_alias`（`etl/products_loader.py` 的 `import_registry()` 导入）
  → `app/sku_alias.py`（唯一读写入口，带 DB 指纹缓存）

本文件覆盖三层：
  ① 纯逻辑：子表解析（表头容错/缺表不抛）+ 业务校验（自指/空值/重复旧号/成环/新号不存在）
  ② 跨实现对齐：后端 `sku_alias.read_alias_from_wb` ↔ 离线 `route_check_sys.read_alias_map`
     （同源双实现，同一份 xlsx 必须读出完全相同的映射）
  ③ 集成：真数据下 `aliasRows` 与催办/作废清单**互斥**，且 review 侧 `noChase` 生效

不变量：P0/P1/P2、作废单判据**一行未改**；换号只在「命中登记表」时才介入，
登记表为空时行为与改造前完全一致（`load_aliases()` 返回 {}）。
"""

import importlib.util
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]                       # …/数据后台系统
REPO = ROOT.parent                                               # …/work-main
sys.path.insert(0, str(ROOT / "backend"))

from app import sku_alias                                        # noqa: E402
from app.routers import alerts                                   # noqa: E402

OFFLINE_SCRIPT = REPO / "流程复盘" / "route_check_sys.py"
# 真源 = 独立登记表（2026-09-30 从商品资料.xlsx 迁出）
REAL_XLSX = Path(r"C:\Users\dell\Desktop\work-main\资产文件\SKU登记表.xlsx")

HDR = ("旧SKU", "新SKU", "换号说明", "生效日期")


def _wb(rows, header=HDR, prelude=()):
    """造一个只含『SKU换号』子表的工作簿，供 read_alias_from_wb 直接消费。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sku_alias.ALIAS_SHEET
    for line in prelude:
        ws.append(list(line))
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    return wb


# ---------------------------------------------------------------------------
# ① 纯逻辑：子表解析
# ---------------------------------------------------------------------------
def test_read_alias_from_wb_basic():
    """正常两行 → 结构清洗（去空白、SKU 转大写），说明/日期原样带走。"""
    wb = _wb([("G493", "G510", "机型变更", "2025-11-19"),
              ("g494", " g533 ", "机型变更2", "2026-03-02")])
    rows, notes = sku_alias.read_alias_from_wb(wb)
    assert [r["old"] for r in rows] == ["G493", "G494"]
    assert [r["new"] for r in rows] == ["G510", "G533"]
    assert rows[0]["note"] == "机型变更" and rows[0]["effectiveAt"] == "2025-11-19"
    assert notes == []


def test_read_alias_missing_sheet_returns_note_not_raise():
    """没有子表 → ([], [提示])，商品资料仍能正常导入（绝不抛）。"""
    wb = Workbook()
    wb.active.title = "商品资料"
    rows, notes = sku_alias.read_alias_from_wb(wb)
    assert rows == []
    assert notes and sku_alias.ALIAS_SHEET in notes[0]


def test_header_tolerates_fullwidth_space_and_case():
    """表头含全角空格/不同写法 → 仍能按包含匹配定位列。"""
    wb = _wb([("G493", "G510", "n", "2025-11-19")],
             header=("旧 SKU\u3000", " 新SKU ", "换号 说明", "生效 日期"))
    rows, _ = sku_alias.read_alias_from_wb(wb)
    assert rows and rows[0]["old"] == "G493" and rows[0]["new"] == "G510"
    assert rows[0]["note"] == "n" and rows[0]["effectiveAt"] == "2025-11-19"


def test_header_found_after_prelude_rows():
    """表头前有说明行（手工习惯）→ 仍能在前 6 行内找到表头。"""
    wb = _wb([("G493", "G510", "n", "2025-11-19")],
             prelude=[("本表记录流程中途换编码的旧号→新号", None, None, None),
                      (None, None, None, None)])
    rows, _ = sku_alias.read_alias_from_wb(wb)
    assert rows and rows[0]["old"] == "G493"


def test_header_not_found_within_scan_window():
    """表头超出行扫描窗口 → 返回空 + 明确告警（不是静默丢掉）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sku_alias.ALIAS_SHEET
    for i in range(sku_alias.MAX_HDR_SCAN + 1):
        ws.append([f"说明{i}", "", "", ""])
    ws.append(list(HDR))
    ws.append(["G493", "G510", "n", "2025-11-19"])
    rows, notes = sku_alias.read_alias_from_wb(wb)
    assert rows == [] and notes and "未找到" in notes[0]


def test_blank_rows_skipped():
    wb = _wb([("G493", "G510", "n", ""), (None, None, None, None), ("", "", "", "")])
    rows, _ = sku_alias.read_alias_from_wb(wb)
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# ① 纯逻辑：业务校验
# ---------------------------------------------------------------------------
def _row(old, new, note=""):
    return {"old": old, "new": new, "note": note, "effectiveAt": ""}


def test_validate_rejects_self_and_empty():
    clean, notes = sku_alias.validate_alias_rows(
        [_row("G493", "G493"), _row("", "G510"), _row("G494", "")])
    assert clean == []
    assert len(notes) == 3


def test_validate_duplicate_old_keeps_last():
    clean, notes = sku_alias.validate_alias_rows(
        [_row("G493", "G510"), _row("G493", "G599")])
    assert len(clean) == 1 and clean[0]["new"] == "G599"
    assert any("重复登记" in n for n in notes)


def test_validate_cycle_detected():
    clean, notes = sku_alias.validate_alias_rows(
        [_row("A1", "B1"), _row("B1", "A1")])
    assert len(clean) == 2
    assert any("成环" in n for n in notes)


def test_validate_unknown_new_sku_warns_but_keeps():
    """新号不在商品资料 SKU 清单里 → 只告警，不拦（可能只是还没录进商品资料）。"""
    clean, notes = sku_alias.validate_alias_rows(
        [_row("G493", "G999")], known_skus=["G493", "G510"])
    assert len(clean) == 1
    assert any("不在商品资料 SKU 清单" in n for n in notes)


def test_validate_ok_has_no_notes():
    clean, notes = sku_alias.validate_alias_rows(
        [_row("G493", "G510")], known_skus=["G493", "G510"])
    assert len(clean) == 1 and notes == []


# ---------------------------------------------------------------------------
# ② 跨实现对齐：后端 ↔ 离线 route_check_sys
# ---------------------------------------------------------------------------
def _load_offline():
    if not OFFLINE_SCRIPT.exists():
        raise FileNotFoundError(f"离线脚本不存在: {OFFLINE_SCRIPT}")
    sys.path.insert(0, str(OFFLINE_SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("route_check_sys_t13", OFFLINE_SCRIPT)
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
    wb = _wb([("G493", "G510", "机型变更", "2025-11-19"),
              ("G494", "G533", "机型变更2", "2026-03-02"),
              ("G493", "G510", "重复登记", "2025-11-19")])
    wb.save(p)

    be_rows, _ = sku_alias.read_alias_sheet(p)
    be = {r["old"]: r["new"] for r in be_rows}
    off = {k: v["新号"] for k, v in offline.read_alias_map(p).items()}
    assert be == off, f"后端 {be} ≠ 离线 {off}"


def test_cross_impl_same_map_on_real_file():
    """真商品资料：两个读法逐键一致（含说明/生效日期）。"""
    if not REAL_XLSX.is_file():
        pytest.skip(f"商品资料不存在: {REAL_XLSX}")
    try:
        offline = _load_offline()
    except Exception as exc:                                   # noqa: BLE001
        pytest.skip(f"离线脚本不可加载: {exc}")

    be_rows, _ = sku_alias.read_alias_sheet(REAL_XLSX)
    be = {r["old"]: (r["new"], r["note"], r["effectiveAt"]) for r in be_rows}
    off = {k: (v["新号"], v["说明"], v["生效日期"])
           for k, v in offline.read_alias_map(REAL_XLSX).items()}
    assert be == off, f"真文件两侧读法不一致：\n后端 {be}\n离线 {off}"


def test_cross_impl_conclusion_matches():
    """结论文案两侧同源（后端参数顺序不同，逐一比对三种分支）。"""
    try:
        offline = _load_offline()
    except Exception as exc:                                   # noqa: BLE001
        pytest.skip(f"离线脚本不可加载: {exc}")

    cases = [
        ("G510", False, True),    # 新号有流程、未闭环 → 进行中
        ("G510", True, True),     # 新号已闭环
        ("G510", None, False),    # 新号在本品类没单据
    ]
    for new, valid, has_flow in cases:
        be = alerts._alias_conclusion(new, bool(valid), has_flow)
        off = offline._alias_conclusion(
            new, {"G510": 1} if has_flow else {}, {"G510": bool(valid)})
        assert be == off, f"{new}/{valid}/{has_flow}: 后端 {be} ≠ 离线 {off}"


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


def test_integration_alias_rows_are_disjoint(real_alerts):
    """换号旧号必须与催办清单、作废单**互斥**（这是「消除多余异常」的核心保证）。"""
    warned = {r["sku"] for r in real_alerts["rows"]}
    void = {r["sku"] for r in real_alerts["voidRows"]}
    alias = {r["sku"] for r in real_alerts["aliasRows"]}

    assert not (warned & alias), f"换号旧号泄漏进催办清单: {sorted(warned & alias)}"
    assert not (void & alias), f"换号旧号同时被当作废单: {sorted(void & alias)}"
    assert real_alerts["aliasCount"] == len(real_alerts["aliasRows"])


def test_integration_alias_rows_are_registered(real_alerts):
    """每条 aliasRows 的旧号都必须真的登记在换号表里，且新号有结论文案。"""
    registered = sku_alias.load_aliases()
    for r in real_alerts["aliasRows"]:
        assert r["sku"] in registered, f"{r['sku']} 在 aliasRows 却不在换号表"
        assert r["newSku"] == registered[r["sku"]]["new"], f"{r['sku']} 新号不符"
        assert r["conclusion"], f"{r['sku']} 缺结论文案"
        assert r["note2"], "缺随行口径说明字号（note2）"


def test_integration_known_pair_g493_and_g494(real_alerts):
    """真机登记对：G493→G510、G494→G533 必须命中且新号有流程。"""
    registered = sku_alias.load_aliases()
    if "G493" not in registered and "G494" not in registered:
        pytest.skip("换号表尚无登记（本机未落盘）")
    by = {r["sku"]: r for r in real_alerts["aliasRows"]}
    for old, new in (("G493", "G510"), ("G494", "G533")):
        if old not in registered:
            continue
        assert old in by, f"{old} 未进 aliasRows"
        assert by[old]["newSku"] == new, f"{old} 新号应为 {new}"
        assert by[old]["newHasFlow"] is True, f"{old} 的新号 {new} 应有流程单据"


def test_integration_review_marks_nochase():
    """复盘侧：旧号 noChase=True 且部门结论被改写为「已换号 → 新号（不催办）」。"""
    from app.routers import review

    try:
        data = review.get_review_data("手机膜")
    except Exception as exc:                                   # noqa: BLE001
        pytest.skip(f"复盘数据不可用: {exc}")

    registered = sku_alias.load_aliases()
    hits = [s for s in registered if s in data["skuDetails"]]
    if not hits:
        pytest.skip("本品类没有命中换号表的 SKU")

    for old in hits:
        d = data["skuDetails"][old]
        new = registered[old]["new"]
        assert d["noChase"] is True, f"{old} 应标 noChase"
        assert d["aliasOut"]["new"] == new, f"{old} aliasOut 不符"
        assert "不催办" in d["chaseReason"], f"{old} chaseReason 不符：{d['chaseReason']}"
        # 部门结论只在「未完成」的那几格被改写成「已换号 → 新号（不催办）」；
        # 已完成的格子保持「完成」不动（不篡改事实）。
        if not d["valid"]:
            assert any("已换号" in str(v.get("note", ""))
                       for v in d["dept"].values()), f"{old} 部门结论未改写"
        # 反向：新号应带 aliasIn
        if new in data["skuDetails"]:
            assert old in data["skuDetails"][new]["aliasIn"], (
                f"{new} 的 aliasIn 应含 {old}")
    assert data["aliases"]["outN"] >= len(hits)


def test_no_alias_registered_means_unchanged_behavior(monkeypatch):
    """不变量：换号表为空时，alerts 不产生任何 aliasRows（行为与改造前一致）。"""
    import app.sku_alias as sa

    monkeypatch.setattr(sa, "load_aliases", lambda force=False: {})
    try:
        res = alerts.compute_alerts("手机膜")
    except Exception as exc:                                   # noqa: BLE001
        pytest.skip(f"真实数据不可用: {exc}")
    if not res.get("totalSkus"):
        pytest.skip("无批次数据")
    assert res["aliasRows"] == [] and res["aliasCount"] == 0
