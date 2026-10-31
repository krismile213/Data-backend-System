# -*- coding: utf-8 -*-
"""probe_frontend.py — 判断前端 dev server 是否**真的可用**

为什么不能只探 `/`：
    dev server 「半死」时（进程正在退出或重启）`/` 仍然返回 200，
    但模块路径会被 SPA fallback 成 index.html，浏览器报
    `Uncaught SyntaxError: Unexpected token '<'`，整页白屏。
    curl 只看得见状态码，会被这个状态骗过去 —— 用户就是这么白屏的。

因此这里的判据是「入口模块必须返回 JS」：
  GET /src/main.jsx  →  状态 200 且 Content-Type 是 javascript（或正文以 import/const 开头）

用法：
    python scripts/probe_frontend.py [base_url]
退出码：0 = 可用；1 = 不可用（未就绪 / 返回 HTML / 连不上）
"""
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:5173"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch(path, timeout=6):
    try:
        with OPENER.open(BASE + path, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read(400).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, "", ""
    except Exception as e:
        return -1, type(e).__name__, ""


def looks_like_js(content_type, body):
    if "javascript" in content_type.lower():
        return True
    return body.lstrip().startswith(("import", "const", "let", "var", "/*", "//"))


status, _, _ = fetch("/")
module_status, mct, mbody = fetch("/src/main.jsx")

if module_status == 200 and looks_like_js(mct, mbody):
    print(f"[ OK  ] frontend ready (index {status}, module {module_status} {mct})")
    sys.exit(0)

if status == 200 and module_status == 200:
    print(f"[ DEAD] frontend returns HTML for /src/main.jsx (Content-Type={mct or 'none'})")
    print(f"        head: {mbody[:100].strip()!r}")
    print("        the dev server is dying or restarting - pages will be blank")
else:
    print(f"[ DOWN] frontend not answering (index {status}, module {module_status})")
sys.exit(1)
