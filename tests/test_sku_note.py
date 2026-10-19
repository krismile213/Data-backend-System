# -*- coding: utf-8 -*-
"""test_sku_note.py — 事中日报「人工登记」列（跨日期延续的在研备注）
======================================================================
背景（2026-10-15）：用户要在「在研SKU状态总表」末列加一个**跟着日期走**的人工登记
（如「暂时不弄了」「苹果18duo，23号发售」）。

⚠️ 核心约束：日报是**每天整体重新生成、幂等覆盖同名文件**的 → 备注绝不能手填在
   日报 xlsx 里（下场生成就被冲掉）。故真源放在**生成器之外**的持久文件：
   `资产文件\\事中日报人工登记.xlsx` · `人工登记` 子表（SKU|备注|更新日期），
   生成器只读不写（`app/sku_note.py`）。

⚠️ 与 `sku_hold`（「人工登记·免催办」）**故意分开存**：那个是「不再走完流程」→
   把 SKU **移出追踪**（严重副作用）；本表只是文字备注、不影响任何判定。
   共用文件会让使用者把备注填进免催办表、误把 SKU 踢出追踪 —— 本文件用测试钉死隔离。

本文件覆盖三层：
  ① 纯逻辑：子表解析（标题行容错/表头包含匹配/缺文件缺子表不抛异常/重复以最后一行为准）
  ② 集成：`daily_report` 末列落值；notes=None 时该列全空（向后兼容不变量）
  ③ 隔离：备注表与免催办登记表**不同文件**，备注不影响豁免判定
"""

import io
import os
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

ROOT = Path(__file__).resolve().parents[1]                       # …/数据后台系统
REPO = ROOT.parent                                               # …/work-main
sys.path.insert(0, str(ROOT / "backend"))

from app import sku_hold, sku_note                               # noqa: E402
from app.reporting import daily_report as _daily_xlsx            # noqa: E402

#: 真源 = 独立备注表（与免催办的 SKU登记表.xlsx 分开）
REAL_XLSX = Path(r"C:\Users\dell\Desktop\work-main\资产文件\事中日报人工登记.xlsx")
HDR = ("SKU", "备注", "更新日期")


