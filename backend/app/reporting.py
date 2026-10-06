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
from datetime import datetime

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
                 f"（提案→取数 75 天只挂异常、不进考核）"),
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
# P0-4 · 事中日报（5 sheets：在研总表 / 超期明细 / 风险预警 / 今日进度 / 上报清单）
# ---------------------------------------------------------------------------
def daily_report(*, category, computed_at, date_str, items, alerts,
                 progress, provisional=False, targets_source="", exempts=None):
    """生成事中日报 xlsx（bytes）。

    items    ：inflight overview 的在研 SKU 行；
    alerts   ：compute_alerts_today() 的 {todayNewOverdue, ongoingOverdue, warnings}；
    progress ：progress_for(days=1) 的 [{date, newNodes}]；
    provisional/targets_source：该品类的目标天/评级线是否仍是待校准的名义值
                （手机壳 = True）。为 True 时在总表标题下打一条橙色口径警示——
                日报是要拿去催办/上报的，口径不确定必须写在脸上。
    exempts  ：被豁免移出追踪的 SKU 明细（compute_inflight 的 exempted，
                [{sku, model, kind, reason}]）。非空时追加第 6 sheet「豁免备查」
                ——移出追踪但不吞数据，谁被豁免、为什么豁免，日报里要能对上账。
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
        ws1.merge_cells(start_row=2, start_column=1, end_row=2, end_column=12)
    _head_row(ws1, 3, ["SKU", "型号", "状态", "最差环节", "部门", "已进行(天)",
                       "目标(天)", "超期(天)", "预警段数", "多轮提交",
                       "核心原因", "责任人"],
              widths=[14, 18, 8, 20, 8, 10, 9, 9, 9, 10, 26, 10])
    r = 4
    for it in items or []:
        _put(ws1, r, 1, it.get("sku"))
        _put(ws1, r, 2, it.get("model") or "—")
        st = it.get("status")
        c = _put(ws1, r, 3, st, align=Alignment(horizontal="center"))
        c.font = Font(size=10, bold=st in ("超期", "预警"),
                      color="C62828" if st == "超期"
                      else ("EF6C00" if st == "预警" else "2E7D32"))
        _put(ws1, r, 4, it.get("worstSeg") or "—")
        _put(ws1, r, 5, it.get("worstSegDept") or "—")
        _put(ws1, r, 6, "—" if it.get("worstElapsed") is None
             else it.get("worstElapsed"), align=Alignment(horizontal="center"))
        _put(ws1, r, 7, "—" if it.get("worstTarget") is None
             else it.get("worstTarget"), align=Alignment(horizontal="center"))
        _put(ws1, r, 8, "—" if it.get("worstOverDays") is None
             else it.get("worstOverDays"),
             align=Alignment(horizontal="center"))
        _put(ws1, r, 9, len(it.get("warnSegs") or []),
             align=Alignment(horizontal="center"))
        _put(ws1, r, 10, "⚠ 是" if it.get("multiRound") else "—")
        rc = it.get("rootCause") or {}
        _put(ws1, r, 11, (f"{rc.get('type')}({rc.get('seg')})"
                          if rc.get("type") else "—"))
        _put(ws1, r, 12, it.get("owner") or "—")
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

    # ---- Sheet4 今日进度变化（P1-1） ----
    ws4 = wb.create_sheet("今日进度变化")
    ws4["A1"] = "今日新完成节点（对比上一快照）"
    ws4["A1"].font = TITLE_FONT
    _head_row(ws4, 3, ["快照日期", "SKU", "新完成节点", "完成时间"],
              widths=[12, 14, 22, 20])
    r = 4
    for p in progress or []:
        for n in p.get("newNodes") or []:
            _put(ws4, r, 1, p.get("date"))
            _put(ws4, r, 2, n.get("sku"))
            _put(ws4, r, 3, n.get("node"))
            _put(ws4, r, 4, n.get("at"))
            r += 1
    if r == 4:
        _put(ws4, 4, 1, "（今日无新完成节点）")

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

    # ---- Sheet6 豁免备查（2026-10-06 口径合并：不进追踪的 SKU 单列备查） ----
    if exempts:
        ws6 = wb.create_sheet("豁免备查")
        ws6["A1"] = ("已移出追踪的 SKU（人工登记 / 换号 / 末端终止作废）"
                     "—— 与加急预警同口径，判定真源 app/exemption.py")
        ws6["A1"].font = TITLE_FONT
        _head_row(ws6, 3, ["SKU", "型号", "豁免类型", "原因"],
                  widths=[14, 20, 12, 60])
        kind_label = {"hold": "人工登记", "alias": "换号", "void": "末端终止"}
        r = 4
        for x in exempts:
            _put(ws6, r, 1, x.get("sku"))
            _put(ws6, r, 2, x.get("model") or "—")
            c = _put(ws6, r, 3, kind_label.get(x.get("kind"), x.get("kind") or "—"),
                     align=Alignment(horizontal="center"))
            c.font = Font(size=10, color="9E9E9E")
            _put(ws6, r, 4, x.get("reason") or "—")
            r += 1
        if r == 4:
            _put(ws6, 4, 1, "（无豁免 SKU）")

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
