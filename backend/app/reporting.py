# -*- coding: utf-8 -*-
"""
reporting.py — xlsx 报告生成（P0-6 核查报告 / P1-4 评估导出）
==============================================================
只用 openpyxl 直接写单元格（不依赖图表引擎），保证在 Excel 与 WPS 里都能正常显示。

颜色口径与前端 `frontend/src/theme.js` 的 SEMANTIC 对齐（此处取其 hex 的浅色派生版）：
    danger #c62828 / success #2e7d32 / warning #ef6c00 / brand #1e5eff / muted #9e9e9e

产出：
    sku_check_report(code, detail, data)  → 单 SKU 核查报告 bytes（3 sheet）
    assessment_report(items, summary)     → 事后评估三指标 bytes（时效/销量/毛利 3 sheet）
"""

import io
import math
from datetime import datetime, timedelta

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .generic_material import BASIS_UNKNOWN as _BASIS_UNKNOWN
from .sample_cycle import label as _sc_label
from .seg_rules import G_ORDER, ROUTE_TARGETS

# 部门归块色（浅色填充，正文仍用深色字，保证黑白打印可读）
DEPT_COLOR = {
    "产品": "D9E5FF",
    "__GAP__": "EEEEEE",
    "运营": "DCEFE0",
    "后勤": "FDF0D2",
    "运输": "D6EBF8",
    "4国": "EBDFF3",
}
DEPT_LABEL = {"__GAP__": "留空"}

