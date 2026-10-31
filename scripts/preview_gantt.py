#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成甘特图 HTML 预览（临时工具，用于确认 2026-11-01 新增标注的视觉效果）。

不替代 Excel 报告，只是把前几个 SKU 块画成网页，方便一眼确认：
  · 标题行是否带「开发类型 · 官方发布时间」
  · 提前开发是否用橙黄底色
  · 未填是否显式标「开发类型未填」
  · 日期轴右侧是否有「超期」列（✓ / 超N天 / 进行中 / —）
"""
import datetime
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.inflight_service import compute_inflight, _gantt_records  # noqa: E402

CAT = sys.argv[1] if len(sys.argv) > 1 else "手机膜"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/gantt_preview.html")
TODAY = datetime.date(2026, 10, 31)
LIMIT = int(sys.argv[3]) if len(sys.argv) > 3 else 3

DEV_STYLE = {
    "提前": ("#FFD966", "#7F4F00"),
    "常规": ("#D9E2F3", "#1F3864"),
    "未填": ("#F2F2F2", "#808080"),
    "其他": ("#FFF2CC", "#7F4F00"),
}
DEPT_COLOR = {"产品": "#4472C4", "运营": "#70AD47", "后勤": "#ED7D31",
              "运输": "#7030A0", "4国": "#BF8F00", "应发未发": "#C00000"}
DEPT_LABEL = {"产品": "产品部", "运营": "运营部", "后勤": "后勤部",
              "运输": "运输", "4国": "4国单独", "应发未发": "应发未发"}


def dev_key(v):
    s = str(v or "").strip()
    if not s or s in ("None", "nan"):
        return "未填"
    if "提前" in s:
        return "提前"
    if "常规" in s:
        return "常规"
    return "其他"


def dev_text(v):
    k = dev_key(v)
    if k == "未填":
        return "开发类型未填"
    s = str(v).strip()
    return f"⚡{s}" if k == "提前" else s


def launch_text(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s or s in ("None", "nan"):
        return "未填"
    return s[:10] if len(s) >= 10 else s


def over_text(item):
    j = str(item.get("judge") or "")
    if j.startswith("✓"):
        return "✓"
    if j[:1] in ("✗", "⚠", "⏳"):
        return j[1:] or "—"
    return "—"


def judge_color(j):
    s = str(j or "")
    if s.startswith("✗"):
        return "#C00000"
    if s.startswith("✓"):
        return "#1F7A1F"
    if s.startswith("⚠"):
        return "#B36A00"
    return "#808080"


def parse(t):
    if not t:
        return None
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(t[:len(f) + 2].strip(), f)
        except ValueError:
            continue
    return None


def main():
    data = compute_inflight(CAT)
    all_recs = _gantt_records(data, TODAY)
    recs = all_recs[:LIMIT]
    # 预览必须能看到「提前开发」的样式（橙黄），但按新→旧排序时它可能排在很后面
    # （手机膜第 7 个才是 G600）。所以额外补一个提前开发的块，否则用户看不出差异。
    if not any(dev_key(r.get("devType")) == "提前" for r in recs):
        extra = next((r for r in all_recs if dev_key(r.get("devType")) == "提前"), None)
        if extra is not None:
            recs = recs + [extra]
    # 没有「未填」样本时也补一个，让用户看到灰底长什么样
    if not any(dev_key(r.get("devType")) == "未填" for r in recs):
        extra = next((r for r in all_recs if dev_key(r.get("devType")) == "未填"), None)
        if extra is not None:
            recs = recs + [extra]
    if not recs:
        OUT.write_text("<p>没有在研 SKU</p>", encoding="utf-8")
        return

    starts, ends = [], []
    for r in recs:
        for x in r["rows"]:
            if x.get("t0"):
                starts.append(x["t0"])
            if x.get("t1"):
                ends.append(x["t1"])
    a0 = (min(starts) - datetime.timedelta(days=1)).date()
    a1 = max(max(ends).date(), TODAY) + datetime.timedelta(days=1)
    ndays = (a1 - a0).days + 1

    def pct(d):
        return (d - a0).days / ndays * 100

    blocks = []
    for rec in recs:
        k = dev_key(rec.get("devType"))
        bg, fg = DEV_STYLE[k]
        lt = launch_text(rec.get("launchDate"))
        meta = dev_text(rec.get("devType"))
        if lt is not None:
            meta += f" · 发布 {lt}"
        left = rec.get("stayDaysLeft")
        left_txt = ("—" if left is None
                    else (f"已超{-left:.0f}天" if left < 0
                          else ("今日到期" if left == 0 else f"剩{left:.0f}天")))
        rows_html = []
        for x in rec["rows"]:
            dept = x.get("dept") or ""
            color = DEPT_COLOR.get(dept, "#A6A6A6")
            t0, t1 = x.get("t0"), x.get("t1")
            bar = ""
            if t0 and t1:
                l = max(pct(t0.date()), 0)
                w = max(pct(t1.date()) - l, 0.4)
                cut = x.get("target")
                red = ""
                if cut:
                    import math
                    cutd = math.ceil(cut)
                    rl = pct((t0 + datetime.timedelta(days=cutd)).date())
                    rw = max(pct(t1.date()) - rl, 0)
                    if rw > 0:
                        red = (f'<div style="position:absolute;left:{rl:.3f}%;'
                               f'width:{rw:.3f}%;top:0;bottom:0;'
                               f'background:#FF3B30;border-radius:2px"></div>')
                bar = (f'<div style="position:relative;height:11px;'
                       f'background:#F2F2F2;border-radius:2px">'
                       f'<div style="position:absolute;left:{l:.3f}%;width:{w:.3f}%;'
                       f'top:0;bottom:0;background:{color};border-radius:2px"></div>'
                       f'{red}</div>')
            ot = over_text(x)
            oc = judge_color(x.get("judge"))
            bold = "600" if (ot == "✓" or ot.startswith("超")) else "400"
            rows_html.append(
                f'<tr>'
                f'<td class="dept">{html.escape(DEPT_LABEL.get(dept, dept or "—"))}</td>'
                f'<td class="seg">{html.escape(str(x.get("seg") or ""))}</td>'
                f'<td class="c">{t0.strftime("%m-%d") if t0 else "—"}</td>'
                f'<td class="c">{("进行中" if x.get("open") else (t1.strftime("%m-%d") if t1 else "—"))}</td>'
                f'<td class="c">{x.get("days") if x.get("days") is not None else "—"}</td>'
                f'<td class="c">{("≤%g" % x["target"]) if x.get("target") else "—"}</td>'
                f'<td class="c" style="color:{judge_color(x.get("judge"))};font-weight:600">'
                f'{html.escape(str(x.get("judge") or "—"))}</td>'
                f'<td class="c">{html.escape(str(x.get("owner") or "—"))}</td>'
                f'<td class="bar">{bar}</td>'
                f'<td class="over" style="color:{oc};font-weight:{bold}">{html.escape(ot)}</td>'
                f'</tr>')

        blocks.append(f'''
<div class="card">
  <div class="head" style="background:{bg};color:{fg}">
    <b>{html.escape(rec["sku"])}</b> · {html.escape(str(rec.get("model") or "—"))}
    <span class="meta">{html.escape(meta)}</span>
    <span class="sep">|</span> 状态: {html.escape(str(rec.get("status") or "—"))}
    <span class="sep">|</span> 当前停留: {html.escape(str(rec.get("staySeg") or "—"))}
    <span class="sep">|</span> 距超时: {left_txt}
  </div>
  <table>
    <thead><tr>
      <th style="width:62px">部门</th><th style="width:180px">环节</th>
      <th style="width:52px">起点</th><th style="width:56px">终点</th>
      <th style="width:48px">耗时</th><th style="width:48px">目标</th>
      <th style="width:130px">判定</th><th style="width:60px">执行人</th>
      <th>条形</th><th style="width:92px" class="over">超期</th>
    </tr></thead>
    <tbody>{"".join(rows_html)}</tbody>
  </table>
</div>''')

    axis = [f'<div class="axis">时间轴 {a0:%Y-%m-%d} → {a1:%Y-%m-%d}（{ndays} 天）'
            f' · 蓝/绿/橙=部门条，红=超出目标部分</div>']
    page = f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>在研 SKU 甘特图预览 · {CAT}</title>
<style>
 body{{font:13px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
      background:#fff;color:#222;margin:0;padding:18px 22px}}
 h1{{font-size:16px;margin:0 0 4px}}
 .sub{{color:#666;font-size:12px;margin-bottom:14px}}
 .card{{border:1px solid #E3E3E3;border-radius:6px;margin-bottom:16px;overflow:hidden}}
 .head{{padding:7px 10px;font-size:12.5px}}
 .head b{{font-size:13px}}
 .meta{{font-weight:600}}
 .sep{{color:currentColor;opacity:.35;margin:0 4px}}
 table{{border-collapse:collapse;width:100%}}
 th{{background:#FAFAFA;font-weight:500;font-size:11.5px;color:#555;
     border-bottom:1px solid #E3E3E3;padding:4px 6px;text-align:left}}
 th.over,td.over{{text-align:center;border-left:2px solid #E3E3E3;background:#FFFDF5}}
 td{{padding:3px 6px;font-size:12px;border-bottom:1px solid #F0F0F0}}
 td.c{{text-align:center}}
 td.dept{{font-weight:600;font-size:11.5px;color:#444;background:#FCFCFC}}
 td.seg{{font-size:12px}}
 td.bar{{padding:3px 8px;min-width:220px}}
 .axis{{font-size:11.5px;color:#666;margin:0 0 10px;padding:6px 9px;
        background:#FFF2CC;border-radius:4px}}
 .note{{font-size:12px;color:#555;background:#F7F9FC;border-left:3px solid #4472C4;
        padding:8px 11px;border-radius:0 4px 4px 0;margin-bottom:16px}}
 .note b{{color:#1F3864}}
</style></head><body>
<h1>在研 SKU 流程甘特图 · {CAT} · 预览</h1>
<div class="sub">仅展示前 {len(recs)} 个 SKU 块；完整内容见
 <code>data/{CAT}/{CAT}/reports/事中日报_20261031.xlsx</code> 的「在研SKU甘特图」</div>
<div class="note">
<b>本次新增（2026-11-01）</b>：① 标题行带「开发类型 · 官方发布时间」——
<b>橙黄=提前开发</b>、浅蓝=常规、灰=未填（不是漏画）；② 最右「超期」列每行给结果，
达标写 ✓、超期写超N天、未完成写进行中。
</div>
{"".join(axis)}{"".join(blocks)}
</body></html>'''
    OUT.write_text(page, encoding="utf-8")
    print(f"已生成 {OUT}  ({len(recs)} 个块, 轴 {ndays} 天)")


if __name__ == "__main__":
    main()
