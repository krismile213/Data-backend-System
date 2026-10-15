# -*- coding: utf-8 -*-
"""
sku_note.py — 事中日报「人工登记」列的唯一读写入口
==================================================
用途：在研 SKU 需要一条**跟着日期走**的人工备注（如「暂时不弄了」「苹果18duo，
      23号发售」）—— 每天生成日报时自动带到新文件里，不用每天重填。

⚠️ 为什么不能把备注直接写在日报 xlsx 里：
   日报是**每天整体重新生成、幂等覆盖同名文件**的，手填内容下场生成就被冲掉。
   所以备注必须住在**生成器之外**的持久文件里，生成器只读不写。

⚠️ 与 `sku_hold.py`（「人工登记·免催办」）是**两件事，故意分开存**：
   · sku_hold = 业务登记「不再走完流程」→ 该 SKU **移出追踪**（严重副作用）；
   · 本模块  = 在研备注（纯文字说明，不影响任何判定）。
   共用同一个 `SKU登记表.xlsx` 会让使用者不小心把备注填进免催办表、
   直接把 SKU 踢出追踪 —— 故独立文件、独立子表名，互不干扰。

真源 = `资产文件\\事中日报人工登记.xlsx` 的 **`人工登记`** 子表
表结构（表头行从第 1 行起、最多扫 6 行；列名按**包含**匹配，容忍全角空格/大小写）：
    | SKU | 备注 | 更新日期 |
  · 「更新日期」只是给人看的，程序不解析；
  · 同一 SKU 多行 → **以最后一行为准**（与换号表同口径），并给警告；
  · 文件/子表不存在 → 返回空 dict + 提示，**绝不抛异常**（登记表读写不该中断日报生成）。
"""

import os
import re
from pathlib import Path

# 与 sku_hold / sku_alias 共用同一套表头解析工具（口径必须一致）
from .sku_alias import SKU_RE, cell_str, pick_col

#: 独立备注表默认路径（新真源）。env `SKU_NOTE_XLSX` 可覆盖（测试/多机部署用）。
NOTE_XLSX_DEFAULT = r"C:\Users\dell\Desktop\work-main\资产文件\事中日报人工登记.xlsx"

NOTE_SHEET = "人工登记"
HDR_SKU_KEYS = ("SKU", "编码")
HDR_NOTE_KEYS = ("备注", "说明", "原因", "理由", "登记内容")
HDR_DATE_KEYS = ("更新", "日期", "时间", "登记")
MAX_HDR_SCAN = 6

#: 「人工登记」列的口径说明（前端/接口 describe() 可回显，避免各处改文案）
DEFINITION = {
    "source": "事中日报人工登记.xlsx · 人工登记 子表",
    "effect": "生成事中日报时填入「在研SKU状态总表 · 人工登记」列，"
              "随每日重新生成自动延续，无需每天重填",
    "why": "在研 SKU 的业务备注（暂停/发售日/特殊约定）需要跨日期留存；"
           "日报文件每天被幂等覆盖，手填会被冲掉",
    "warning": "本表只影响日报展示，不影响任何时效/预警/豁免判定",
}


def resolve_note_path() -> Path:
    """备注表路径：env `SKU_NOTE_XLSX` → 默认。"""
    env = os.environ.get("SKU_NOTE_XLSX")
    if env:
        return Path(env)
    return Path(NOTE_XLSX_DEFAULT)


def read_notes(path=None) -> tuple:
    """读备注表 → ({SKU: 备注}, warnings)。path 为空 → `resolve_note_path()`。

    绝不清空已生成日报：读不到就返回空 dict，日报该列全空，行为与没有本功能一致。
    """
    src = Path(path) if path else resolve_note_path()
    if not src.is_file():
        return {}, [f"ℹ 事中日报人工登记文件不存在：{src}（人工登记列将全部为空）"]
    try:
        from openpyxl import load_workbook
        wb = load_workbook(src, read_only=True, data_only=True)
    except Exception as e:                   # noqa: BLE001
        return {}, [f"⚠ 事中日报人工登记表读取失败：{type(e).__name__}: {e}"]
    try:
        if NOTE_SHEET not in wb.sheetnames:
            return {}, [f"ℹ 『{NOTE_SHEET}』子表不存在于 {src}"
                        f"（现有子表：{wb.sheetnames}）→ 人工登记列全部为空"]
        return _scan(wb)
    finally:
        try:
            wb.close()
        except Exception:                     # noqa: BLE001
            pass


def _scan(wb) -> tuple:
    """已打开的 workbook → ({SKU: 备注}, warnings)。表头定位逻辑与 sku_hold 同源。"""
    notes: list = []
    if NOTE_SHEET not in wb.sheetnames:
        return {}, [f"⚠ 『{NOTE_SHEET}』子表不存在（现有子表：{wb.sheetnames}）"]
    ws = wb[NOTE_SHEET]
    hdr_row, ci = None, {}
    for i, vals in enumerate(ws.iter_rows(values_only=True), start=1):
        if hdr_row is None:
            if i > MAX_HDR_SCAN:
                break
            c_sku = pick_col(vals, HDR_SKU_KEYS)
            if not c_sku:
                continue
            # ⚠️ 必须挡掉「标题行 / 说明行」——手工表常写成
            #    「在研 SKU 人工备注登记表」，含『SKU』『登记』字样，`pick_col`
            #    按**包含**匹配会把它误当表头（sku_hold 实测踩过，会吞掉全部数据）。
            #    真表头判据（两条都要）：① SKU 那格是**短标签**（≤8 字）；
            #                            ② 该行至少有 **2 个非空格**。
            label = cell_str(vals[c_sku - 1]).replace(" ", "").replace("\u3000", "")
            if len(label) > 8 or sum(1 for v in vals if cell_str(v)) < 2:
                continue
            hdr_row = i
            ci = {"sku": c_sku,
                  "note": pick_col(vals, HDR_NOTE_KEYS),
                  "date": pick_col(vals, HDR_DATE_KEYS)}
            continue

        def _cell(key, _v=vals):
            idx = ci.get(key)
            return _v[idx - 1] if (idx and idx - 1 < len(_v)) else None

        sku = cell_str(_cell("sku")).upper()
        if not sku:
            continue
        note = cell_str(_cell("note"))
        if not note:
            continue                            # 只登记了 SKU 没写备注 → 视为无备注
        notes.append({"sku": sku, "note": note,
                      "updatedAt": cell_str(_cell("date"))})

    if hdr_row is None:
        return {}, [f"⚠ 『{NOTE_SHEET}』前 {MAX_HDR_SCAN} 行未找到含『SKU』的表头行"]

    out, warns, seen = {}, [], {}
    for r in notes:
        sku = r["sku"]
        if sku in seen:
            warns.append(f"⚠ SKU {sku} 重复登记（{seen[sku]} → {r['note']}），以最后一行为准")
        seen[sku] = r["note"]
        if not SKU_RE.match(sku):
            warns.append(f"⚠ 登记编码格式可疑（{sku}），已保留但请核对")
        out[sku] = r["note"]
    return out, warns


def note_count(notes: dict | None) -> int:
    """给日报 index / 接口用：非空备注条数。"""
    return len(notes or {})