THIN = Side(style="thin", color="D0D0D0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="1E5EFF")
HEAD_FONT = Font(color="FFFFFF", bold=True, size=10)
TITLE_FONT = Font(bold=True, size=13)
CELL_FONT = Font(size=10)


def _judge_color(judge):
    s = str(judge or "")
    if s.startswith("✗") or "反向" in s:
        return "C62828"
    if s.startswith("✓"):
        return "2E7D32"
    if s.startswith("⚠"):
        return "EF6C00"
    return "616161"


def _put(ws, row, col, value, *, font=None, fill=None, align=None):
    c = ws.cell(row=row, column=col, value=value)
    c.font = font or CELL_FONT
    c.border = BORDER
    if fill:
        c.fill = fill
    if align:
        c.alignment = align
    return c


def _head_row(ws, row, headers, widths=None):
    for i, h in enumerate(headers, start=1):
        _put(ws, row, i, h, font=HEAD_FONT, fill=HEAD_FILL,
             align=Alignment(horizontal="center", vertical="center"))
    if widths:
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w


# ---------------------------------------------------------------------------
# P0-6 · 单 SKU 核查报告（甘特 + 段级明细 + 末尾"人工核对"空白列）
# ---------------------------------------------------------------------------
def sku_check_report(code, detail, data, root_cause=None, assess=None,
                     ruleset=None):
    """生成 SKU 核查报告 xlsx（bytes）。

    ruleset：品类规则集（category_rules.RuleSet）；None = 手机膜默认排序。
    """
    wb = Workbook()

    # ---------------- Sheet1：段级明细与人工核对 ----------------
    ws = wb.active
    ws.title = "段级明细与人工核对"
    ws["A1"] = f"{code} 流程核查报告"
    ws["A1"].font = TITLE_FONT
    meta = [
        ("型号", detail.get("model") or "—"),
        ("开发类型", detail.get("devType") or "—"),
        ("立项时间", detail.get("firstProposalAt") or "—"),
        ("复盘结论", "✅ 有效复盘（三部门完成）" if detail.get("valid")
         else f"❌ 未闭环：{detail.get('invalidReason') or '—'}"),
        ("生成时间", data.get("computedAt") or datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("口径", "实际动作映射（seg_rules）；节点时间 = 系统业务列 > audit上报 > 审批动作"),
    ]
    if ruleset is not None and ruleset.provisional:
        meta.append(("⚠ 目标口径",
                     f"目标天/评级线 = {ruleset.targets_source or '继承名义值'}；"
                     "判定仅供预警参考，不得作为既定 KPI"))
    r = 2
    for k, v in meta:
        warn = str(k).startswith("⚠")
        _put(ws, r, 1, k, font=Font(bold=True, size=10,
                                    color="EF6C00" if warn else "000000"))
        c = ws.cell(row=r, column=2, value=v)
        c.font = Font(size=10, color="EF6C00") if warn else CELL_FONT
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=5)
        r += 1

    # 多轮提交警示（有则显著标注）
    mr = detail.get("multiRound") or {}
    if mr.get("flag"):
        _put(ws, r, 1, "⚠ 多轮提交", font=Font(bold=True, size=10, color="EF6C00"))
        c = ws.cell(row=r, column=2, value=mr.get("note") or "")
        c.font = Font(size=10, color="EF6C00")
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=5)
        r += 1

    r += 1
    header_row = r
    _head_row(ws, header_row,
              ["部门", "环节", "起点节点", "终点节点", "耗时(天)", "目标(天)",
               "判定", "执行人", "人工核对（请填写）"],
              widths=[9, 22, 18, 18, 10, 10, 16, 12, 18])

    _gorder = ruleset.g_order if ruleset is not None else G_ORDER
    segs = sorted(detail.get("segments") or [],
                  key=lambda x: _gorder.get(x.get("dept"), 9))
    r = header_row + 1
    first_data = r
    for s in segs:
        dept = s.get("dept") or ""
        fill = PatternFill("solid", fgColor=DEPT_COLOR.get(dept, "FFFFFF"))
        _put(ws, r, 1, DEPT_LABEL.get(dept, dept), fill=fill)
        _put(ws, r, 2, s.get("seg"), fill=fill)
        _put(ws, r, 3, s.get("start") or "—", fill=fill)
        _put(ws, r, 4, s.get("end") or "—", fill=fill)
        _put(ws, r, 5, "—" if s.get("days") is None else s.get("days"), fill=fill,
             align=Alignment(horizontal="center"))
        _put(ws, r, 6, "—" if s.get("target") is None else s.get("target"), fill=fill,
             align=Alignment(horizontal="center"))
        _put(ws, r, 7, s.get("judge") or "", fill=fill,
             font=Font(size=10, color=_judge_color(s.get("judge")),
                       bold=str(s.get("judge") or "").startswith("✗")))
        _put(ws, r, 8, s.get("executor") or "—", fill=fill)
        _put(ws, r, 9, None)                    # ← 人工核对：刻意留空
        r += 1
    last_data = r - 1
    if last_data >= first_data:
        dv = DataValidation(type="list", formula1='"已核对,有异议,待确认,漏报"',
                            allow_blank=True, showDropDown=False)
        dv.prompt = "请人工核对本行后填写"
        ws.add_data_validation(dv)
        dv.add(f"I{first_data}:I{last_data}")
    ws.freeze_panes = ws.cell(row=header_row + 1, column=3)

    # ---------------- Sheet2：甘特（部门归块 + 单元格时间格） ----------------
    gw = wb.create_sheet("甘特")
    _gantt_sheet(gw, segs, label=f"{code} 流程甘特")

    # ---------------- Sheet3：核心原因与损益 ----------------
    aw = wb.create_sheet("核心原因与损益")
    _causal_sheet(aw, root_cause, assess, detail)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _gantt_sheet(ws, segs, label=""):
    """单元格网格甘特：行=段（按部门归块），列=时间格（按跨度自适应粒度）。"""
    ws["A1"] = label
    ws["A1"].font = TITLE_FONT
    segs = [s for s in segs if s.get("t0") or s.get("t1")]
    if not segs:
        ws["A3"] = "无可用时间轴"
        return

    starts = [s["t0"] or s["t1"] for s in segs]
    ends = [s["t1"] or s["t0"] for s in segs]
    t_min, t_max = min(starts), max(ends)
    span = max((t_max.date() - t_min.date()).days + 1, 1)
    step = 1 if span <= 60 else (3 if span <= 180 else 7)
    ncol = min((span + step - 1) // step, 200)

    head = 3
    _put(ws, head, 1, "部门", font=HEAD_FONT, fill=HEAD_FILL)
    _put(ws, head, 2, "环节", font=HEAD_FONT, fill=HEAD_FILL)
    _put(ws, head, 3, "耗时(天)", font=HEAD_FONT, fill=HEAD_FILL)
    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 24
    ws.column_dimensions["C"].width = 10
    grid0 = 4
    for c in range(ncol):
        col = grid0 + c
        ws.column_dimensions[get_column_letter(col)].width = 3.2
        d = t_min.date()
        cur = d.fromordinal(d.toordinal() + c * step)
        _put(ws, head, col, cur.strftime("%m-%d"),
             font=Font(size=8, color="616161"),
             align=Alignment(horizontal="center"))

    row = head + 1
    for s in segs:
        dept = s.get("dept") or ""
        fill = PatternFill("solid", fgColor=DEPT_COLOR.get(dept, "FFFFFF"))
        _put(ws, row, 1, DEPT_LABEL.get(dept, dept), fill=fill)
        _put(ws, row, 2, s.get("seg"), fill=fill)
        _put(ws, row, 3, "—" if s.get("days") is None else s.get("days"),
             fill=fill, align=Alignment(horizontal="center"))
        t0 = s.get("t0") or s.get("t1")
        t1 = s.get("t1") or s.get("t0")
        c0 = (t0.date() - t_min.date()).days // step
        c1 = (t1.date() - t_min.date()).days // step
        bar_fill = PatternFill("solid", fgColor=_bar_color(dept, s.get("judge")))
        for c in range(max(c0, 0), min(c1, ncol - 1) + 1):
            cell = ws.cell(row=row, column=grid0 + c)
            cell.fill = bar_fill
            cell.border = BORDER
        row += 1

    row += 1
    ws.cell(row=row, column=1, value=(
        f"时间粒度：{step} 天/格；起点 {t_min.strftime('%Y-%m-%d')}，"
        f"终点 {t_max.strftime('%Y-%m-%d')}（共 {span} 天）。"
        "红色条 = 该段判定超期。")).font = Font(size=9, color="616161")
    ws.freeze_panes = ws.cell(row=head + 1, column=grid0)


def _bar_color(dept, judge):
    s = str(judge or "")
    if s.startswith("✗") or "反向" in s:
        return "E57373"                     # danger 浅
    if s.startswith("⚠"):
        return "FFB74D"                     # warning 浅
    if s.startswith("✓"):
        return "81C784"                     # success 浅
    return DEPT_COLOR.get(dept, "CCCCCC")


def _causal_sheet(ws, root_cause, assess, detail):
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 96
    row = 1

    ws.cell(row=row, column=1, value="核心原因（因果链定位）").font = TITLE_FONT
    row += 1
    if not root_cause:
        _put(ws, row, 1, "结论", font=Font(bold=True, size=10))
        _put(ws, row, 2, "无超期段，流程衔接良好")
        row += 1
    else:
        for k, v in (("段位", f"{root_cause.get('dept')} · {root_cause.get('seg')}"),
                     ("定性", root_cause.get("type")
                      + ("（人工漏报嫌疑）" if root_cause.get("suspect") else "")),
                     ("责任人", root_cause.get("owner") or "—"),
                     ("等待天数", f"{root_cause.get('waitDays')} 天"),
                     ("证据", root_cause.get("evidence") or "—")):
            _put(ws, row, 1, k, font=Font(bold=True, size=10))
            _put(ws, row, 2, v)
            ws.cell(row=row, column=2).alignment = Alignment(wrap_text=True,
                                                             vertical="top")
            row += 1
        chain = root_cause.get("chain") or []
        if chain:
            row += 1
            ws.cell(row=row, column=1, value="步进因果链").font = Font(bold=True, size=10)
            row += 1
            _head_row(ws, row, ["节点", "时间", "距上一节点(天)"])
            row += 1
            for c in chain:
                _put(ws, row, 1, c.get("node"))
                _put(ws, row, 2, c.get("at") or "—")
                _put(ws, row, 3, "—" if c.get("wait") is None else c.get("wait"))
                row += 1

    row += 2
    ws.cell(row=row, column=1, value="事后评估（空运口径损益）").font = TITLE_FONT
    row += 1
    if not assess:
        _put(ws, row, 1, "结论", font=Font(bold=True, size=10))
        _put(ws, row, 2, "无法评估（缺取数时间或无可用终点）")
        return
    dir_label = {"loss": "亏损", "gain": "增益（提前完成）", "none": "无损益"}[
        assess.get("direction", "none")]
    rows = [
        ("口径", f"{assess.get('basis')}；时钟起点=取数"
                 f"（提案→取数 1 天只挂异常、不进考核）"),
        ("取数→到货", f"{assess.get('spanDays')} 天"),
        ("推迟底线", f"{assess.get('limit')} 天（对齐路线总评级底线）"),
        ("推迟天数", f"{assess.get('delayDays')} 天 → {dir_label}"),
        ("评级/日均", f"{assess.get('grade') or '未命中'}；"
                      f"{assess.get('dailyMin')}~{assess.get('dailyMax')} 件/天"),
        ("销量影响", f"{assess.get('qtyMin')}~{assess.get('qtyMax')} 件"),
        ("单价", f"¥{assess.get('price')}（{assess.get('priceType') or '—'}）"
                 if assess.get("price") is not None else "缺失"),
        ("金额区间",
         f"¥{assess.get('amountMin'):,.2f} ~ ¥{assess.get('amountMax'):,.2f}"
         if assess.get("amountMin") is not None else "缺单价，无法折算"),
        ("降级标注", "；".join(assess.get("degraded") or []) or "无"),
    ]
    for k, v in rows:
        _put(ws, row, 1, k, font=Font(bold=True, size=10))
        _put(ws, row, 2, v)
        row += 1
    row += 1
    ws.cell(row=row, column=1, value="口径说明：推迟天数 × 评级日均区间 = 销量影响；"
            "销量影响 × 单价 = 金额区间。增益仅「提前完成」才计入。").font = Font(
        size=9, color="616161")


# ---------------------------------------------------------------------------
# P1-4 · 事后评估三指标导出（时效 / 销量 / 毛利）
# ---------------------------------------------------------------------------
def assessment_report(items, summary, computed_at="", start_node="取数",
                      provisional=False, targets_source=""):
    """事后评估三指标导出（时效 / 销量 / 金额）。

    start_node：考核时钟起点（手机膜=取数 / 手机壳=提案）——原先 A1 写死「取数」，
                手机壳导出会显示错的口径，此处改为按品类传入。
    """
    wb = Workbook()

    ws1 = wb.active
    ws1.title = "时效"
    ws1["A1"] = f"事后评估 · 时效（路线总评级口径，时钟起点={start_node}）"
    ws1["A1"].font = TITLE_FONT
    if provisional:
        ws1["A2"] = (f"⚠ 目标口径待校准：评级线与推迟底线按「"
                     f"{targets_source or '继承名义值'}」计算，仅供预警参考，"
                     "不得作为既定 KPI 引用。")
        ws1["A2"].font = Font(bold=True, size=10, color="EF6C00")
        ws1.merge_cells(start_row=2, start_column=1, end_row=2, end_column=8)
    _head_row(ws1, 3, ["SKU", "评级", "口径", f"{start_node}→到货(天)", "底线(天)",
                       "推迟天数(天)", "方向", "降级标注"],
              widths=[16, 8, 16, 14, 10, 14, 10, 30])
    r = 4
    for x in items:
        if not x:
            continue
        _put(ws1, r, 1, x["sku"])
        _put(ws1, r, 2, x.get("grade") or "—")
        _put(ws1, r, 3, x.get("basis"))
        _put(ws1, r, 4, x.get("spanDays"))
        _put(ws1, r, 5, x.get("limit"))
        c = _put(ws1, r, 6, x.get("delayDays"))
        c.font = Font(size=10, bold=x["direction"] == "loss",
                      color="C62828" if x["direction"] == "loss"
                      else ("2E7D32" if x["direction"] == "gain" else "616161"))
        _put(ws1, r, 7, {"loss": "亏损", "gain": "增益", "none": "无"}[x["direction"]])
        _put(ws1, r, 8, "；".join(x.get("degraded") or []))
        r += 1

    ws2 = wb.create_sheet("销量")
    ws2["A1"] = "事后评估 · 销量影响（推迟天数 × 评级日均区间）"
    ws2["A1"].font = TITLE_FONT
    _head_row(ws2, 3, ["SKU", "评级", "日均下限(件/天)", "日均上限(件/天)",
                       "推迟天数(天)", "少卖下限(件)", "少卖上限(件)"],
              widths=[16, 8, 16, 16, 14, 14, 14])
    r = 4
    for x in items:
        if not x:
            continue
        _put(ws2, r, 1, x["sku"])
        _put(ws2, r, 2, x.get("grade") or "—")
        _put(ws2, r, 3, x.get("dailyMin"))
        _put(ws2, r, 4, x.get("dailyMax"))
        _put(ws2, r, 5, x.get("delayDays"))
        _put(ws2, r, 6, x.get("qtyMin"))
        _put(ws2, r, 7, x.get("qtyMax"))
        r += 1

    ws3 = wb.create_sheet("毛利")
    ws3["A1"] = "事后评估 · 金额影响（销量影响 × 单价）"
    ws3["A1"].font = TITLE_FONT
    _head_row(ws3, 3, ["SKU", "评级", "单价", "单价类型", "少卖下限(件)",
                       "少卖上限(件)", "金额下限(元)", "金额上限(元)", "方向"],
              widths=[16, 8, 12, 14, 14, 14, 16, 16, 10])
    r = 4
    for x in items:
        if not x:
            continue
        _put(ws3, r, 1, x["sku"])
        _put(ws3, r, 2, x.get("grade") or "—")
        _put(ws3, r, 3, x.get("price"))
        _put(ws3, r, 4, x.get("priceType"))
        _put(ws3, r, 5, x.get("qtyMin"))
        _put(ws3, r, 6, x.get("qtyMax"))
        _put(ws3, r, 7, x.get("amountMin"))
        _put(ws3, r, 8, x.get("amountMax"))
        _put(ws3, r, 9, {"loss": "亏损", "gain": "增益", "none": "无"}[x["direction"]])
        r += 1

    r += 1
    s = summary or {}
    ws3.cell(row=r, column=1, value="汇总").font = Font(bold=True, size=11)
    r += 1
    for k, v in (("可评估 SKU", s.get("assessedCount")),
                 ("亏损 SKU", s.get("lossCount")),
                 ("增益 SKU", s.get("gainCount")),
                 ("亏损合计(元)",
                  f"¥{s.get('lossTotalMin', 0):,.2f} ~ ¥{s.get('lossTotalMax', 0):,.2f}"),
                 ("增益合计(元)",
                  f"¥{s.get('gainTotalMin', 0):,.2f} ~ ¥{s.get('gainTotalMax', 0):,.2f}"),
                 ("口径降级 SKU 数", s.get("degradedCount")),
                 ("生成时间", computed_at)):
        ws3.cell(row=r, column=1, value=k).font = Font(bold=True, size=10)
        ws3.cell(row=r, column=2, value=v).font = CELL_FONT
        r += 1

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# T10 · 单据增量导出（未闭环 SKU 的新增单据 / 新动作 / 状态变化）
# ---------------------------------------------------------------------------
def doc_delta_report(delta, rows, category="", provisional=False,
                     targets_source=""):
    """单据增量 xlsx（bytes）。5 sheet：新增单据 / 新动作 / 状态变化 /
    未闭环 SKU 汇总 / 口径说明。

    delta：doc_delta_service.doc_delta() 的响应（取汇总与走势）
    rows ：doc_delta_service.window_rows() 的明细（窗口内全部增量行）
    """
    rows = rows or {}
    win = (delta or {}).get("window") or {}
    summary = win.get("summary") or {}
    last = (delta or {}).get("last") or None
    scope = (delta or {}).get("scope") or ""

    wb = Workbook()

    # ---- sheet 1：新增单据 ----
    ws1 = wb.active
    ws1.title = "新增单据"
    ws1["A1"] = (f"单据增量 · 新增单据（近 {win.get('days')} 天，"
                 f"自 {win.get('since')} 起）")
    ws1["A1"].font = TITLE_FONT
    ws1["A2"] = (f"范围：{scope}；仅统计**未闭环** SKU 的单据 —— "
                 "已闭环 SKU 不再推进，不纳入追踪。")
    ws1["A2"].font = Font(size=10, color="616161")
    if provisional:
        ws1["A3"] = (f"⚠ {category} 目标口径待校准（{targets_source or '继承名义值'}）；"
                     "本表只记录「单据有没有新增」，不涉及目标天判定。")
        ws1["A3"].font = Font(bold=True, size=10, color="EF6C00")
    _head_row(ws1, 5, ["SKU", "型号", "Part", "审批编号", "审批状态",
                       "创建时间", "节点数", "首次发现于"],
              widths=[16, 22, 8, 26, 12, 20, 8, 20])
    r = 6
    for x in rows.get("newDocs") or []:
        _put(ws1, r, 1, x.get("sku"))
        _put(ws1, r, 2, x.get("model") or "")
        _put(ws1, r, 3, x.get("part"))
        _put(ws1, r, 4, x.get("approvalNo") or "")
        _put(ws1, r, 5, x.get("status") or "—")
        _put(ws1, r, 6, x.get("createAt") or "")
        _put(ws1, r, 7, x.get("nodeCount"))
        _put(ws1, r, 8, x.get("at") or "")
        r += 1
    if r == 6:
        ws1.cell(row=6, column=1, value="（窗口内没有新增单据）").font = CELL_FONT

    # ---- sheet 2：单据新动作 ----
    ws2 = wb.create_sheet("单据新动作")
    ws2["A1"] = "单据增量 · 既有单据上的新动作（新单据的动作不在这里重复计）"
    ws2["A1"].font = TITLE_FONT
    _head_row(ws2, 3, ["SKU", "Part", "审批编号", "新动作", "动作时间",
                       "操作人", "首次发现于"],
              widths=[16, 8, 26, 26, 20, 14, 20])
    r = 4
    for x in rows.get("newNodes") or []:
        _put(ws2, r, 1, x.get("sku"))
        _put(ws2, r, 2, x.get("part"))
        _put(ws2, r, 3, x.get("approvalNo") or "")
        _put(ws2, r, 4, x.get("action") or "")
        _put(ws2, r, 5, x.get("actionAt") or "")
        _put(ws2, r, 6, x.get("operator") or "")
        _put(ws2, r, 7, x.get("at") or "")
        r += 1
    if r == 4:
        ws2.cell(row=4, column=1, value="（窗口内没有新增动作）").font = CELL_FONT

    # ---- sheet 3：状态变化 ----
    ws3 = wb.create_sheet("状态变化")
    ws3["A1"] = "单据增量 · 审批状态变化"
    ws3["A1"].font = TITLE_FONT
    _head_row(ws3, 3, ["SKU", "Part", "审批编号", "原状态", "新状态", "发现于"],
              widths=[16, 8, 26, 12, 12, 20])
    r = 4
    for x in rows.get("statusChanges") or []:
        _put(ws3, r, 1, x.get("sku"))
        _put(ws3, r, 2, x.get("part"))
        _put(ws3, r, 3, x.get("approvalNo") or "")
        _put(ws3, r, 4, x.get("from") or "")
        c = _put(ws3, r, 5, x.get("to") or "")
        c.font = Font(size=10, color="2E7D32" if x.get("to") == "已结束"
                      else ("C62828" if x.get("to") == "终止" else "616161"))
        _put(ws3, r, 6, x.get("at") or "")
        r += 1
    if r == 4:
        ws3.cell(row=4, column=1, value="（窗口内没有状态变化）").font = CELL_FONT

    # ---- sheet 4：未闭环 SKU 汇总 ----
    ws4 = wb.create_sheet("未闭环SKU汇总")
    ws4["A1"] = "单据增量 · 未闭环 SKU 增量排行（近 %s 天）" % win.get("days")
    ws4["A1"].font = TITLE_FONT
    _head_row(ws4, 3, ["SKU", "型号", "开发类型", "未闭环原因", "当前单据数",
                       "新增单据", "新增动作", "状态变化", "最近变化"],
              widths=[16, 22, 12, 40, 12, 10, 10, 10, 20])
    r = 4
    for x in win.get("bySku") or []:
        _put(ws4, r, 1, x.get("sku"))
        _put(ws4, r, 2, x.get("model") or "")
        _put(ws4, r, 3, x.get("devType") or "")
        _put(ws4, r, 4, x.get("reason") or "")
        _put(ws4, r, 5, x.get("docCount"))
        _put(ws4, r, 6, x.get("newDocs"))
        _put(ws4, r, 7, x.get("newNodes"))
        _put(ws4, r, 8, x.get("statusChanges"))
        _put(ws4, r, 9, x.get("lastChangeAt") or "")
        r += 1

    # ---- sheet 5：口径说明 ----
    ws5 = wb.create_sheet("口径说明")
    ws5["A1"] = "单据增量追踪 · 口径说明"
    ws5["A1"].font = TITLE_FONT
    lines = [
        f"品类：{category}",
        f"统计范围：{scope}",
        f"统计窗口：近 {win.get('days')} 天（自 {win.get('since')} 起）；"
        f"共 {summary.get('snapshots', 0)} 次快照",
        f"新增单据 {summary.get('newDocs', 0)} 张 / 新增动作 "
        f"{summary.get('newNodes', 0)} 个 / 状态变化 "
        f"{summary.get('statusChanges', 0)} 次 / 新进入未闭环 "
        f"{summary.get('newSkus', 0)} 个 SKU / 已闭环离开 "
        f"{summary.get('closedSkus', 0)} 个 SKU",
        "",
        "1) 为什么要这个模型：批次目录名固定后「只有一个批次」，批次对比失去意义；"
        "本表追踪的是同一批未闭环 SKU 身上『又多了哪些单据』。",
        "2) 基线拍法：数据指纹（批次导入状态 + 规则版本 + 商品资料）变化时才拍一次，"
        "没变化不产生事件；当天多次同步各自成事件，因此按时间累积即为真实增量。",
        "3) 单据键 = SKU|Part|审批编号（编号缺失时用创建时间兜底）。"
        "不能用数据库自增 id —— 重导入会整批重建，id 每次都变。",
        "4) 单据指纹 = 审批状态 + 节点动作（动作|时间|操作人），"
        "因此「同一张单新推进一步」也会被抓出来。",
        "5) 新单据自带的节点**不计入**「新增动作」，避免一张新单被数成 1 单 + N 动作。",
        "6) 已闭环 SKU 会离开追踪集合（记在「已闭环离开」计数里），不再占基线体积。",
        "7) 疑似基线重算：某轮新增单据数 > 当前单据总数×0.6 且总数 ≥ 50 时打标，"
        "提示可能是源单据号段变化，而非真的有这么多新单。",
    ]
    if provisional:
        lines.append(f"8) ⚠ 待校准：{category} 的评级线/目标天仍是名义值"
                     f"（{targets_source or '继承名义值'}），与本表增量计数无关。")
    r = 3
    for ln in lines:
        c = ws5.cell(row=r, column=1, value=ln)
        c.font = Font(bold=True, size=11) if ln == "" else CELL_FONT
        r += 1

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 路由总览用的路线口径说明（前端"口径说明卡"数据源）
# ---------------------------------------------------------------------------
def route_limits(ruleset=None):
    """路线评级线。ruleset=None → 手机膜默认（行为不变）。"""
    src = ruleset.route_targets if ruleset is not None else ROUTE_TARGETS
    return {k: dict(v) for k, v in src.items()}


# ---------------------------------------------------------------------------
# P0-4 · 事中日报（5 张固定：在研总表 / 超期明细 / 风险预警 / 在研SKU甘特图 / 上报清单；
#                   按需 +2：货件运输 / 豁免备查）
# ---------------------------------------------------------------------------
def daily_report(*, category, computed_at, date_str, items, alerts,
                 gantts=None, provisional=False, targets_source="", exempts=None,
                 notes=None, shipments=None, digested=None, sample_cycles=None):
    """生成事中日报 xlsx（bytes）。

    items    ：inflight overview 的在研 SKU 行（staySeg=当前停留环节，
               stayDaysLeft=距超时天数，负=已超期）；
    alerts   ：compute_alerts_today() 的 {todayNewOverdue, ongoingOverdue, warnings}；
    gantts   ：_gantt_records() 的甘特记录（每在研 SKU 一块）→「在研SKU甘特图」
               sheet（2026-10-06 替换原「今日进度变化」）；
    provisional/targets_source：该品类的目标天/评级线是否仍是待校准的名义值
                （手机壳 = True）。为 True 时在总表标题下打一条橙色口径警示——
                日报是要拿去催办/上报的，口径不确定必须写在脸上。
    exempts  ：被豁免移出追踪的 SKU 明细（compute_inflight 的 exempted +
                transportExempted，[{sku, model, kind, reason}]）。kind ∈ {hold,
                alias, void, 运输闭环}；非空时追加「豁免备查」sheet —— 移出追踪但不吞数据，
                谁被豁免、为什么豁免，日报里要能对上账。
    notes    ：{SKU: 备注}（`sku_note.read_notes()` 的真源，`资产文件\事中日报人工登记.xlsx`）。
                非空时在总表末列「人工登记」按 SKU 填上，随每日生成自动延续；
                缺省/为空 → 该列全空，行为与本功能上线前完全一致。
                只影响展示，不影响任何时效/预警/豁免判定。
    shipments：compute_inflight 的 shipmentTracking（在研 SKU 的真货件逐票，
               字段见 inflight_service._shipment_tracking_rows）。非空时在
               「异常上报清单」之后追加「货件运输」sheet —— 货件号/发往地区/
               已运输天数 + 追踪状态（在途 / 留观N/5天 / 已归档(到货N天)）；
               **全量保留**，5 天只降级状态文案不删行（2026-11-01 改）；
               未发货渠道无货件号不进本表。空 → 不产生空页。
    sample_cycles：{SKU: sampleCycle}（**直接取复盘缓存** skuDetails[*].sampleCycle，
               计算入口 = app/sample_cycle.py，与复盘页同一实现）。非空时总表末
               追加 4 列：签样(系统) / 拍摄交付(系统) / 样机流转(天) / 流转判定。
               口径（2026-11-06 用户定）：**只认系统业务日期**，任一端缺失就留空、
               判「待补」（不拿 audit 补录顶替，否则 G600 会算出 -14 天的负段）；
               合格线 = seg_rules.SAMPLE_CYCLE["limit"]（15 = 5 + 10），严格小于。
               不传 → 不产生这 4 列时表宽保持 15 列（行为与本功能上线前一致）。
               ⚠️ 甘特图 sheet 不动 —— 样机流转只做指标，不改时间轴画法。
    """
    wb = Workbook()
    dir_label = {"loss": "亏损", "gain": "增益", "none": "无"}
    owner_of = {it.get("sku"): (it.get("owner") or "") for it in items or []}
    OVER_ESCALATE = 30                     # 长期未决阈值（天）：超期 ≥30 天升级

    # 样机流转 4 列（2026-11-06）：只有传了 sample_cycles 才追加 —— 缺省时
    # 表宽仍是 15 列，行为与本功能上线前完全一致（老调用方/老测试不受影响）。
    _SC = sample_cycles or {}
    _NCOL = 19 if _SC else 15

    # ---- Sheet1 在研 SKU 状态总表 ----
    ws1 = wb.active
    ws1.title = "在研SKU状态总表"
    ws1["A1"] = (f"流程事中日报 · {category} · {date_str}"
                 f"（生成于 {computed_at}）")
    ws1["A1"].font = TITLE_FONT
    _legend_row = 2
    if provisional:
        ws1["A2"] = (f"⚠ 目标口径待校准：本表「目标(天)/超期(天)/预警线」按"
                     f"「{targets_source or '继承名义值'}」判定，仅供预警参考，"
                     "不得作为既定 KPI 引用；业务确认后须重跑本日报。")
        ws1["A2"].font = Font(bold=True, size=10, color="EF6C00")
        ws1.merge_cells(start_row=2, start_column=1, end_row=2, end_column=_NCOL)
        _legend_row = 3
    # 图例（2026-10-29）：新增标注太多，不写清楚会被误读
    ws1[f"A{_legend_row}"] = (
        "标注：状态「超期❗」= 任一超期段 ≥30 天（长期未决，优先处理）；"
        "部门列「*」= 其他部门段亦超期（明细见「超期环节」列）；"
        "「无目标」= 该段未设目标天，不参与超期/预警判定；"
        "「素材·通用」= 复用已有素材/不真机拍摄，跳过寄送拍摄(15天)与素材加工(3天)，"
        "本地仓到货后直进上架 Listing+广告；"
        "「素材·—」= Part6 单据未建，暂无法判定（默认按需拍摄）；"
        "流程依赖（2026-11-05）：Part6 单据在本地仓到货检查(Part3)通过后才建 —— "
        "Part6 未建单时「签样→寄送」未轮到、不判超期，当前停留回落到「采购→本地仓到货」；"
        "「样机流转」= 签样→拍摄交付 的间隔，**只认系统业务日期**、<15 天合格；"
        "任一端没填就留空判「待补」（不拿 audit 补录顶替，补录一拖会算成负段）；"
        "人工登记「⚠未豁免」= 已写备注但未匹配任何豁免通道，仍在追踪。")
    ws1[f"A{_legend_row}"].font = Font(size=9, color="616161")
    ws1.merge_cells(start_row=_legend_row, start_column=1,
                    end_row=_legend_row, end_column=_NCOL)
    _hdr = ["SKU", "型号", "状态", "当前停留环节", "部门", "已停留(天)",
            "目标(天)", "距超时(天)", "超期环节", "预警段数", "多轮提交",
            "核心原因", "责任人", "人工登记", "素材"]
    _w = [14, 18, 10, 22, 8, 10, 9, 11, 26, 9, 10, 26, 12, 20, 10]
    if _SC:
        _hdr += ["签样(系统)", "拍摄交付(系统)", "样机流转(天)", "流转判定"]
        _w += [13, 15, 12, 14]
    _head_row(ws1, 4, _hdr, widths=_w)
    r = 5
    for it in items or []:
        # 长期未决（≥30 天）→ 整行浅红底纹（麻木效应对策：190 天和 2 天不能同权重）
        _esc = any(isinstance(o.get("overDays"), (int, float))
                   and o["overDays"] >= OVER_ESCALATE
                   for o in (it.get("overSegs") or []))
        _row_fill = PatternFill("solid", fgColor="FDECEA") if _esc else None
        _put(ws1, r, 1, it.get("sku"), fill=_row_fill)
        _put(ws1, r, 2, it.get("model") or "—", fill=_row_fill)
        st = it.get("status")
        _st_txt = "超期❗" if (st == "超期" and _esc) else st
        c = _put(ws1, r, 3, _st_txt, align=Alignment(horizontal="center"),
                 fill=_row_fill)
        c.font = Font(size=10, bold=_st_txt in ("超期", "超期❗", "预警"),
                      color="C62828" if st == "超期"
                      else ("EF6C00" if st == "预警" else "2E7D32"))
        _put(ws1, r, 4, it.get("staySeg") or "—", fill=_row_fill)
        # 部门列失真修正（2026-10-29）：当前部门 + 其他部门段亦超期时加「*」
        _dept = it.get("staySegDept") or "—"
        _extra_depts = sorted({o.get("dept") for o in (it.get("overSegs") or [])
                               if o.get("dept") and o.get("dept") != _dept})
        _dept_txt = f"{_dept}*" if _extra_depts else _dept
        _put(ws1, r, 5, _dept_txt, fill=_row_fill,
             font=Font(size=10, bold=bool(_extra_depts),
                       color="B36A00" if _extra_depts else "000000"),
             align=Alignment(horizontal="center"))
        _put(ws1, r, 6, "—" if it.get("stayElapsed") is None
             else it.get("stayElapsed"), fill=_row_fill,
             align=Alignment(horizontal="center"))
        _put(ws1, r, 7, "—" if it.get("stayTarget") is None
             else it.get("stayTarget"), fill=_row_fill,
             align=Alignment(horizontal="center"))
        # 距超时(天)：负 = 已超期 N 天（红），0 = 今日到期（红），
        # ≤2 天临近（橙），其余正常 —— 数字比"超期天数"更有行动性；
        # 目标未设（免检金牌，2026-10-29）→ 显式写「无目标」，不再伪装成普通 "—"
        dl = it.get("stayDaysLeft")
        if dl is None and it.get("stayTarget") is None \
                and it.get("stayElapsed") is not None:
            dl_txt, dl_color, dl_bold = "无目标", "9E9E9E", False
        elif dl is None:
            dl_txt, dl_color, dl_bold = "—", "616161", False
        elif dl < 0:
            dl_txt, dl_color, dl_bold = f"已超{-dl:.0f}天", "C62828", True
        elif dl == 0:
            dl_txt, dl_color, dl_bold = "今日到期", "C62828", True
        else:
            dl_txt = f"剩{dl:.0f}天"
            dl_color, dl_bold = ("EF6C00", True) if dl <= 2 else ("2E7D32", False)
        c = _put(ws1, r, 8, dl_txt, align=Alignment(horizontal="center"),
                 fill=_row_fill)
        c.font = Font(size=10, bold=dl_bold, color=dl_color)
        # 超期环节（2026-10-19 二次修正）：状态列只认当前停留环节，本列仍统计
        # **全部**超期段。加「当前·/已过·」前缀，一眼看出超的是现在卡的这段、
        # 还是已经走过的那段（G599：当前·采购→首次上架 / 已过·签样→寄送）。
        _ov = []
        _cur_over = bool(it.get("stayOver"))
        for _o in (it.get("overSegs") or []):
            _od = _o.get("overDays")
            _tag = "当前·" if _o.get("isCurrent") else "已过·"
            _ov.append(f"{_tag}{_o.get('seg') or '—'} "
                       + (f"超{_od:g}天" if isinstance(_od, (int, float))
                          else "超期"))
        c = _put(ws1, r, 9, "；".join(_ov) if _ov else "—", fill=_row_fill)
        if _ov:
            # 当前环节超期=红（要立刻催）；只有历史段超期=橙（记录/追责用）
            c.font = Font(size=10, color="C62828" if _cur_over else "EF6C00")
        _put(ws1, r, 10, len(it.get("warnSegs") or []), fill=_row_fill,
             align=Alignment(horizontal="center"))
        _put(ws1, r, 11, "⚠ 是" if it.get("multiRound") else "—", fill=_row_fill)
        rc = it.get("rootCause") or {}
        _put(ws1, r, 12, (f"{rc.get('type')}({rc.get('seg')})"
                          if rc.get("type") else "—"), fill=_row_fill)
        _put(ws1, r, 13, it.get("owner") or "⚠未配置",
             font=Font(size=10, bold=not it.get("owner"),
                       color="C62828" if not it.get("owner") else "000000"),
             fill=_row_fill)
        # 人工登记（2026-10-15）：跟着日期走的在研备注，真源在生成器之外的
        # `资产文件\事中日报人工登记.xlsx`（见 sku_note.py）—— 日报每天被
        # 幂等覆盖，备注不能手填在表里。留空即无备注。
        # 2026-10-29：备注≠豁免 —— 有备注仍在追踪的 SKU 显式标「⚠未豁免」，
        # 别再让业务以为"写一句暂时不弄了"就生效（G591 连报 60 天的教训）。
        _note = (notes or {}).get(it.get("sku") or "")
        if _note:
            c = _put(ws1, r, 14, f"{_note} ⚠未豁免", fill=_row_fill)
            c.font = Font(size=10, bold=True, color="C62828")
        # 素材（2026-11-05）：通用 = Part6 免寄送拍摄(15天)+素材加工(3天)。
        # 只作标注，**不改段规则/不参与状态判定** —— 事中口径本轮不动。
        # 「—」= Part6 单据还没建，拍摄列无处可填，暂判不出（默认按需拍摄）。
        _mat = "通用" if it.get("genericMaterial") else (
            "—" if (it.get("genericBasis") or "") == _BASIS_UNKNOWN else "需拍摄")
        c = _put(ws1, r, 15, _mat, align=Alignment(horizontal="center"),
                 fill=_row_fill)
        if _mat == "通用":
            c.font = Font(size=10, bold=True, color="2E7D32")
        # 样机流转（2026-11-06）：签样 → 拍摄交付。日期列没填就留**空白**
        # （用户口径「实际没有就写空」），判定写「待补」而不是「不合格」——
        # 数据没填不该算到业务头上；间隔与合格线由 sample_cycle.label 统一定义，
        # 复盘页与日报共用同一套措辞（不在这里另写一套 15 天）。
        if _SC:
            _cyc = _SC.get(it.get("sku")) or {}
            _lb = _sc_label(_cyc) if _cyc else {"days": "—", "judge": "—",
                                                "tone": "muted", "note": ""}
            _put(ws1, r, 16, _cyc.get("start") or "", fill=_row_fill,
                 font=Font(size=10, color="616161")
                 if not _cyc.get("start") else None,
                 align=Alignment(horizontal="center"))
            _put(ws1, r, 17, _cyc.get("end") or "", fill=_row_fill,
                 font=Font(size=10, color="616161")
                 if not _cyc.get("end") else None,
                 align=Alignment(horizontal="center"))
            # 天数算不出来就写**空**（不是「—」）：用户口径「实际没有就写空」，
            # 空格在这一列里就代表「还起算不了」，比符号更不容易被误读成 0。
            _sc_days = "" if _lb["days"] in ("—", "-", None) else _lb["days"]
            _put(ws1, r, 18, _sc_days, fill=_row_fill,
                 font=Font(size=10, bold=_lb["tone"] == "bad",
                           color={"ok": "2E7D32", "bad": "C62828",
                                  "muted": "9E9E9E"}[_lb["tone"]]),
                 align=Alignment(horizontal="center"))
            _put(ws1, r, 19, _lb["judge"], fill=_row_fill,
                 font=Font(size=10, bold=_lb["tone"] != "muted",
                           color={"ok": "2E7D32", "bad": "C62828",
                                  "muted": "9E9E9E"}[_lb["tone"]]),
                 align=Alignment(horizontal="center"))
        r += 1
    if r == 5:
        _put(ws1, 5, 1, "（当前无在研 SKU）")

    # ---- Sheet2 超期明细 ----
    # 2026-10-29：+4 列段时间追溯（起点/起点来源/终点/终点来源）——
    # 天数怎么算出来的要能对到原始字段（系统业务列 / audit上报 / 审批动作），
    # 催办时直接甩证据；责任人缺失显式标「⚠未配置」，别再以「—」蒙混。
    _TIME_COLS = ["起点时间", "起点来源", "终点时间", "终点来源"]
    ws2 = wb.create_sheet("超期明细")
    ws2["A1"] = "进行中超期段（终点未完成，elapsed > 目标天）"
    ws2["A1"].font = TITLE_FONT
    _head_row(ws2, 3, ["SKU", "部门", "环节", "已进行(天)", "目标(天)",
                       "超期(天)", "责任人", "起点时间", "起点来源",
                       "终点时间", "终点来源"],
              widths=[14, 8, 22, 10, 9, 9, 12, 17, 9, 17, 9])
    r = 4
    det_src = []
    for it in items or []:
        det_src.append(it)
    for x in (alerts or {}).get("ongoingOverdue", []) + \
             (alerts or {}).get("todayNewOverdue", []):
        _put(ws2, r, 1, x.get("sku"))
        _put(ws2, r, 2, x.get("dept") or "—")
        _put(ws2, r, 3, x.get("seg"))
        _put(ws2, r, 4, x.get("elapsed"), align=Alignment(horizontal="center"))
        _put(ws2, r, 5, x.get("target"), align=Alignment(horizontal="center"))
        c = _put(ws2, r, 6, x.get("overDays"),
                 align=Alignment(horizontal="center"))
        c.font = Font(size=10, bold=True, color="C62828")
        if x.get("owner"):
            _put(ws2, r, 7, x["owner"])
        else:
            _put(ws2, r, 7, "⚠未配置",
                 font=Font(size=10, bold=True, color="C62828"))
        _put(ws2, r, 8, x.get("start") or "—")
        _put(ws2, r, 9, x.get("src0") or "—",
             align=Alignment(horizontal="center"))
        _put(ws2, r, 10, x.get("end") or "进行中",
             font=None if x.get("end") else Font(size=10, color="1F4E79"))
        _put(ws2, r, 11, x.get("src1") or "—",
             align=Alignment(horizontal="center"))
        r += 1
    if r == 4:
        _put(ws2, 4, 1, "（无进行中超期段）")

    # ---- Sheet3 风险预警（85% 环节工期阈值，P1-2） ----
    # 2026-10-29：标题写明实际预警系数（此前只写"预警系数"没给值，像黑箱）；
    # 底部加「无目标段」小节 —— target=null 的段（如 G597 亚马逊沙特 43 天未发货）
    # 此前完全绕过超期/预警，等于免检金牌，至少要让人看见它挂在哪。
    # 实际预警系数：alerts.warnings 行带 ratio（compute_alerts_today 注入）
    _warn_rows = (alerts or {}).get("warnings") or []
    _ratio = float(_warn_rows[0].get("ratio") or 0.85) if _warn_rows else 0.85
    ws3 = wb.create_sheet("风险预警")
    ws3["A1"] = (f"即将超期预警（已进行 > 目标 × {_ratio:g} 预警系数，未到目标；"
                 "超期段不进本表，见「超期明细」）")
    ws3["A1"].font = TITLE_FONT
    _head_row(ws3, 3, ["SKU", "部门", "环节", "已进行(天)", "目标(天)",
                       "预警线(天)", "责任人"],
              widths=[14, 8, 22, 10, 9, 10, 12])
    r = 4
    for w in (alerts or {}).get("warnings", []):
        _put(ws3, r, 1, w.get("sku"))
        _put(ws3, r, 2, w.get("dept") or "—")
        _put(ws3, r, 3, w.get("seg"))
        _put(ws3, r, 4, w.get("elapsed"), align=Alignment(horizontal="center"))
        _put(ws3, r, 5, w.get("target"), align=Alignment(horizontal="center"))
        c = _put(ws3, r, 6, round(float(w["target"]) * float(w.get("ratio", 0.85)), 2),
                 align=Alignment(horizontal="center"))
        c.font = Font(size=10, color="EF6C00")
        if w.get("owner"):
            _put(ws3, r, 7, w["owner"])
        else:
            _put(ws3, r, 7, "⚠未配置",
                 font=Font(size=10, bold=True, color="C62828"))
        r += 1
    if r == 4:
        _put(ws3, 4, 1, "（无预警段）")
        r = 5
    # 无目标段小节（免检金牌可见性）：已停留但未设目标天的段
    _no_tgt = [it for it in items or []
               if it.get("stayTarget") is None
               and it.get("stayElapsed") is not None
               and float(it.get("stayElapsed") or 0) > 0]
    if _no_tgt:
        r += 1
        c = ws3.cell(row=r, column=1, value="⚠ 无目标段（未设目标天，不参与超期/预警判定 —— 需要人自己判断要不要催）")
        c.font = Font(bold=True, size=10, color="EF6C00")
        r += 1
        _head_row(ws3, r, ["SKU", "当前停留环节", "已停留(天)", "责任人"],
                  widths=None)
        r += 1
        for it in sorted(_no_tgt, key=lambda x: -(x.get("stayElapsed") or 0)):
            _put(ws3, r, 1, it.get("sku"))
            _put(ws3, r, 2, it.get("staySeg") or "—")
            _put(ws3, r, 3, it.get("stayElapsed"),
                 align=Alignment(horizontal="center"))
            _put(ws3, r, 4, it.get("owner") or "⚠未配置")
            r += 1

    # ---- Sheet4 在研SKU甘特图（2026-10-06 替换原「今日进度变化」）----
    ws4 = wb.create_sheet("在研SKU甘特图")
    _gantt_sheet_inflight(ws4, gantts or [], date_str)

    # ---- Sheet5 异常上报清单（P0-7） ----
    # 2026-10-29 三处升级：① 超期 ≥30 天的类型升级「长期未决」（深红加粗，
    # 别再让 190 天和 2 天一个权重）；② 持续超期行若该 SKU 人工登记了备注
    # → 标「已备注未豁免」（备注≠豁免，G591 连报 60 天的教训）；③ 货件超期
    # （在途票已运输 > 目标天）并入上报 —— 此前流程超期与运输超期两套口径不打通。
    ws5 = wb.create_sheet("异常上报清单")
    ws5["A1"] = ("异常上报（当日新超期 / 持续超期累计 / 长期未决≥30天 / 货件超期；"
                 "「已备注未豁免」= 登记了备注但未匹配豁免通道，仍在追踪）")
    ws5["A1"].font = TITLE_FONT
    _head_row(ws5, 3, ["类型", "SKU", "部门", "环节", "超期(天)", "已进行(天)",
                       "责任人", "起点时间", "起点来源", "终点时间", "终点来源"],
              widths=[16, 14, 8, 22, 10, 10, 12, 17, 9, 17, 9])
    r = 4
    for x in (alerts or {}).get("todayNewOverdue", []):
        _put(ws5, r, 1, "当日新超期", font=Font(size=10, bold=True,
                                               color="C62828"))
        _fill_report_row(ws5, r, x)
        r += 1
    for x in (alerts or {}).get("ongoingOverdue", []):
        _esc = isinstance(x.get("overDays"), (int, float)) \
            and x["overDays"] >= OVER_ESCALATE
        _noted = bool((notes or {}).get(x.get("sku") or ""))
        if _esc:
            _label, _color, _bold = "长期未决·≥30天", "7B0000", True
        elif _noted:
            _label, _color, _bold = "持续超期·已备注未豁免", "C62828", True
        else:
            _label, _color, _bold = "持续超期", "EF6C00", False
        _put(ws5, r, 1, _label, font=Font(size=10, bold=_bold, color=_color))
        _fill_report_row(ws5, r, x)
        r += 1
    # 货件超期（运输口径并入上报）：在途票已运输天数 > 目标天（到货超期的票
    # 已在「留观」中自然暴露，红字见货件运输表）
    for x in shipments or []:
        _el, _tg = x.get("elapsed"), x.get("target")
        if _el is None or _tg is None or x.get("arr"):
            continue
        if not float(_el) > float(_tg):
            continue
        _put(ws5, r, 1, "货件超期", font=Font(size=10, bold=True,
                                             color="7030A0"))
        _put(ws5, r, 2, x.get("sku"))
        _put(ws5, r, 3, "运输")
        _put(ws5, r, 4, f"{x.get('hub') or '—'} {x.get('trk') or ''}".strip())
        c = _put(ws5, r, 5, round(float(_el) - float(_tg), 2),
                 align=Alignment(horizontal="center"))
        c.font = Font(size=10, bold=True, color="C62828")
        _put(ws5, r, 6, _el, align=Alignment(horizontal="center"))
        _ow = owner_of.get(x.get("sku") or "")
        if _ow:
            _put(ws5, r, 7, _ow)
        else:
            _put(ws5, r, 7, "⚠未配置",
                 font=Font(size=10, bold=True, color="C62828"))
        _put(ws5, r, 8, x.get("ship") or "—")
        _put(ws5, r, 9, "系统", align=Alignment(horizontal="center"))
        _put(ws5, r, 10, "在途", font=Font(size=10, color="1F4E79"))
        _put(ws5, r, 11, "—", align=Alignment(horizontal="center"))
        r += 1
    if r == 4:
        _put(ws5, 4, 1, "（无待上报异常）")

    # ---- Sheet6 货件运输（2026-10-15：在研 SKU 真货件逐票；2026-11-01 起全量保留）----
    if shipments:
        ws_ship = wb.create_sheet("货件运输")
        ws_ship["A1"] = ("在研 SKU 货件运输（Part5 发货 × Part8 到货按货件号配对 · "
                         "全量保留：SKU 在研期间每一票都列，到货 ≤5 天标「留观N/5天」、"
                         "超出标「已归档(到货N天)」· 未发货渠道无货件号不进本表）")
        ws_ship["A1"].font = TITLE_FONT
        _head_row(ws_ship, 3, ["SKU", "型号", "货件号", "发往地区", "运输方式",
                               "发货日期", "到货日期", "已运输(天)", "目标(天)",
                               "状态"],
                  widths=[14, 20, 20, 14, 11, 12, 12, 10, 9, 18])
        r = 4
        for x in shipments:
            _put(ws_ship, r, 1, x.get("sku"))
            _put(ws_ship, r, 2, x.get("model") or "—")
            _put(ws_ship, r, 3, x.get("trk") or "—")
            _put(ws_ship, r, 4, x.get("hub") or "—")
            _put(ws_ship, r, 5, x.get("mode") or "—")
            _put(ws_ship, r, 6, x.get("ship") or "—",
                 align=Alignment(horizontal="center"))
            _put(ws_ship, r, 7, x.get("arr") or "—",
                 align=Alignment(horizontal="center"))
            # 已运输天数：在途超目标 → 红字加粗（「持续运输多少天」要能一眼看出超没超）
            elap = x.get("elapsed")
            tgt = x.get("target")
            over = (elap is not None and tgt is not None
                    and float(elap) > float(tgt))
            c = _put(ws_ship, r, 8, elap if elap is not None else "—",
                     align=Alignment(horizontal="center"))
            if over:
                c.font = Font(size=10, bold=True, color="C62828")
            _put(ws_ship, r, 9, tgt if tgt is not None else "—",
                 align=Alignment(horizontal="center"))
            # 状态列（2026-11-01）：全量保留后由行内 track 决定文案 ——
            # 在途（深蓝）/ 留观N/5天（绿）/ 已归档(到货N天)（灰，只留档不再催）
            if x.get("archived"):
                txt, color = (x.get("track") or "已归档"), "757575"
            elif x.get("state") == "已到货":
                txt, color = (x.get("track") or f"留观{x.get('keep')}/5天"), "2E7D32"
            else:
                txt, color = (x.get("track") or "在途"), "1F4E79"
            c = _put(ws_ship, r, 10, txt, align=Alignment(horizontal="center"))
            c.font = Font(size=10, color=color)
            r += 1

    # ---- Sheet8 本月已消化超期（2026-10-29 修复"完成即蒸发"）----
    # 此前完成段（✗超期）一走完，超期痕迹就从日报消失，要等复盘才统计得到。
    # 现在终点落在本月的消化超期按月留痕，也直接喂给绩效视角。
    ws_dg = wb.create_sheet("本月已消化超期")
    ws_dg["A1"] = ("本月已完成但超期的段（终点落在本月；按超期天数降序）——"
                   "超期了但按时补完的在这里对账，不再蒸发")
    ws_dg["A1"].font = TITLE_FONT
    _head_row(ws_dg, 3, ["SKU", "型号", "部门", "环节", "起点时间", "起点来源",
                         "终点时间", "终点来源", "耗时(天)", "目标(天)",
                         "超期(天)", "责任人"],
              widths=[14, 18, 8, 20, 17, 9, 17, 9, 10, 9, 10, 12])
    r = 4
    for x in sorted(digested or [], key=lambda v: -(v.get("over") or 0)):
        _put(ws_dg, r, 1, x.get("sku"))
        _put(ws_dg, r, 2, x.get("model") or "—")
        _put(ws_dg, r, 3, x.get("dept") or "—")
        _put(ws_dg, r, 4, x.get("seg"))
        _put(ws_dg, r, 5, x.get("start") or "—")
        _put(ws_dg, r, 6, x.get("src0") or "—",
             align=Alignment(horizontal="center"))
        _put(ws_dg, r, 7, x.get("end") or "—")
        _put(ws_dg, r, 8, x.get("src1") or "—",
             align=Alignment(horizontal="center"))
        c = _put(ws_dg, r, 9, x.get("days"), align=Alignment(horizontal="center"))
        c.font = Font(size=10, color="C62828", bold=True)
        _put(ws_dg, r, 10, x.get("target"), align=Alignment(horizontal="center"))
        c = _put(ws_dg, r, 11, x.get("over"), align=Alignment(horizontal="center"))
        c.font = Font(size=10, bold=True, color="C62828")
        if x.get("owner"):
            _put(ws_dg, r, 12, x["owner"])
        else:
            _put(ws_dg, r, 12, "⚠未配置",
                 font=Font(size=10, bold=True, color="C62828"))
        r += 1
    if r == 4:
        _put(ws_dg, 4, 1, "（本月暂无已消化超期段——好现象）")

    # ---- Sheet9 本月绩效视角（2026-10-29：按责任人汇总）----
    # 给 HR / 部门负责人的第一步：不用透视就能看到"谁名下多少超期、消化了多少"。
    # ⚠ 口径：只统计事中可见的超期段与本月消化段，权威绩效仍以复盘报告为准。
    ws_pf = wb.create_sheet("本月绩效视角")
    ws_pf["A1"] = ("本月绩效视角（按责任人）—— 只统计事中可见口径"
                   "（在研超期段 + 本月已消化超期段），权威统计以复盘报告为准")
    ws_pf["A1"].font = TITLE_FONT
    _head_row(ws_pf, 3, ["责任人", "在研超期段数", "其中长期未决(≥30天)",
                         "当日新超期", "本月已消化超期段"],
              widths=[14, 14, 18, 12, 18])
    _pf = {}
    def _pf_of(name):
        key = name or "(未配置)"
        return _pf.setdefault(key, {"over": 0, "esc": 0, "new": 0, "dig": 0})
    for x in (alerts or {}).get("ongoingOverdue", []):
        _e = _pf_of(x.get("owner"))
        _e["over"] += 1
        if isinstance(x.get("overDays"), (int, float)) \
                and x["overDays"] >= OVER_ESCALATE:
            _e["esc"] += 1
    for x in (alerts or {}).get("todayNewOverdue", []):
        _e = _pf_of(x.get("owner"))
        _e["over"] += 1
        _e["new"] += 1
    for x in digested or []:
        _pf_of(x.get("owner"))["dig"] += 1
    r = 4
    for name, e in sorted(_pf.items(), key=lambda kv: (-kv[1]["over"],
                                                       -kv[1]["dig"], kv[0])):
        _put(ws_pf, r, 1, name)
        c = _put(ws_pf, r, 2, e["over"], align=Alignment(horizontal="center"))
        c.font = Font(size=10, bold=e["over"] > 0,
                      color="C62828" if e["over"] else "000000")
        c = _put(ws_pf, r, 3, e["esc"], align=Alignment(horizontal="center"))
        c.font = Font(size=10, bold=e["esc"] > 0, color="7B0000" if e["esc"] else "000000")
        _put(ws_pf, r, 4, e["new"], align=Alignment(horizontal="center"))
        _put(ws_pf, r, 5, e["dig"], align=Alignment(horizontal="center"))
        r += 1
    if r == 4:
        _put(ws_pf, 4, 1, "（本月无任何超期记录——好现象）")

    # ---- Sheet7 豁免备查（2026-10-06 口径合并：不进追踪的 SKU 单列备查） ----
    # 2026-10-29 排序修正：人工主动豁免（人工登记/换号/末端终止）在前，
    # 运输闭环（在售完结件，曾占 86% 淹没备查表）沉底 + 灰字淡化。
    if exempts:
        ws7 = wb.create_sheet("豁免备查")
        ws7["A1"] = ("已移出追踪的 SKU（人工登记 / 换号 / 末端终止 / 运输闭环）——"
                     "前三种=人工主动豁免（真源 app/exemption.py），"
                     "运输闭环=在售完结件仅备查（真源 app/transport_closure）")
        ws7["A1"].font = TITLE_FONT
        _head_row(ws7, 3, ["SKU", "型号", "豁免类型", "原因"],
                  widths=[14, 20, 12, 60])
        kind_label = {"hold": "人工登记", "alias": "换号", "void": "末端终止",
                      "运输闭环": "运输闭环"}
        _ex_sorted = sorted(exempts,
                            key=lambda x: 1 if x.get("kind") == "运输闭环" else 0)
        r = 4
        for x in _ex_sorted:
            _is_closed = x.get("kind") == "运输闭环"
            _put(ws7, r, 1, x.get("sku"))
            _put(ws7, r, 2, x.get("model") or "—")
            c = _put(ws7, r, 3, kind_label.get(x.get("kind"), x.get("kind") or "—"),
                     align=Alignment(horizontal="center"))
            c.font = Font(size=10, color="9E9E9E" if _is_closed else "000000")
            c = _put(ws7, r, 4, x.get("reason") or "—")
            if _is_closed:
                c.font = Font(size=10, color="9E9E9E")
            r += 1
        if r == 4:
            _put(ws7, 4, 1, "（无豁免 SKU）")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _fill_report_row(ws, r, x):
    _put(ws, r, 2, x.get("sku"))
    _put(ws, r, 3, x.get("dept") or "—")
    _put(ws, r, 4, x.get("seg"))
    c = _put(ws, r, 5, x.get("overDays"), align=Alignment(horizontal="center"))
    c.font = Font(size=10, bold=True, color="C62828")
    _put(ws, r, 6, x.get("elapsed"), align=Alignment(horizontal="center"))
    if x.get("owner"):
        _put(ws, r, 7, x["owner"])
    else:
        _put(ws, r, 7, "⚠未配置", font=Font(size=10, bold=True, color="C62828"))
    # 段时间追溯（2026-10-29）：起点/终点原始时间 + 来源（系统/audit/动作）
    _put(ws, r, 8, x.get("start") or "—")
    _put(ws, r, 9, x.get("src0") or "—", align=Alignment(horizontal="center"))
    _put(ws, r, 10, x.get("end") or "进行中",
         font=None if x.get("end") else Font(size=10, color="1F4E79"))
    _put(ws, r, 11, x.get("src1") or "—", align=Alignment(horizontal="center"))


# ---------------------------------------------------------------------------
# 在研 SKU 流程甘特（2026-10-06 替换原「今日进度变化」sheet）
# 画法 1:1 抄 流程复盘/gantt_sheet.py（route_check_sys 汇总 xlsx 同款）：
# 全局日历轴每格=1天 / 月份合并轴 / 部门列纵向合并 / 条形超出目标部分红色；
# 事中差异：未完成段条形延伸至今日（当前停留可见）、只画在研 SKU、区块新→旧。
# 2026-10-19 用户要求"学复盘的程序，把日期也带上"→ 标签列由 6 列扩到 8 列，
# 与复盘 dates_mode 完全一致：A部门 B环节 C起点 D终点 E耗时 F目标 G判定 H执行人。
# ---------------------------------------------------------------------------
G_CAL0 = 9                              # 日历轴起始列（I；标签列 A-H）
G_LABEL_W = {"A": 7, "B": 38, "C": 11, "D": 10, "E": 8.5, "F": 8.5,
             "G": 10, "H": 10}
G_DEPT_FILL = {"产品": "4472C4", "运营": "70AD47", "后勤": "ED7D31",
               "运输": "7030A0", "4国": "BF8F00", "应发未发": "C00000"}
G_DEPT_HEAD = {"产品": "D9E1F2", "运营": "E2EFDA", "后勤": "FCE4D6",
               "运输": "E4DFEC", "4国": "FFF2CC", "留空": "F2F2F2",
               "__GAP__": "F2F2F2", "应发未发": "F4CCCC"}
G_DEPT_LABEL = {"产品": "产品部", "运营": "运营部", "后勤": "后勤部",
                "运输": "运输", "4国": "4国单独", "__GAP__": "留空",
                "应发未发": "应发未发"}
G_RED = "FF3B30"                        # 条形超出目标的部分
G_NEUTRAL = "A6A6A6"                    # 留空段 / 未设目标段（灰条）
G_TITLE_FILL = "D9E2F3"
G_AXIS_FILL = "262626"
G_THIN_BOTTOM = Border(bottom=Side(style="thin", color="BFBFBF"))


def _g_fill(hex6):
    return PatternFill("solid", start_color=hex6, end_color=hex6)


def _g_judge_color(judge):
    s = str(judge or "")
    if s.startswith("✗"):
        return "C00000"
    if s.startswith("✓"):
        return "1F7A1F"
    if s.startswith("⚠"):
        return "B36A00"
    return "808080"


# 开发类型标注（2026-11-01 用户要求）：提前开发的窗口比常规短，甘特图上必须
# 一眼可辨；未填也要标出来，不能默默当常规处理（否则排期判断会错）。
# 值 = (文案, 标题行底色, 字色)
G_DEV_STYLE = {
    "常规开发": ("常规开发", "D9E2F3", "1F3864"),    # 浅蓝（沿用原标题底色）
    "提前开发": ("⚡提前开发", "FFD966", "7F4F00"),   # 橙黄：醒目，扫一眼能挑出来
    "":         ("开发类型未填", "F2F2F2", "808080"),  # 灰：显式标注，不是漏画
}


def _g_dev_style(dev_type):
    """开发类型 → (文案, 底色, 字色)。

    各品类取值不一样：手机膜是「常规开发 / 提前开发」，手机壳是
    「常规手机壳开发 / IP手机壳开发」。所以按关键字判配色、**文案保留原值** ——
    硬塞进两类会丢信息（IP 款和普通常规款的排期逻辑并不相同）。
    """
    key = str(dev_type or "").strip()
    if not key or key in ("None", "nan", "NaT"):
        return G_DEV_STYLE[""]
    if "提前" in key:
        return (f"⚡{key}", "FFD966", "7F4F00")
    if "常规" in key:
        return (key, "D9E2F3", "1F3864")
    return (key, "FFF2CC", "7F4F00")     # 其他取值（如 IP手机壳开发）原样展示


def _g_launch_text(launch):
    """官方发布时间文案。返回 None = 该品类没有这一列，标题行不显示这一段。

    - None（字段不存在，如手机壳）→ None
    - ""（有字段但没填）→ 「未填」：必须显式写，留空会被当成"这行没画"
    上游偶有带时刻的写法（如 2026-10-13 00:00:00），只取日期部分。
    """
    if launch is None:
        return None
    s = str(launch).strip()
    if not s or s in ("None", "nan", "NaT"):
        return "未填"
    return s[:10] if len(s) >= 10 else s


def _g_over_text(item):
    """甘特图每行末尾的「超期」列文案：达标打勾，超期给天数。

    与左侧 G 列判定同源（都用 judge），去掉开头的符号避免与颜色重复表达；
    进行中且已进行天数为空时不再拼出「已None天」。
    """
    judge = str(item.get("judge") or "")
    # 运输·在途行：判定列写「⏳进行中(已N天)」，最右列直接给票数更实用
    if item.get("pendingCount"):
        return f"在途{item['pendingCount']}票"
    if judge.startswith("✓"):
        return "✓"
    if judge[:1] in ("✗", "⚠", "⏳"):
        return judge[1:] or "—"
    return "—"


def _g_fmt_dt(t):
    """起止时间展示（抄复盘 gantt_sheet 的 dates_mode）：零点只显 MM-DD，
    带时刻显 MM-DD HH:MM —— 节点时间精确到分时别丢掉。"""
    if not isinstance(t, datetime):
        return "—"
    if (t.hour, t.minute, t.second) == (0, 0, 0):
        return f"{t:%m-%d}"
    return f"{t:%m-%d %H:%M}"


def _g_left_text(dl):
    """距超时天数的展示文案（与总表第 8 列同口径）。"""
    if dl is None:
        return "—"
    if dl < 0:
        return f"已超{-dl:.0f}天"
    if dl == 0:
        return "今日到期"
    return f"剩{dl:.0f}天"


def _gantt_sheet_inflight(ws, records, date_str):
    """在研 SKU 甘特主入口：records = inflight_service._gantt_records() 产物。"""
    try:
        today = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        today = datetime.now().date()
    ws.cell(1, 1, (
        f"在研 SKU 流程甘特图（截至 {today:%Y-%m-%d} · 每格=1天 · "
        "条形超出目标的部分=红色 · 未完成段延伸至今日、终点列写「进行中」）   "
        "C/D=该段起点/终点（有具体时刻的显示 MM-DD HH:MM，纯日期只显 MM-DD）· "
        "颜色：■产品部(蓝) ■运营部(绿) ■后勤部(橙) ■运输(紫) ■4国(金) "
        "■留空/无目标(灰) · SKU 区块按新→旧排列")).font = Font(bold=True, size=11)

    starts, ends = [], []
    for rec in records:
        for it in rec["rows"]:
            if it.get("t0"):
                starts.append(it["t0"])
            if it.get("t1"):
                ends.append(it["t1"])
    if not starts or not ends:
        ws.cell(3, 1, "（无可用时间轴：在研 SKU 均无节点时间）").font = CELL_FONT
        return
    axis_start = (min(starts) - timedelta(days=1)).date()
    axis_end = max((max(ends) + timedelta(days=2)).date(), today)
    n_days = (axis_end - axis_start).days + 1
    last_col = G_CAL0 + n_days - 1
    over_col = last_col + 1              # 超期列：日期轴右侧，每行标超期天数/打勾
    note_col = over_col + 1              # 在途明细列：「已发N·到M·未到K：站点…」

    for col, w in G_LABEL_W.items():
        ws.column_dimensions[col].width = w
    for i in range(n_days):
        ws.column_dimensions[get_column_letter(G_CAL0 + i)].width = 3.0
    ws.column_dimensions[get_column_letter(over_col)].width = 11
    ws.column_dimensions[get_column_letter(note_col)].width = 46
    for c in range(1, note_col + 1):
        ws.cell(1, c).fill = _g_fill("FFF2CC")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=note_col)
    ws.row_dimensions[1].height = 20
    ws.row_dimensions[2].height = 6
    ws.freeze_panes = f"{get_column_letter(G_CAL0)}3"

    row = 3
    for rec in records:
        row = _g_block(ws, row, rec, axis_start, n_days, last_col, over_col,
                       today, note_col)


