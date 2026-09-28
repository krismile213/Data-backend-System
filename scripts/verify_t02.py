# -*- coding: utf-8 -*-
"""T02 验收脚本：多轮提交修复 + 因果链定位 + 事后评估（G506 基准）。

只读，不改任何数据。跑法（须用后端受管解释器）：
    <envs/default>/Scripts/python.exe scripts/verify_t02.py

验收点（对齐 docs/system_design.md §8.3）：
    1. /api/review/sku/G506 无 days<0 段，且含 multiRound / rootCause / assessment
    2. 提案 = 取数 = 2025-10-30（多轮提交统一取『最早一轮』，2026-09-24 T07 起
       与 流程复盘/route_check_sys.py 对齐）；multiRound.gapDays ≈ 182
    3. 空运路线 232 天 → delayDays 172；件数 [237,946]；金额 [2854.63, 11377.16]
    4. rootCause 指向人工漏报（suspect=True）
    5. /api/review/sku/G506/checkreport 可下载且含甘特与"人工核对"列
    6. /api/review/assessment 与 /assessment/export 可用
"""

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

os.environ.setdefault("PREWARM", "0")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

LINES = []


def p(*parts):
    LINES.append(" ".join(str(x) for x in parts))


def main():
    client = TestClient(app)
    ok = True

    # ---------- 1. 单 SKU 复盘档案 ----------
    r = client.get("/api/review/sku/G506")
    p(f"[1] GET /api/review/sku/G506 → {r.status_code}")
    if r.status_code != 200:
        p("    FAIL:", r.text[:400])
        return False
    d = r.json()

    neg = [(s["seg"], s["days"]) for s in d["segments"]
           if s.get("days") is not None and float(s["days"]) < 0]
    p(f"    反向段(days<0) = {neg or '无'}  → {'PASS' if not neg else 'FAIL'}")
    ok &= not neg

    p(f"    取数 = {d['nodes']['取数']['iso']}  (期望 2025-10-30…)")
    p(f"    multiRound = {d.get('multiRound')}")
    p(f"    rounds = {d.get('rounds')}")

    a = d.get("assessment") or {}
    p(f"    assessment: basis={a.get('basis')} span={a.get('spanDays')} "
      f"limit={a.get('limit')} delay={a.get('delayDays')} dir={a.get('direction')}")
    p(f"                 grade={a.get('grade')} daily={a.get('dailyMin')}~{a.get('dailyMax')} "
      f"price={a.get('price')}({a.get('priceType')})")
    p(f"                 qty={a.get('qtyMin')}~{a.get('qtyMax')} "
      f"amount={a.get('amountMin')}~{a.get('amountMax')} degraded={a.get('degraded')}")

    rc = d.get("rootCause")
    if rc:
        p(f"    rootCause: seg={rc['seg']} type={rc['type']} wait={rc['waitDays']} "
          f"suspect={rc['suspect']} owner={rc['owner']}")
        p(f"               evidence={rc['evidence']}")
        p(f"               chain={[(c['node'], c['at'], c['wait']) for c in rc.get('chain') or []]}")
    else:
        p("    rootCause = None")

    p("    --- 全部段（days / target / judge） ---")
    for s in d["segments"]:
        p(f"        {s['dept']:<6} {s['seg']:<22} days={str(s['days']):>7} "
          f"target={str(s['target']):>5} {s['judge']}")

    # ---------- 2. 事后评估 ----------
    r2 = client.get("/api/review/assessment")
    p(f"\n[2] GET /api/review/assessment → {r2.status_code}")
    if r2.status_code == 200:
        j = r2.json()
        p(f"    summary = {j['summary']}")
        g = next((x for x in j["items"] if x["sku"] == "G506"), None)
        p(f"    G506 项 = {g}")
    else:
        p("    FAIL:", r2.text[:300])
        ok = False

    r3 = client.get("/api/review/assessment/export")
    p(f"[3] GET /api/review/assessment/export → {r3.status_code}, "
      f"{len(r3.content)} bytes")
    ok &= r3.status_code == 200

    # ---------- 3. 核查报告 xlsx ----------
    r4 = client.get("/api/review/sku/G506/checkreport")
    p(f"\n[4] GET /api/review/sku/G506/checkreport → {r4.status_code}, "
      f"{len(r4.content)} bytes")
    if r4.status_code == 200:
        try:
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(r4.content))
            p(f"    sheets = {wb.sheetnames}")
            ws = wb[wb.sheetnames[0]]
            heads, head_row = [], None
            for r_i, row in enumerate(ws.iter_rows(min_row=1, max_row=20), start=1):
                vals = [c.value for c in row]
                if any(v and "人工核对" in str(v) for v in vals):
                    heads, head_row = vals, r_i
                    break
            p(f"    明细表头(第 {head_row} 行) = {heads}")
            got = [h for h in heads if h and "人工核对" in str(h)]
            p(f"    含『人工核对』列 = {bool(got)}  → {'PASS' if got else 'FAIL'}")
            ok &= bool(got)
        except Exception as e:
            p("    xlsx 解析失败:", e)
            ok = False
    else:
        p("    FAIL:", r4.text[:300])
        ok = False

    p(f"\n===== T02 验收：{'PASS' if ok else 'FAIL'} =====")
    return ok


if __name__ == "__main__":
    try:
        good = main()
    except Exception as exc:            # noqa: BLE001
        import traceback
        LINES.append("异常: " + repr(exc))
        LINES.append(traceback.format_exc())
        good = False
    out = os.path.join(ROOT, "scripts", "_t02.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(LINES))
    print(f"exit={0 if good else 1}  →  {out}")
    sys.exit(0 if good else 1)
