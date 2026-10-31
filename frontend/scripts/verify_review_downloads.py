# -*- coding: utf-8 -*-
"""verify_review_downloads.py — 真点击验证「复盘档案」弹窗里的下载按钮

为什么需要单独一个脚本
----------------------
`browser_check.py` 只能 `--dump-dom`（静态渲染一次就退出），而 MUI 的 `Dialog`
**关闭时不挂载**，所以弹窗里的按钮在 DOM dump 里根本不存在 —— 这块是现有验证链的盲区。
本脚本用 CDP（Chrome DevTools Protocol）直连一个真 Chromium：

  1. 打开复盘总览页，点第一行 → 等弹窗出现；
  2. 断言弹窗里有「导出核查报告」与「复盘报告 md」两个按钮，
     且 href 分别指向 `/api/review/sku/<code>/checkreport` 与 `/report`，带 `category=`；
  3. 真点这两个按钮，确认文件真的落盘（xlsx 认 zip 魔数 `PK`，md 认首字符 `#`）；
  4. 顺手读 `window.__uiErrs`（页面内 error / unhandledrejection 收集器）确认没报错。

只依赖 `websockets`（envs/default 已装）与 Playwright 下载的 Chromium，无需 playwright 包。

跑法（前后端都要在跑）：
    <envs/default>/Scripts/python.exe frontend/scripts/verify_review_downloads.py
    <envs/default>/Scripts/python.exe frontend/scripts/verify_review_downloads.py --base http://127.0.0.1:5173 --batch 手机膜9.7

退出码：0 = 全过，1 = 有断言失败，2 = 环境没就绪（前端/浏览器不可用）。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from browser_check import looks_like_js, probe, probe_module  # noqa: E402

BASE = "http://127.0.0.1:5173"
DEFAULT_BATCH = "手机膜9.7"

# 弹窗里两个按钮各自的断言目标：(按钮文本, href 必须包含的路径片段, 下载文件应满足的判据)
EXPECT_BUTTONS = [
    ("导出核查报告", "/checkreport", "xlsx"),
    ("复盘报告 md", "/report", "md"),
]

_INSTALL_ERR_COLLECTOR = """
window.__uiErrs = [];
window.addEventListener('error', function (e) {
  window.__uiErrs.push(String((e && e.message) || e));
});
window.addEventListener('unhandledrejection', function (e) {
  window.__uiErrs.push('unhandledrejection: ' + String((e && e.reason) || e));
});
"""

_READ_DIALOG = r"""
(function () {
  var dlg = document.querySelector('[role="dialog"]');
  if (!dlg) return null;
  // DialogTitle 的 textContent 就是弹窗的可访问名，形如「复盘档案 · G598 常规开发」。
  // ⚠️ 别在这里给标题自己加 id 再用 getElementById 取：MUI 的 DialogTitle 会把父级
  // Dialog 的 aria-labelledby 当成自己的 id，两边撞成同一个 id，取到的是外层 <h2>。
  var titleEl = dlg.querySelector('.MuiDialogTitle-root');
  var title = titleEl ? (titleEl.textContent || '').trim() : '';
  var links = [].slice.call(dlg.querySelectorAll('a, button')).map(function (b) {
    return {
      tag: b.tagName,
      text: (b.textContent || '').trim(),
      href: b.getAttribute('href'),
      disabled: !!(b.disabled || b.getAttribute('aria-disabled') === 'true'
                   || b.classList.contains('Mui-disabled'))
    };
  });
  return JSON.stringify({ title: title, links: links });
})()
"""

# 详情是异步拉的：弹窗刚挂载的那一瞬间按钮处于禁用态（detail 还没回来）。
_WAIT_ENABLED = r"""
(function () {
  var d = document.querySelector('[role="dialog"]');
  if (!d) return false;
  var as = [].slice.call(d.querySelectorAll('a'));
  if (!as.length) return false;
  return as.every(function (x) { return !x.classList.contains('Mui-disabled'); });
})()
"""


# ---------------------------------------------------------------------------
# 浏览器定位
# ---------------------------------------------------------------------------
def find_chrome():
    """优先完整版 chrome.exe（CDP + 下载行为最稳），退回 headless shell。

    与 browser_check.find_browser 一样**不写死版本号** —— 平台换目录不留旧版。
    """
    explicit = os.environ.get("CHROME_BIN")
    if explicit and Path(explicit).exists():
        return explicit

    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright",
        Path.home() / "AppData" / "Local" / "ms-playwright",
        Path.home() / ".cache" / "ms-playwright",
    ]
    cands = []
    for root in roots:
        if not root.is_dir():
            continue
        cands += list(root.glob("chromium-*/chrome-win64/chrome.exe"))
        cands += list(root.glob("chromium-*/chrome-linux/chrome"))
        cands += list(root.glob("chromium_headless_shell-*/chrome-headless-shell-win64/"
                                "chrome-headless-shell.exe"))
    if not cands:
        return None

    def rank(p):
        # 完整版 chrome 排前面；同类型取版本号大的
        import re
        ver = re.search(r"-(\d+)", p.parent.parent.name)
        return (1 if "chrome-headless-shell" in p.name else 0,
                -int(ver.group(1)) if ver else 0)

    return str(sorted(cands, key=rank)[0])


# ---------------------------------------------------------------------------
# 极简 CDP 客户端（同步）
# ---------------------------------------------------------------------------
class CDP:
    def __init__(self, ws_url, open_timeout=20):
        from websockets.sync.client import connect
        self.ws = connect(ws_url, max_size=None, open_timeout=open_timeout)
        self._id = 0

    def send(self, method, params=None, timeout=30):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError(f"CDP {method} 超时")
            msg = json.loads(self.ws.recv(timeout=left))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} 失败: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr, timeout=30):
        r = self.send("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True,
        }, timeout=timeout)
        if r.get("exceptionDetails"):
            raise RuntimeError("页面 JS 抛错: "
                               + json.dumps(r["exceptionDetails"])[:300])
        return r.get("result", {}).get("value")

    def wait_js(self, expr, timeout, poll=0.35):
        t0 = time.time()
        last = None
        while time.time() - t0 < timeout:
            last = self.js(expr)
            if last:
                return last
            time.sleep(poll)
        raise TimeoutError(f"等待超时（{timeout}s）: {expr[:90]}（最后取值 {last!r}）")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


class Browser:
    """起一个带远程调试端口的 Chromium，并连上它的第一个 page target。"""

    def __init__(self, exe):
        self.exe = exe
        self.profile = Path(tempfile.mkdtemp(prefix="cdp-profile-"))
        self.downloads = self.profile / "downloads"
        self.downloads.mkdir(parents=True, exist_ok=True)
        self.proc = None
        self.cdp = None

    def __enter__(self):
        args = [
            self.exe, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--disable-dev-shm-usage", "--no-first-run",
            "--no-default-browser-check", "--hide-scrollbars",
            "--window-size=1600,1000",
            "--remote-debugging-port=0",
            f"--user-data-dir={self.profile}",
            "about:blank",
        ]
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        port_file = self.profile / "DevToolsActivePort"
        t0 = time.time()
        while time.time() - t0 < 30:
            if port_file.exists() and port_file.stat().st_size > 0:
                break
            time.sleep(0.15)
        else:
            raise RuntimeError("Chromium 没有写出 DevToolsActivePort（启动失败）")
        port = port_file.read_text().splitlines()[0].strip()

        targets = _http_json(f"http://127.0.0.1:{port}/json")
        page = next((t for t in targets if t.get("type") == "page"), None)
        if not page:
            raise RuntimeError("没有可用的 page target")
        self.cdp = CDP(page["webSocketDebuggerUrl"])
        self.cdp.send("Page.enable")
        self.cdp.send("Runtime.enable")
        try:
            self.cdp.send("Browser.setDownloadBehavior", {
                "behavior": "allow", "downloadPath": str(self.downloads),
                "eventsEnabled": True,
            })
        except Exception:
            # 老版本只在 page 域上认这个命令
            self.cdp.send("Page.setDownloadBehavior", {
                "behavior": "allow", "downloadPath": str(self.downloads),
            })
        self.cdp.send("Page.addScriptToEvaluateOnNewDocument",
                      {"source": _INSTALL_ERR_COLLECTOR})
        return self

    def __exit__(self, *exc):
        if self.cdp:
            self.cdp.close()
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
        # safe-delete shim 可能拒删；清理失败不影响结论
        shutil.rmtree(self.profile, ignore_errors=True)

    def navigate(self, url, timeout=40):
        self.cdp.send("Page.navigate", {"url": url})
        self.cdp.wait_js("document.readyState === 'complete'", timeout)
        return self.cdp.wait_js(
            "document.querySelectorAll('#root *').length > 0", timeout)

    def wait_download(self, timeout=40):
        """等一个真正落盘的文件（忽略 .crdownload 之类的半成品）。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            done = [p for p in self.downloads.iterdir()
                    if p.is_file() and not p.name.endswith(".crdownload")]
            if done:
                time.sleep(0.4)          # 等写盘收尾
                f = max(done, key=lambda p: p.stat().st_mtime)
                return f
            time.sleep(0.35)
        return None