def _g_block(ws, top, rec, axis_start, n_days, last_col, over_col, today,
             note_col=None):
    """一个 SKU 区块：标题行 + 月份合并轴 + 日期行 + 条形行（部门纵向合并）。

    2026-11-01 用户要求补两项标注：
      · 标题行带「开发类型 · 官方发布时间」—— 提前开发底色换橙黄，未填显式写
        「开发类型未填」（不能默默当常规，否则排期判断会错）；
      · 日期轴右侧加「超期」列，每行给超期天数、达标打勾 —— 横向滚到条形末端
        时仍能看到状态，不必滚回左侧标签列。
    """
    dev_text, dev_fill, dev_color = _g_dev_style(rec.get("devType"))
    launch = _g_launch_text(rec.get("launchDate"))
    if note_col is None:
        note_col = over_col + 1
    # 该品类没有「官方发布时间」列时（手机壳）只显示开发类型，不写「发布 未填」
    meta = dev_text if launch is None else f"{dev_text} · 发布 {launch}"
    title = (f"{rec['sku']} · {rec.get('model') or '—'}    "
             f"{meta}    "
             f"状态: {rec.get('status') or '—'}    "
             f"当前停留: {rec.get('staySeg') or '—'}    "
             f"距超时: {_g_left_text(rec.get('stayDaysLeft'))}")
    for c in range(1, note_col + 1):
        ws.cell(top, c).fill = _g_fill(dev_fill)
    ws.cell(top, 1, title).font = Font(bold=True, size=10, color=dev_color)
    ws.cell(top, 1).alignment = Alignment(vertical="center")
    ws.merge_cells(start_row=top, start_column=1, end_row=top, end_column=note_col)
    ws.row_dimensions[top].height = 20

    # 月份行（深色底白字，按自然月合并）
    mrow = top + 1
    c = 0
    while c < n_days:
        d = axis_start + timedelta(days=c)
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        span = min((nxt - d).days, n_days - c)
        for k in range(span):
            ws.cell(mrow, G_CAL0 + c + k).fill = _g_fill(G_AXIS_FILL)
        ws.cell(mrow, G_CAL0 + c, f"{d:%Y-%m}").font = Font(
            bold=True, size=9, color="FFFFFF")
        ws.cell(mrow, G_CAL0 + c).alignment = Alignment(
            horizontal="center", vertical="center")
        ws.merge_cells(start_row=mrow, start_column=G_CAL0 + c,
                       end_row=mrow, end_column=G_CAL0 + c + span - 1)
        c += span
    ws.row_dimensions[mrow].height = 14

    # 日期行（每月 1 日加粗）
    drow = mrow + 1
    for c in range(n_days):
        d = axis_start + timedelta(days=c)
        cell = ws.cell(drow, G_CAL0 + c, d.day)
        cell.font = Font(bold=(d.day == 1), size=7,
                         color="404040" if d.day == 1 else "909090")
        cell.alignment = Alignment(horizontal="center")
    ws.row_dimensions[drow].height = 12
    # 超期列表头（与日期行同高，让读者知道最右那列是什么）
    hc = ws.cell(drow, over_col, "超期")
    hc.font = Font(bold=True, size=8, color="404040")
    hc.alignment = Alignment(horizontal="center")
    # 在途明细列表头（只有「运输·在途」行会有内容）
    nc = ws.cell(drow, note_col, "在途明细")
    nc.font = Font(bold=True, size=8, color="404040")
    nc.alignment = Alignment(horizontal="center")

    # 条形行（部门分组，A 列纵向合并）
    r = drow + 1
    grp_from, prev_dept = r, None
    for item in rec["rows"]:
        dept = item.get("dept") or ""
        if prev_dept is not None and dept != prev_dept:
            _g_merge_dept(ws, grp_from, r - 1, prev_dept)
            grp_from = r
        prev_dept = dept
        cost = item.get("days")
        target = item.get("target")
        judge = str(item.get("judge") or "-")
        jc = _g_judge_color(judge)
        # 标签列与复盘 dates_mode 一致：C 起点 / D 终点（未完成段写「进行中」，
        # 因为它的终点是"延伸到今日"的填充值，不能伪装成完成日期）
        ws.cell(r, 2, item.get("seg")).font = Font(size=9)
        ws.cell(r, 3, _g_fmt_dt(item.get("t0"))).font = Font(size=8, color="404040")
        if item.get("open"):
            ws.cell(r, 4, "进行中").font = Font(size=8, color="1F4E79")
        else:
            ws.cell(r, 4, _g_fmt_dt(item.get("t1"))).font = Font(size=8, color="404040")
        ws.cell(r, 5, cost if cost is not None else "—").font = Font(size=9)
        ws.cell(r, 6, f"≤{target:g}" if target is not None else "—").font = Font(size=9)
        ws.cell(r, 7, judge).font = Font(size=9, bold=judge.startswith(("✓", "✗")),
                                         color=jc)
        ws.cell(r, 8, item.get("owner") or "—").font = Font(size=9)
        for cc in range(2, G_CAL0):
            ws.cell(r, cc).border = G_THIN_BOTTOM
            ws.cell(r, cc).alignment = Alignment(vertical="center", wrap_text=False)
        base = (G_NEUTRAL if item.get("neutral")
                else G_DEPT_FILL.get(dept, G_NEUTRAL))
        _g_bar(ws, r, item, base, axis_start, n_days)
        # 超期列（日期轴右侧）：达标打勾，超期给天数。与左侧 G 列同源（judge），
        # 目的是横向滚到条形末端时也能看到状态，不用滚回标签列。
        ot = _g_over_text(item)
        oc = ws.cell(r, over_col, ot)
        oc.font = Font(size=9, bold=(ot == "✓" or ot.startswith(("超", "在途"))),
                       color=jc)
        oc.alignment = Alignment(horizontal="center", vertical="center")
        oc.border = G_THIN_BOTTOM
        # 在途明细：「已发15·到10·未到5：菲律宾、印尼、泰国、加拿大」
        nt = item.get("noteText")
        ncell = ws.cell(r, note_col, nt or None)
        ncell.border = G_THIN_BOTTOM
        if nt:
            ncell.font = Font(size=8, color="404040")
            ncell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.row_dimensions[r].height = 30 if nt else 16
        r += 1
    _g_merge_dept(ws, grp_from, r - 1, prev_dept)
    return r + 1          # 区块间空行