def _wb(rows, header=HDR, prelude=(), sheet=None):
    """造一个只含『人工登记』子表的工作簿，供 sku_note 直接消费。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet or sku_note.NOTE_SHEET
    for line in prelude:
        ws.append(list(line))
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    return wb


# ---------------------------------------------------------------------------
# ① 纯逻辑
# ---------------------------------------------------------------------------

def test_parse_basic(tmp_path):
    p = tmp_path / "n.xlsx"
    _wb([("G591", "暂时不弄了", "2026-10-15"),
         ("g566", "苹果18duo，23号发售", "2026-10-15")]).save(p)
    notes, warns = sku_note.read_notes(p)
    assert notes == {"G591": "暂时不弄了", "G566": "苹果18duo，23号发售"}  # SKU 统一大写
    assert warns == []


def test_empty_note_skipped(tmp_path):
    """只登记了 SKU 没写备注 → 视为无备注（不当成空串占列）。"""
    p = tmp_path / "n.xlsx"
    _wb([("G591", "  ", ""), ("G566", "有备注", "")]).save(p)
    notes, _ = sku_note.read_notes(p)
    assert notes == {"G566": "有备注"}


def test_title_row_not_mistaken_as_header(tmp_path):
    """标题行（含『SKU』『登记』字样）不能被误当表头 —— 否则吞掉全部数据。

    与 sku_hold 同款踩坑：`pick_col` 按包含匹配，会把
    「在研 SKU 人工登记备注表」这种标题行当成表头行。
    """
    p = tmp_path / "n.xlsx"
    _wb([("G591", "暂时不弄了", "2026-10-15")],
        prelude=[("在研 SKU 人工登记备注表（每天生成日报时自动带入）",),
                 (None, None, None)]).save(p)
    notes, warns = sku_note.read_notes(p)
    assert notes == {"G591": "暂时不弄了"}


def test_header_fuzzy_match(tmp_path):
    """列名按包含匹配：『产品SKU / 说明 / 更新时间』也要能解析。"""
    p = tmp_path / "n.xlsx"
    _wb([("G591", "备注内容", "今天")],
        header=("产品SKU", "说明", "更新时间")).save(p)
    notes, _ = sku_note.read_notes(p)
    assert notes == {"G591": "备注内容"}


def test_duplicate_last_wins(tmp_path):
    p = tmp_path / "n.xlsx"
    _wb([("G591", "先写的", ""), ("G591", "后写的", "")]).save(p)
    notes, warns = sku_note.read_notes(p)
    assert notes == {"G591": "后写的"}
    assert any("重复登记" in w for w in warns)


def test_bad_sku_warns_but_kept(tmp_path):
    p = tmp_path / "n.xlsx"
    _wb([("不存在的编码", "备注", "")]).save(p)
    notes, warns = sku_note.read_notes(p)
    assert notes == {"不存在的编码": "备注"}
    assert any("格式可疑" in w for w in warns)


def test_missing_file_and_sheet_no_raise(tmp_path):
    """读不到 → 空 dict + 提示，绝不抛异常（登记表读写不该中断日报生成）。"""
    notes, warns = sku_note.read_notes(tmp_path / "no_such.xlsx")
    assert notes == {} and warns
    p = tmp_path / "n.xlsx"
    _wb([], sheet="别的子表").save(p)
    notes2, warns2 = sku_note.read_notes(p)
    assert notes2 == {} and any("子表不存在" in w for w in warns2)


def test_no_header_row(tmp_path):
    """前 6 行没有像表头的行 → 空 + 告警（不吞数据也不假装成功）。"""
    p = tmp_path / "n.xlsx"
    wb = Workbook()
    wb.active.title = sku_note.NOTE_SHEET
    wb.active.append(("随便一行",))
    wb.save(p)
    notes, warns = sku_note.read_notes(p)
    assert notes == {} and any("未找到" in w for w in warns)


def test_resolve_env_override(monkeypatch, tmp_path):
    """env `SKU_NOTE_XLSX` 可覆盖路径（测试/多机部署用）。"""
    monkeypatch.setenv("SKU_NOTE_XLSX", str(tmp_path / "custom.xlsx"))
    assert sku_note.resolve_note_path() == tmp_path / "custom.xlsx"


# ---------------------------------------------------------------------------
# ② 集成：日报末列
# ---------------------------------------------------------------------------

def _items():
    return [{"sku": "G591", "model": "Honor Robot Phone 5G", "status": "超期",
             "staySeg": "取数→签样", "staySegDept": "产品",
             "stayElapsed": 58, "stayTarget": 12, "stayDaysLeft": -46,
             "warnSegs": [], "multiRound": False, "rootCause": {}, "owner": "李杰华"},
            {"sku": "G599", "model": "vivo X500 Pro 5G", "status": "超期",
             "staySeg": "签样→寄送", "staySegDept": "产品",
             "stayElapsed": 3, "stayTarget": 5, "stayDaysLeft": 2,
             "warnSegs": [], "multiRound": False, "rootCause": {}, "owner": "李杰华"}]


_ALERTS = {"todayNewOverdue": [], "ongoingOverdue": [], "warnings": []}


def _sheet1(content):
    wb = load_workbook(io.BytesIO(content))
    return wb["在研SKU状态总表"]


def test_daily_report_column_written():
    content = _daily_xlsx(category="手机膜", computed_at="2026-10-15 15:00",
                          date_str="2026-10-15", items=_items(), alerts=_ALERTS,
                          gantts=[], notes={"G591": "暂时不弄了"})
    ws = _sheet1(content)
    # 表头第 14 列 = 人工登记（2026-10-19 起第 9 列插入「超期环节」，整体右移一位），
    # 前 13 列（含第 13 列「责任人」）不动
    assert ws.cell(row=3, column=14).value == "人工登记"
    assert ws.cell(row=3, column=13).value == "责任人"
    # 有备注的 SKU 填上，没备注的留空
    assert ws.cell(row=4, column=14).value == "暂时不弄了"
    assert ws.cell(row=5, column=1).value == "G599"
    assert ws.cell(row=5, column=14).value in (None, "")


def test_daily_report_without_notes_backwards_compatible():
    """不传 notes（老调用方）→ 该列全空，行为与本功能上线前完全一致。"""
    content = _daily_xlsx(category="手机膜", computed_at="x", date_str="d",
                          items=_items(), alerts=_ALERTS, gantts=[])
    ws = _sheet1(content)
    assert ws.cell(row=3, column=14).value == "人工登记"
    for r in (4, 5):
        assert ws.cell(row=r, column=14).value in (None, "")
    # 其它 sheet 不受影响
    wb = load_workbook(io.BytesIO(content))
    assert wb.sheetnames == ["在研SKU状态总表", "超期明细", "风险预警",
                             "在研SKU甘特图", "异常上报清单"]


def test_note_does_not_leak_into_other_sheets():
    """备注只进总表：超期明细/异常上报是段级粒度，一 SKU 一段会重复 → 不放。"""
    content = _daily_xlsx(category="手机膜", computed_at="x", date_str="d",
                          items=_items(),
                          alerts={"todayNewOverdue": [
                              {"sku": "G591", "dept": "产品", "seg": "取数→签样",
                               "overDays": 46, "elapsed": 58, "target": 12,
                               "owner": "李杰华"}],
                              "ongoingOverdue": [], "warnings": []},
                          gantts=[], notes={"G591": "暂时不弄了"})
    wb = load_workbook(io.BytesIO(content))
    assert wb["超期明细"].cell(row=4, column=7).value == "李杰华"   # 责任人列不变
    assert "暂时不弄了" not in str(wb["超期明细"]["A1"].value or "")


# ---------------------------------------------------------------------------
# ③ 隔离：与「人工登记·免催办」互不干扰
# ---------------------------------------------------------------------------

def test_note_table_is_separate_from_hold_table():
    """备注表与免催办登记表必须是不同文件 —— 防止误把备注填成免催办。"""
    from app import sku_alias
    assert Path(sku_note.NOTE_XLSX_DEFAULT) != Path(sku_alias.REGISTRY_XLSX_DEFAULT)


def test_note_does_not_change_hold_judgement():
    """给 SKU 写备注不会让它被移出追踪：免催办只认 SKU登记表.xlsx·人工登记 子表。"""
    holds, _ = sku_hold.read_hold_sheet()
    hold_skus = {r["sku"] for r in holds}
    # 备注里的两个 SKU 目前都不在免催办名单里（在研、正常参与时效统计）
    for sku in ("G591", "G566"):
        assert sku not in hold_skus


# ---------------------------------------------------------------------------
# 真源文件（用户已迁入两条示例备注）—— 存在才跑
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not REAL_XLSX.exists(), reason="事中日报人工登记.xlsx 未创建")
def test_real_note_file_seeded():
    notes, warns = sku_note.read_notes(REAL_XLSX)
    assert notes.get("G591") == "暂时不弄了"
    assert notes.get("G566") == "苹果18duo，23号发售"
    assert warns == []
