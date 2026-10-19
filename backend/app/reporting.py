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
                 notes=None, shipments=None):
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
               已运输天数 + 到货 5 天留观（超出不追踪，>5 天的行已在数据源丢弃）；
               未发货渠道无货件号不进本表。空 → 不产生空页。
    """
    wb = Workbook()
    dir_label = {"loss": "亏损", "gain": "增益", "none": "无"}

    # ---- Sheet1 在研 SKU 状态总表 ----
    ws1 = wb.active
    ws1.title = "在研SKU状态总表"
    ws1["A1"] = (f"流程事中日报 · {category} · {date_str}"
                 f"（生成于 {computed_at}）")
    ws1["A1"].font = TITLE_FONT
    if provisional:
        ws1["A2"] = (f"⚠ 目标口径待校准：本表「目标(天)/超期(天)/预警线」按"
                     f"「{targets_source or '继承名义值'}」判定，仅供预警参考，"
                     "不得作为既定 KPI 引用；业务确认后须重跑本日报。")
        ws1["A2"].font = Font(bold=True, size=10, color="EF6C00")
        ws1.merge_cells(start_row=2, start_column=1, end_row=2, end_column=14)
    _head_row(ws1, 3, ["SKU", "型号", "状态", "当前停留环节", "部门", "已停留(天)",
                       "目标(天)", "距超时(天)", "超期环节", "预警段数", "多轮提交",
                       "核心原因", "责任人", "人工登记"],
              widths=[14, 18, 8, 22, 8, 10, 9, 11, 26, 9, 10, 26, 10, 26])
    r = 4
    for it in items or []:
        _put(ws1, r, 1, it.get("sku"))
        _put(ws1, r, 2, it.get("model") or "—")
        st = it.get("status")
        c = _put(ws1, r, 3, st, align=Alignment(horizontal="center"))
        c.font = Font(size=10, bold=st in ("超期", "预警"),
                      color="C62828" if st == "超期"
                      else ("EF6C00" if st == "预警" else "2E7D32"))
        _put(ws1, r, 4, it.get("staySeg") or "—")
        _put(ws1, r, 5, it.get("staySegDept") or "—")
        _put(ws1, r, 6, "—" if it.get("stayElapsed") is None
             else it.get("stayElapsed"), align=Alignment(horizontal="center"))
        _put(ws1, r, 7, "—" if it.get("stayTarget") is None
             else it.get("stayTarget"), align=Alignment(horizontal="center"))
        # 距超时(天)：负 = 已超期 N 天（红），0 = 今日到期（红），
        # ≤2 天临近（橙），其余正常 —— 数字比"超期天数"更有行动性
        dl = it.get("stayDaysLeft")
        if dl is None:
            dl_txt, dl_color, dl_bold = "—", "616161", False
        elif dl < 0:
            dl_txt, dl_color, dl_bold = f"已超{-dl:.0f}天", "C62828", True
        elif dl == 0:
            dl_txt, dl_color, dl_bold = "今日到期", "C62828", True
        else:
            dl_txt = f"剩{dl:.0f}天"
            dl_color, dl_bold = ("EF6C00", True) if dl <= 2 else ("2E7D32", False)
        c = _put(ws1, r, 8, dl_txt, align=Alignment(horizontal="center"))
        c.font = Font(size=10, bold=dl_bold, color=dl_color)
        # 超期环节（2026-10-19）：状态=超期 判的是「任一未完成段超期」，而上一列
        # 「距超时」只看当前停留环节 —— 两列口径不同，同一行会同时出现
        # 「超期」+「剩3天」。这里写明超的到底是哪一段（G599/G600/G601 实例：
        # 签样→寄送 7天/目标5天 超2天，而当前停留的采购段还剩3天）。
        _ov = []
        for _o in (it.get("overSegs") or []):
            _od = _o.get("overDays")
            _ov.append(f"{_o.get('seg') or '—'} "
                       + (f"超{_od:g}天" if isinstance(_od, (int, float))
                          else "超期"))
        c = _put(ws1, r, 9, "；".join(_ov) if _ov else "—")
        if _ov:
            c.font = Font(size=10, color="C62828")
        _put(ws1, r, 10, len(it.get("warnSegs") or []),
             align=Alignment(horizontal="center"))
        _put(ws1, r, 11, "⚠ 是" if it.get("multiRound") else "—")
        rc = it.get("rootCause") or {}
        _put(ws1, r, 12, (f"{rc.get('type')}({rc.get('seg')})"
                          if rc.get("type") else "—"))
        _put(ws1, r, 13, it.get("owner") or "—")
        # 人工登记（2026-10-15）：跟着日期走的在研备注，真源在生成器之外的
        # `资产文件\事中日报人工登记.xlsx`（见 sku_note.py）—— 日报每天被
        # 幂等覆盖，备注不能手填在表里。留空即无备注。
        _note = (notes or {}).get(it.get("sku") or "")
        if _note:
            c = _put(ws1, r, 14, _note)
            c.font = Font(size=10, bold=True, color="1F4E79")
        r += 1
    if r == 4:
        _put(ws1, 4, 1, "（当前无在研 SKU）")

    # ---- Sheet2 超期明细 ----
    ws2 = wb.create_sheet("超期明细")
    ws2["A1"] = "进行中超期段（终点未完成，elapsed > 目标天）"
    ws2["A1"].font = TITLE_FONT
    _head_row(ws2, 3, ["SKU", "部门", "环节", "已进行(天)", "目标(天)",
                       "超期(天)", "责任人"],
              widths=[14, 8, 22, 10, 9, 9, 10])
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
        _put(ws2, r, 7, x.get("owner") or "—")
        r += 1
    if r == 4:
        _put(ws2, 4, 1, "（无进行中超期段）")

    # ---- Sheet3 风险预警（85% 环节工期阈值，P1-2） ----
    ws3 = wb.create_sheet("风险预警")
    ws3["A1"] = "即将超期预警（已进行 > 目标 × 预警系数，未到目标）"
    ws3["A1"].font = TITLE_FONT
    _head_row(ws3, 3, ["SKU", "部门", "环节", "已进行(天)", "目标(天)",
                       "预警线(天)", "责任人"],
              widths=[14, 8, 22, 10, 9, 10, 10])
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
        _put(ws3, r, 7, w.get("owner") or "—")
        r += 1
    if r == 4:
        _put(ws3, 4, 1, "（无预警段）")

    # ---- Sheet4 在研SKU甘特图（2026-10-06 替换原「今日进度变化」）----
    ws4 = wb.create_sheet("在研SKU甘特图")
    _gantt_sheet_inflight(ws4, gantts or [], date_str)

    # ---- Sheet5 异常上报清单（P0-7） ----
    ws5 = wb.create_sheet("异常上报清单")
    ws5["A1"] = "异常上报（当日新超期 + 持续超期累计）"
    ws5["A1"].font = TITLE_FONT
    _head_row(ws5, 3, ["类型", "SKU", "部门", "环节", "超期(天)", "已进行(天)",
                       "责任人"],
              widths=[12, 14, 8, 22, 10, 10, 10])
    r = 4
    for x in (alerts or {}).get("todayNewOverdue", []):
        _put(ws5, r, 1, "当日新超期", font=Font(size=10, bold=True,
                                               color="C62828"))
        _fill_report_row(ws5, r, x)
        r += 1
    for x in (alerts or {}).get("ongoingOverdue", []):
        _put(ws5, r, 1, "持续超期", font=Font(size=10, color="EF6C00"))
        _fill_report_row(ws5, r, x)
        r += 1
    if r == 4:
        _put(ws5, 4, 1, "（无待上报异常）")

    # ---- Sheet6 货件运输（2026-10-15：在研 SKU 真货件逐票）----
    if shipments:
        ws_ship = wb.create_sheet("货件运输")
        ws_ship["A1"] = ("在研 SKU 货件运输（Part5 发货 × Part8 到货按货件号配对 · "
                         "已到货票到货 5 天内保留，超出不追踪 · 未发货渠道无货件号不进本表）")
        ws_ship["A1"].font = TITLE_FONT
        _head_row(ws_ship, 3, ["SKU", "型号", "货件号", "发往地区", "运输方式",
                               "发货日期", "到货日期", "已运输(天)", "目标(天)",
                               "状态"],
                  widths=[14, 20, 20, 14, 11, 12, 12, 10, 9, 16])
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
            if x.get("state") == "已到货":
                txt, color = f"已到货·留观{x.get('keep')}/5天", "2E7D32"
            else:
                txt, color = "在途", "1F4E79"
            c = _put(ws_ship, r, 10, txt, align=Alignment(horizontal="center"))
            c.font = Font(size=10, color=color)
            r += 1

    # ---- Sheet7 豁免备查（2026-10-06 口径合并：不进追踪的 SKU 单列备查） ----
    if exempts:
        ws7 = wb.create_sheet("豁免备查")
        ws7["A1"] = ("已移出追踪的 SKU（人工登记 / 换号 / 末端终止作废 / 运输闭环）"
                     "—— 前三种与加急预警同口径（真源 app/exemption.py），"
                     "运输闭环真源 app/transport_closure.classify_transport")
        ws7["A1"].font = TITLE_FONT
        _head_row(ws7, 3, ["SKU", "型号", "豁免类型", "原因"],
                  widths=[14, 20, 12, 60])
        kind_label = {"hold": "人工登记", "alias": "换号", "void": "末端终止",
                      "运输闭环": "运输闭环"}
        r = 4
        for x in exempts:
            _put(ws7, r, 1, x.get("sku"))
            _put(ws7, r, 2, x.get("model") or "—")
            c = _put(ws7, r, 3, kind_label.get(x.get("kind"), x.get("kind") or "—"),
                     align=Alignment(horizontal="center"))
            c.font = Font(size=10, color="9E9E9E")
            _put(ws7, r, 4, x.get("reason") or "—")
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
    _put(ws, r, 7, x.get("owner") or "—")


# ---------------------------------------------------------------------------
# 在研 SKU 流程甘特（2026-10-06 替换原「今日进度变化」sheet）
# 画法 1:1 抄 流程复盘/gantt_sheet.py（route_check_sys 汇总 xlsx 同款）：
# 全局日历轴每格=1天 / 月份合并轴 / 部门列纵向合并 / 条形超出目标部分红色；
# 事中差异：未完成段条形延伸至今日（当前停留可见）、只画在研 SKU、区块新→旧。
# ---------------------------------------------------------------------------
G_CAL0 = 7                              # 日历轴起始列（G；标签列 A-F）
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
        "条形超出目标的部分=红色 · 未完成段延伸至今日）   "
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

    ws.column_dimensions["A"].width = 7
    ws.column_dimensions["B"].width = 38
    ws.column_dimensions["C"].width = 8.5
    ws.column_dimensions["D"].width = 8.5
    ws.column_dimensions["E"].width = 10
    ws.column_dimensions["F"].width = 10
    for i in range(n_days):
        ws.column_dimensions[get_column_letter(G_CAL0 + i)].width = 3.0
    for c in range(1, last_col + 1):
        ws.cell(1, c).fill = _g_fill("FFF2CC")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    ws.row_dimensions[1].height = 20
    ws.row_dimensions[2].height = 6
    ws.freeze_panes = f"{get_column_letter(G_CAL0)}3"

    row = 3
    for rec in records:
        row = _g_block(ws, row, rec, axis_start, n_days, last_col)


def _g_block(ws, top, rec, axis_start, n_days, last_col):
    """一个 SKU 区块：标题行 + 月份合并轴 + 日期行 + 条形行（部门纵向合并）。"""
    title = (f"{rec['sku']} · {rec.get('model') or '—'}    "
             f"状态: {rec.get('status') or '—'}    "
             f"当前停留: {rec.get('staySeg') or '—'}    "
             f"距超时: {_g_left_text(rec.get('stayDaysLeft'))}")
    for c in range(1, last_col + 1):
        ws.cell(top, c).fill = _g_fill(G_TITLE_FILL)
    ws.cell(top, 1, title).font = Font(bold=True, size=10)
    ws.cell(top, 1).alignment = Alignment(vertical="center")
    ws.merge_cells(start_row=top, start_column=1, end_row=top, end_column=last_col)
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
        ws.cell(r, 2, item.get("seg")).font = Font(size=9)
        ws.cell(r, 3, cost if cost is not None else "—").font = Font(size=9)
        ws.cell(r, 4, f"≤{target:g}" if target is not None else "—").font = Font(size=9)
        ws.cell(r, 5, judge).font = Font(size=9, bold=judge.startswith(("✓", "✗")),
                                         color=jc)
        ws.cell(r, 6, item.get("owner") or "—").font = Font(size=9)
        for cc in range(2, G_CAL0):
            ws.cell(r, cc).border = G_THIN_BOTTOM
            ws.cell(r, cc).alignment = Alignment(vertical="center", wrap_text=False)
        base = (G_NEUTRAL if item.get("neutral")
                else G_DEPT_FILL.get(dept, G_NEUTRAL))
        _g_bar(ws, r, item, base, axis_start, n_days)
        ws.row_dimensions[r].height = 16
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