def _g_merge_dept(ws, r_from, r_to, dept):
    if r_to < r_from or not dept:
        return
    for rr in range(r_from, r_to + 1):
        ws.cell(rr, 1).fill = _g_fill(G_DEPT_HEAD.get(dept, "F2F2F2"))
    ws.cell(r_from, 1, G_DEPT_LABEL.get(dept, f"{dept}部")).font = Font(
        bold=True, size=9)
    ws.cell(r_from, 1).alignment = Alignment(horizontal="center",
                                             vertical="center", wrap_text=True)
    ws.merge_cells(start_row=r_from, start_column=1, end_row=r_to, end_column=1)


def _g_bar(ws, row, item, base_color, axis_start, n_days, cal0=G_CAL0):
    """条形：起点→终点逐日填充；相对起点超出目标天的部分(≥ceil(目标天))红色。
    同 gantt_sheet._draw_bar 口径：目标 0/空（留空段）不切红。"""
    t0, t1 = item.get("t0"), item.get("t1")
    if t0 is None or t1 is None:
        return
    d0 = (t0.date() - axis_start).days
    d1 = (t1.date() - axis_start).days
    if d1 < d0:
        d0, d1 = d1, d0
    cut = math.ceil(target) if (target := item.get("target")) else None
    for c in range(max(d0, 0), min(d1, n_days - 1) + 1):
        rel = c - d0               # 距条形起点的天数（0=起点当日）
        over = cut is not None and rel >= cut and rel > 0
        ws.cell(row, cal0 + c).fill = _g_fill(G_RED if over else base_color)