def _http_json(url, timeout=10):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _looks_like_xlsx(path):
    with open(path, "rb") as fh:
        return fh.read(2) == b"PK"


def _looks_like_md(path):
    with open(path, "rb") as fh:
        return fh.read(1) in (b"#", b"\xef")   # `#` 或 BOM 开头


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="真点击验证复盘档案弹窗的下载按钮（CDP）")
    ap.add_argument("--base", default=BASE, help="前端地址")
    ap.add_argument("--batch", default=DEFAULT_BATCH, help="数据批次")
    ap.add_argument("--page", default="review", help="页面 key")
    ap.add_argument("--timeout", type=int, default=45, help="单步等待上限（秒）")
    args = ap.parse_args()

    chrome = find_chrome()
    if not chrome:
        print("[ERROR] 找不到 Chromium。设置 CHROME_BIN，或先 npx playwright install chromium")
        return 2
    print(f"[INFO] 浏览器: {chrome}")

    status = probe(f"{args.base}/")
    if status != 200:
        print(f"[ERROR] 前端未就绪: {args.base}/ 返回 {status} —— 先 start.bat")
        return 2
    mstatus, mct, mbody = probe_module(args.base)
    if mstatus != 200 or not looks_like_js(mct, mbody):
        print(f"[ERROR] 前端在返回 HTML 而不是 JS（/src/main.jsx -> {mstatus}）。"
              f"dev server 半死，先 start.bat restart")
        return 2
    print(f"[INFO] 前端就绪: {args.base} （/src/main.jsx -> {mstatus} {mct}）")

    import urllib.parse
    url = (f"{args.base}/?page={args.page}"
           f"&batch={urllib.parse.quote(args.batch)}")
    print(f"[INFO] 页面: {url}")
    print()

    fails = []
    with Browser(chrome) as br:
        br.navigate(url, args.timeout)

        # 1) 打开第一行的「复盘档案」
        br.cdp.wait_js("document.querySelectorAll('tbody tr').length > 0",
                       args.timeout)
        # 第一格才是 SKU（原先取整行 textContent，会把型号/状态/多轮标记全带进来）
        row_sku = br.cdp.js("document.querySelector('tbody tr td').textContent.trim()")
        print(f"[STEP] 点第一行 SKU = {row_sku}")
        br.cdp.js("document.querySelector('tbody tr').click()")
        first = br.cdp.wait_js(_READ_DIALOG, args.timeout)
        print(f"[STEP] 弹窗打开: {json.loads(first)['title']}")
        # 详情是异步拉的，弹窗刚挂载时按钮是禁用态；等变成可点再断言
        br.cdp.wait_js(_WAIT_ENABLED, args.timeout)
        info = json.loads(br.cdp.js(_READ_DIALOG))

        # 标题形如「复盘档案 · G598常规开发」（Chip 紧跟 SKU、中间没有空格），
        # 所以只吃「字母开头 + 字母数字连字符」这段，别用 \S+ 把 Chip 文字吞进来。
        m = re.search(r"复盘档案\s*·\s*([A-Za-z][A-Za-z0-9\-]*)", info["title"])
        title_code = m.group(1) if m else ""
        if not title_code:
            fails.append(f"弹窗标题里读不出 SKU: {info['title']!r}")
        elif row_sku and title_code != row_sku:
            fails.append(f"弹窗标题里的 SKU({title_code}) 与点击行({row_sku}) 不一致")

        # 2) 断言两个按钮存在、href 正确、未禁用
        by_label = {}
        for link in info["links"]:
            for label, _, _ in EXPECT_BUTTONS:
                if label in link["text"]:
                    by_label[label] = link
        for label, frag, _ in EXPECT_BUTTONS:
            got = by_label.get(label)
            if not got:
                fails.append(f"弹窗里找不到按钮「{label}」")
                continue
            href = got.get("href") or ""
            if frag not in href or "/api/review/sku/" not in href:
                fails.append(f"「{label}」href 不对: {href!r}（应含 {frag}）")
            if "category=" not in href:
                fails.append(f"「{label}」href 丢了 category: {href!r}")
            if got.get("disabled"):
                fails.append(f"「{label}」是禁用状态（详情没加载出来？）")

        # 3) 真点，看文件到底有没有下来
        for label, _, kind in EXPECT_BUTTONS:
            if label not in by_label:
                continue
            before = {p.name for p in br.downloads.iterdir()}
            clicked = br.cdp.js(
                "(function(){var a=[].slice.call("
                "document.querySelectorAll('[role=\"dialog\"] a')).find("
                "function(x){return (x.textContent||'').indexOf(%s)>=0;});"
                "if(!a) return false; a.click(); return true;})()"
                % json.dumps(label))
            if not clicked:
                fails.append(f"「{label}」点不动（不是 <a> 或已被禁用）")
                continue
            f = br.wait_download(args.timeout)
            new = [p for p in br.downloads.iterdir()
                   if p.is_file() and p.name not in before]
            if not f or not new:
                fails.append(f"点了「{label}」但没有文件落盘")
                continue
            f = max(new, key=lambda p: p.stat().st_mtime)
            good = _looks_like_xlsx(f) if kind == "xlsx" else _looks_like_md(f)
            if not good:
                fails.append(f"「{label}」下到的文件不是合法 {kind}: {f.name}")
            print(f"  OK   {label:<12} -> {f.name}  {f.stat().st_size}B  ({kind} 魔数 OK)"
                  if good else f"  FAIL {label} -> {f.name}")

        # 4) 页面有没有 JS 报错
        errs = br.cdp.js("JSON.stringify(window.__uiErrs || [])")
        errs = json.loads(errs or "[]")
        if errs:
            fails.append("页面有 JS 报错: " + " | ".join(str(e)[:120] for e in errs[:3]))

    print()
    if fails:
        print(f"=== {len(fails)} 项失败 ===")
        for f in fails:
            print(f"  x {f}")
        return 1
    print("=== PASS：弹窗两个下载按钮都在，href 正确，真点击后文件正常落盘，页面无 JS 报错 ===")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:      # noqa: BLE001 — 顶层兜底，把原因打清楚
        print(f"[ERROR] {type(e).__name__}: {e}")
        sys.exit(2)
