# -*- coding: utf-8 -*-
"""scan_endpoints.py — 后端接口健康扫描

把所有 GET 接口真正调一遍，找出非 200 的。

为什么需要它：单看代码很难发现「NaN 不是合法 JSON」这类只在实际响应时才炸的问题
（/api/anomalies 与 /api/timeliness 在 手机膜 批次上各踩过一次 500）。
一次性把所有接口打一遍，比逐个排查可靠，是「改完后端还能不能进系统」的常规验证手段。

与 frontend/scripts/browser_check.py 的关系：
  browser_check.py  看**前端**能不能渲染出页面（真实 Chromium）
  scan_endpoints.py 看**后端**接口有没有 500（全部 GET 接口，含带 {code} 的）
两者合起来才是完整的「能不能进后台」。

用法：python scripts/scan_endpoints.py [base_url]
退出码 0 = 全部 200，1 = 有接口异常
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕开沙箱代理
BATCH = "手机膜9.7"           # 兜底值；下面启动探针会动态覆盖（批次名每天变）
ALT_BATCH = None
SKU = None


# 动态发现批次：新模式「钉钉每天导出最新全量」后批次名会变、旧批次目录会被
# 移走 —— 硬编码批次名会让下面带 {batch} 的接口全 404。取目录里前两个批次。
try:
    with OPENER.open(BASE + "/api/batches", timeout=15) as _r:
        _names = [b["name"] for b in json.loads(_r.read()).get("batches") or []]
    if _names:
        BATCH = _names[0]
        ALT_BATCH = _names[1] if len(_names) > 1 else None
except Exception:
    pass


def get(path, timeout=180):
    t0 = time.perf_counter()
    try:
        with OPENER.open(urllib.request.Request(BASE + path), timeout=timeout) as r:
            body = r.read()
            return r.status, len(body), time.perf_counter() - t0, body
    except urllib.error.HTTPError as e:
        return e.code, 0, time.perf_counter() - t0, e.read()
    except Exception as e:
        return -1, 0, time.perf_counter() - t0, str(e).encode()


# 取一份真实 SKU，供带 {code} 的接口使用
status, _, _, body = get("/api/review/overview")
try:
    # ⚠️ 接口返回的键是 "rows"（不是 "skus"）。曾经写错成 "skus" 导致 SKU 恒为 None，
    # 于是下面 3 个带 {code} 的接口**从未被真正探测过**，却仍打印「全部 N 个接口均返回 200」。
    rows = json.loads(body).get("rows") or []
    if rows:
        SKU = rows[0].get("sku") or rows[0].get("SKU")
except Exception:
    pass
print(f"（探针 SKU = {SKU}）\n", flush=True)

# 显式列出要测的接口：openapi 里带路径参数的接口没法自动填，人工给两个批次
PATHS = [
    "/api/health",
    "/api/batches",
    "/api/etl/scan",
    "/api/etl/backup",
    "/api/etl/products",
    "/api/manual",
    "/api/manual/sop",
    # T08：新增的手册第 3 篇（流程事中/事后评估）—— 白名单登记了就要真的探一次
    "/api/manual/inflight",
    "/api/skus",
    "/api/review/overview",
    "/api/scenarios",
    "/api/routes",
    "/api/transport",
    "/api/alerts",
    "/api/watch",
    "/api/fillins",
    "/api/history",
    "/api/rules",
    # T02：事后评估三指标（时效/销量/金额）与 xlsx 导出（P1-4）
    "/api/review/assessment",
    "/api/review/assessment/export",
    # T03：事中引擎（在研总览 / 风险预警 / 进度变化 / 日报列表）
    "/api/inflight",
    "/api/inflight/alerts",
    "/api/inflight/progress",
    "/api/inflight/daily-report",
]
for b in [x for x in (BATCH, ALT_BATCH) if x]:
    PATHS += [
        f"/api/overview/{urllib.parse.quote(b)}",
        f"/api/timeliness/{urllib.parse.quote(b)}",
        f"/api/anomalies/{urllib.parse.quote(b)}",
        f"/api/records/{urllib.parse.quote(b)}/Part1",
    ]
if SKU:
    PATHS += [
        f"/api/review/sku/{urllib.parse.quote(SKU)}",
        f"/api/review/sku/{urllib.parse.quote(SKU)}/checkreport",   # P0-6 核查报告
        f"/api/review/sku/{urllib.parse.quote(SKU)}/report",        # 复盘报告 md
        f"/api/skus/{urllib.parse.quote(SKU)}",
        f"/api/fillins/sku/{urllib.parse.quote(SKU)}",
        f"/api/inflight/sku/{urllib.parse.quote(SKU)}",             # T03 事中档案
    ]

bad = []
for p in PATHS:
    st, size, dt, body = get(p)
    flag = "OK  " if st == 200 else "FAIL"
    extra = ""
    if st != 200:
        snippet = body.decode("utf-8", "replace")[:160].replace("\n", " ")
        extra = f"  <- {snippet}"
        bad.append((p, st, snippet))
    print(f"  {flag} {st:>4}  {dt:6.2f}s  {size:>9}B  {p}{extra}", flush=True)

print()
if bad:
    print(f"=== {len(bad)} 个接口未返回 200 ===")
    for p, st, snip in bad:
        print(f"  [{st}] {p}")
        print(f"        {snip}")
    sys.exit(1)
print(f"=== 全部 {len(PATHS)} 个接口均返回 200 ===")
