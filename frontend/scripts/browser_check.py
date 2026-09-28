#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""browser_check.py — 真实浏览器渲染自检：17 个页面到底能不能进得去

为什么需要这个脚本（以及它和另外两个检查的分工）：

    curl / 模块爬虫   只能证明 HTML 和每个 JS 模块能下载，**证明不了页面能渲染**。
                      出现过「所有模块都 200，浏览器里却整页白屏」的情况。
    ssr_smoke.mjs     用 renderToString 渲染，**不执行 useEffect**，
                      所以「组件挂载之后才炸」的白屏它抓不到。
    本脚本            真的启动一个 Chromium（Playwright 自带的 headless shell），
                      执行完整 JS（含 useEffect / 事件 / 图表），再检查 #root
                      里到底有没有东西。这是唯一能回答「能不能进后台」的检查。

用法：

    python scripts/browser_check.py                     # 检查全部 17 个页面
    python scripts/browser_check.py --batch 手机膜9.7    # 指定批次
    python scripts/browser_check.py --page dashboard    # 只检查一个页面

前置：前端 dev server（或 preview）已在运行。

关于浏览器路径：Playwright 的目录带版本号（chromium_headless_shell-1243），
升级后会变，所以这里**动态查找**而不是写死 —— 写死过一次 node 版本号，
平台一升级启动脚本就废了，同样的坑不踩第二次。
"""

import argparse
import glob
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:5173"
DEFAULT_BATCH = "手机膜9.7"

# 与 App.jsx 的 NAV_ITEMS 保持一致
PAGES = [
    ("dashboard", "时效看板"), ("inflight", "事中日报"),
    ("review", "复盘总览"), ("assessment", "事后评估"),
    ("gantt", "流程甘特"),
    ("scenarios", "场景诊断"), ("routes", "路线达标"), ("transport", "货件运输"),
    ("alerts", "加急预警"), ("watch", "联动提醒"), ("fillins", "手工填报"),
    ("history", "趋势周报"), ("compare", "批次对比"), ("skus", "SKU 档案"),
    ("records", "明细查询"), ("anomalies", "异常排查"), ("rules", "规则配置"),
    ("manual", "操作手册"), ("dataadmin", "数据管理"),
]

_STYLE_RE = re.compile(r"<style\b.*?</style>", re.S | re.I)
_ROOT_RE = re.compile(r'id="root"[^>]*>(.*)', re.S)


def find_browser():
    """动态定位 Playwright 的 chrome-headless-shell。"""
    explicit = os.environ.get("HEADLESS_SHELL")
    if explicit and Path(explicit).exists():
        return explicit

    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright",
        Path.home() / "AppData" / "Local" / "ms-playwright",
        Path.home() / ".cache" / "ms-playwright",
    ]
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        for pat in ("chromium_headless_shell-*", "chromium-*"):
            for d in root.glob(pat):
                for exe in ("chrome-headless-shell.exe", "chrome-headless-shell",
                            "headless_shell.exe", "chrome.exe"):
                    p = d / "chrome-headless-shell-win64" / exe
                    if p.exists():
                        found.append(p)
                    p2 = d / "chrome-win64" / exe
                    if p2.exists() and exe == "chrome.exe":
                        found.append(p2)

    def ver(p):
        m = re.search(r"-(\d+)", p.parent.parent.name)
        return int(m.group(1)) if m else 0

    found = [p for p in found if "headless" in p.name]
    if not found:
        found = sorted(glob.glob(str(roots[0] / "chromium-*" / "chrome-win64" / "chrome.exe")))
        found = [Path(p) for p in found]
    if not found:
        return None
    return str(sorted(found, key=ver)[-1])


def probe(url, timeout=8):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return -1


def probe_module(base, timeout=8):
    """取入口模块，用来判断前端到底是在提供 JS，还是在返回 HTML。

    为什么必须有这一步：dev server 「半死」时（进程正在退出或重启）
    `/` 仍然返回 200，但模块路径会被 SPA fallback 成 index.html，
    浏览器报 `Uncaught SyntaxError: Unexpected token '<'`，17 个页面全白。
    只探 `/` 的检查（包括 start.bat 的 curl 探针）会被这个状态骗过去。
    返回 (status, content_type, body_head)。
    """
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{base}/src/main.jsx", timeout=timeout) as r:
            ct = r.headers.get("Content-Type", "")
            return r.status, ct, r.read(400).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, "", ""
    except Exception:
        return -1, "", ""


def looks_like_js(content_type, body):
    if "javascript" in content_type.lower():
        return True
    return body.lstrip().startswith(("import", "const", "let", "var", "/*", "//"))


def render(browser, url, budget_ms, timeout_s):
    """用 headless shell 渲染页面，返回 (dom, stderr)。"""
    cmd = [
        browser,
        "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
        "--disable-dev-shm-usage",
        f"--virtual-time-budget={budget_ms}",
        "--dump-dom", url,
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout_s)
        return p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return "", "TIMEOUT"


def judge(dom):
    """从渲染出来的 DOM 判断这一页是否真的可用。

    注意：不能用 MuiCircularProgress 的出现来判定「卡在加载中」——
    MUI 的 emotion 会把组件样式注入到 <style> 里，正常渲染的页面同样含这个词。
    先把 <style> 全部剥掉再判断。
    """
    if not dom:
        return False, "页面没有任何输出"
    if "vite-error-overlay" in dom or "Element type is invalid" in dom:
        return False, "存在编译/渲染错误覆盖层"

    m = _ROOT_RE.search(dom)
    if not m:
        return False, "找不到 #root"
    if '<div id="root"></div>' in dom:
        return False, "#root 为空 —— 整页白屏"

    body = m.group(1)
    stripped = _STYLE_RE.sub("", body)

    if 'class="MuiAppBar' not in stripped and "MuiAppBar" not in stripped:
        return False, "应用外壳（AppBar）未渲染"
    if "MuiCircularProgress-root" in stripped:
        return False, "仍停在加载动画上（数据没回来）"
    if len(stripped) < 500:
        return False, f"渲染内容过少（{len(stripped)} 字节）"
    if "ERR_" in stripped or "Failed to fetch" in stripped:
        return False, "页面显示了网络错误"
    return True, f"渲染成功（{len(stripped)} 字节）"


def latest_batch(base, timeout=8):
    """动态取最新批次名（新模式：钉钉每天导出全量，批次名会变）。失败回退默认。"""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{base}/api/batches", timeout=timeout) as r:
            import json as _json
            data = _json.loads(r.read().decode("utf-8"))
        batches = data.get("batches") or []
        if batches:
            return batches[0]["name"]
    except Exception:
        pass
    return DEFAULT_BATCH


def main():
    ap = argparse.ArgumentParser(description="真实浏览器渲染自检")
    ap.add_argument("--batch", default=None,
                    help="数据批次（缺省 = 动态取批次列表第一个）")
    ap.add_argument("--base", default=BASE, help="前端地址")
    ap.add_argument("--page", help="只检查指定页面 key")
    ap.add_argument("--budget", type=int, default=18000, help="虚拟时间预算 ms")
    args = ap.parse_args()

    if not args.batch:
        args.batch = latest_batch(args.base)

    browser = find_browser()
    if not browser:
        print("[ERROR] 找不到可用的 headless 浏览器。")
        print("        设置环境变量 HEADLESS_SHELL 指向 chrome-headless-shell.exe，")
        print("        或先执行：npx playwright install chromium")
        return 2
    print(f"[INFO] 浏览器: {browser}")

    status = probe(f"{args.base}/")
    if status != 200:
        print(f"[ERROR] 前端未就绪: {args.base}/ 返回 {status}")
        print("        先执行 start.bat 启动服务。")
        return 2
    print(f"[INFO] 前端就绪: {args.base}/ -> {status}")

    # 关键前置：入口模块必须真的是 JS。半死的 dev server 能骗过 `/` 探针。
    mstatus, mct, mbody = probe_module(args.base)
    if mstatus != 200 or not looks_like_js(mct, mbody):
        print()
        print("[ERROR] 前端在返回 HTML 而不是 JS 模块 —— 这样每个页面都会白屏。")
        print(f"        GET {args.base}/src/main.jsx -> {mstatus}  Content-Type={mct or '(none)'}")
        if mbody.strip():
            print(f"        响应开头: {mbody[:120].strip()!r}")
        print("        ⇒ dev server 已死或正在重启，不是页面代码的问题。")
        print("        请执行:  start.bat restart")
        return 2

    print(f"[INFO] 入口模块: /src/main.jsx -> {mstatus} ({mct})")
    print(f"[INFO] 批次: {args.batch}")
    print()

    todo = [(k, n) for k, n in PAGES if not args.page or k == args.page]
    if not todo:
        print(f"[ERROR] 未知页面: {args.page}")
        return 2

    import urllib.parse
    batch_q = urllib.parse.quote(args.batch)
    failed = []
    for i, (key, label) in enumerate(todo, 1):
        url = f"{args.base}/?page={key}&batch={batch_q}"
        dom, err = render(browser, url, args.budget, max(60, args.budget // 1000 + 40))
        ok, note = judge(dom)
        flag = "OK  " if ok else "FAIL"
        print(f"  [{i:>2}/{len(todo)}] {flag} {label:<10} {key:<11} {note}", flush=True)
        if not ok:
            failed.append((key, label, note, err[:300]))
            # 服务中途死掉时，剩下的页面会集体以同一种方式失败。
            # 与其报「N 个页面有 bug」，不如先弄清是不是服务先没了。
            if len(failed) == 3:
                s2, ct2, b2 = probe_module(args.base)
                if s2 != 200 or not looks_like_js(ct2, b2):
                    print()
                    print("[ERROR] 中途发现前端 dev server 已不再提供 JS（返回 HTML 或不可达）——")
                    print("        后面这些页面的失败原因不是页面代码，而是服务先挂了。")
                    print("        请执行:  start.bat restart")
                    return 2

    print()
    if failed:
        print(f"=== {len(failed)} / {len(todo)} 个页面打不开 ===")
        for key, label, note, err in failed:
            print(f"  ✗ {label} ({key}): {note}")
            if err.strip():
                print(f"      {err.strip()[:200]}")
        return 1
    print(f"=== 全部 {len(todo)} 个页面都能正常打开 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
