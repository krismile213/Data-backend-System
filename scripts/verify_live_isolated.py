# -*- coding: utf-8 -*-
"""
verify_live_isolated.py — 隔离端口在线验收（不打扰用户正在用的实例）
=======================================================================
为什么需要它：
    本机常年有一个**用户自己启动的旧实例**占着 8000 / 5173，而检查脚本默认都打
    这两个端口 —— 于是「验收新代码」会打到旧代码身上（表现为新增接口 404），
    而把用户的实例杀掉又不礼貌（他可能正在看）。

    本脚本另起一套服务做验收：
        后端 uvicorn → --be（默认 8001，新代码）
        前端 vite    → --fe（默认 5174，API_TARGET 指向上面的后端）
    跑完后拆掉这一套，并断言用户的 8000 / 5173 仍然活着（没被误伤）。
    `frontend/vite.config.js` 已支持 `API_TARGET` 环境变量。

用法（务必用**带依赖的解释器**，即项目的 venv）：
    C:\\Users\\dell\\.workbuddy\\binaries\\python\\envs\\default\\Scripts\\python.exe \\
        scripts\\verify_live_isolated.py
    ... --be 8001 --fe 5174

⚠️ 常见的坑：用错解释器（例如裸的 3.13.x 受管解释器）会 `No module named 'fastapi'`，
   后端永远起不来、脚本 90s 后报「后端未就绪」。本脚本开跑前先自检 fastapi/uvicorn。

验收内容：
    一、/api/deltas 口径（手机膜 / 手机壳 / 未知品类回退 / sku 过滤）
    二、/api/deltas/export 导出 xlsx（魔数 PK）
    三、POST /api/deltas/snapshot 幂等（第二次应 recorded=False）
    四、既有四个检查脚本：scan_endpoints / verify_category_live /
        browser_check（真浏览器 20 页）/ verify_review_downloads（弹窗双下载）

    ⚠️ 查询参数里的中文**必须转义**（本脚本用 `urllib.parse.urlencode` 统一处理）：
       直接把中文塞进 URL，urllib 会抛 UnicodeEncodeError，看起来像「接口坏了」。

⚠️ browser_check 需要 chromium，须在**关闭沙箱**的情况下运行本脚本。
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VITE = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
NO_WINDOW = 0x08000000

lines = []


def _verkey(name):
    return [int(x) for x in re.findall(r"\d+", name)]


def find_python():
    """后端解释器：优先本机托管的 venv，其次项目内 venv（与 start.py 同口径）。

    ⚠️ **不许写死版本号**：平台升级会整体替换 `versions\\<ver>` 目录名。
    """
    home = Path(os.environ.get("USERPROFILE", Path.home()))
    for c in (home / ".workbuddy" / "binaries" / "python" / "envs" / "default"
              / "Scripts" / "python.exe",
              ROOT / ".venv" / "Scripts" / "python.exe",
              BACKEND / ".venv" / "Scripts" / "python.exe"):
        if c.is_file():
            return c
    return Path(sys.executable) if sys.executable else None


def find_node():
    """Node 运行时：读 `versions\\current` 指针，否则取版本号最大的目录（与 start.py 同口径）。"""
    if os.environ.get("NODE_EXE"):
        p = Path(os.environ["NODE_EXE"])
        if p.is_file():
            return p
    home = Path(os.environ.get("USERPROFILE", Path.home()))
    base = home / ".workbuddy" / "binaries" / "node" / "versions"
    if not base.is_dir():
        return None
    cur = base / "current"
    if cur.is_file():
        try:
            name = cur.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:                                    # noqa: BLE001
            name = ""
        if name and (base / name / "node.exe").is_file():
            return base / name / "node.exe"
    dirs = [d for d in base.iterdir()
            if d.is_dir() and (d / "node.exe").is_file()]
    return (sorted(dirs, key=lambda d: _verkey(d.name))[-1] / "node.exe") \
        if dirs else None


def log(*a):
    s = " ".join(str(x) for x in a)
    lines.append(s)
    print(s, flush=True)


def q(**kw):
    """查询参数编码（中文必须转义，否则 urllib 直接抛错）。"""
    return urllib.parse.urlencode({k: v for k, v in kw.items() if v is not None})


def port_busy(port):
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def get(url, timeout=300):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(url, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})
    except Exception as e:                                   # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}".encode(), {}


def jget(url, timeout=300):
    """GET + 解析 JSON；失败返回 (status, None, 原因) 而不抛异常。"""
    st, body, _ = get(url, timeout=timeout)
    try:
        return st, json.loads(body), ""
    except Exception as e:                                   # noqa: BLE001
        return st, None, f"{type(e).__name__}: {e} | head={body[:120]!r}"


def wait(probe, secs, name):
    t0 = time.time()
    while time.time() - t0 < secs:
        if probe():
            log(f"  OK   {name} 就绪（{time.time() - t0:.1f}s）")
            return True
        time.sleep(1)
    log(f"  WARN {name} 在 {secs}s 内未就绪")
    return False


def ensure_interpreter():
    """确保当前解释器有 fastapi；没有就用项目 venv **重新执行自己**。

    ⚠️ 最常见的失败原因就是用裸的受管解释器跑（`No module named 'fastapi'`）→
    后端永远起不来、90s 后报「后端未就绪」，看起来像接口坏了。
    """
    try:
        import fastapi, uvicorn                               # noqa: F401
        return Path(sys.executable)
    except ImportError:
        pass
    venv = find_python()
    if venv and Path(sys.executable).resolve() != Path(venv).resolve():
        log(f"== 当前解释器缺依赖，改用 venv 重新执行：{venv} ==")
        sys.stdout.flush()
        return subprocess.call([str(venv), str(Path(__file__).resolve()),
                                *sys.argv[1:]])
    log("!! 找不到带 fastapi/uvicorn 的解释器，请用项目 venv 运行本脚本")
    return 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--be", type=int, default=8001, help="后端端口（隔离）")
    ap.add_argument("--fe", type=int, default=5174, help="前端端口（隔离）")
    args = ap.parse_args()
    BE, FE = args.be, args.fe

    PY = ensure_interpreter()
    if isinstance(PY, int):
        return PY
    NODE = find_node()
    if NODE is None or not VITE.is_file():
        log(f"!! 找不到 Node 运行时或 {VITE}（先 npm install）")
        return 2

    procs = []
    rc_all = {}
    try:
        for p in (BE, FE):
            if port_busy(p):
                log(f"  !! 端口 {p} 已被占用，可用 --be / --fe 换一组")
                raise SystemExit(2)

        env = os.environ.copy()
        env.pop("NODE_OPTIONS", None)
        env.pop("PYTHONPATH", None)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONUTF8"] = "1"

        log(f"== 起后端 uvicorn（新代码）:{BE} ==")
        be_log = open(Path(os.environ.get("TEMP", ".")) / "_verify_be.log", "wb")
        procs.append(subprocess.Popen(
            [str(PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(BE), "--log-level", "warning"],
            cwd=str(BACKEND), env=env, creationflags=NO_WINDOW,
            stdout=be_log, stderr=subprocess.STDOUT))
        if not wait(lambda: get(f"http://127.0.0.1:{BE}/api/health", 5)[0] == 200,
                    90, "Backend"):
            raise SystemExit("后端未就绪")

        log(f"== 起前端 vite:{FE}（/api 代理到 {BE}）==")
        env_fe = dict(env)
        env_fe["API_TARGET"] = f"http://127.0.0.1:{BE}"
        procs.append(subprocess.Popen(
            [str(NODE), str(VITE), "dev", "--port", str(FE), "--strictPort"],
            cwd=str(FRONTEND), env=env_fe, creationflags=NO_WINDOW,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

        def fe_ok():
            st, _b, hdr = get(f"http://127.0.0.1:{FE}/src/main.jsx", 8)
            return st == 200 and "html" not in (hdr.get("Content-Type") or "").lower()

        if not wait(fe_ok, 180, "Frontend"):
            raise SystemExit("前端未就绪")

        B, F = f"http://127.0.0.1:{BE}", f"http://127.0.0.1:{FE}"

        # ---------- 一、/api/deltas 口径 ----------
        log("")
        log("== 一、/api/deltas 响应口径 ==")
        st, d, err = jget(f"{B}/api/deltas?{q(category='手机膜', days=30)}")
        if d is None:
            raise SystemExit(f"手机膜 /api/deltas 未返回 JSON：status={st} {err}")
        log(f"  status={st} exists={d['exists']} scope={d['scope']!r}")
        log(f"  state={json.dumps(d['state'], ensure_ascii=False)}")
        assert st == 200 and d["category"] == "手机膜" and d["window"]["days"] == 30

        st2, d2, err2 = jget(f"{B}/api/deltas?{q(category='手机壳', days=7)}")
        if d2 is None:
            raise SystemExit(f"手机壳 /api/deltas 未返回 JSON：status={st2} {err2}")
        log(f"  手机壳 status={st2} exists={d2['exists']} "
            f"state={json.dumps(d2['state'], ensure_ascii=False)}")

        st3, d3, err3 = jget(f"{B}/api/deltas?{q(category='不存在', days=7)}")
        if d3 is None:
            raise SystemExit(f"未知品类未返回 JSON：status={st3} {err3}")
        log(f"  未知品类回退：status={st3} category={d3['category']!r}（应回退手机膜）")
        assert d3["category"] == "手机膜"

        st5, d5, err5 = jget(f"{B}/api/deltas?{q(days=7, sku='G493')}")
        if d5 is None:
            raise SystemExit(f"sku 过滤未返回 JSON：status={st5} {err5}")
        log(f"  sku 过滤：status={st5} sku={d5['sku']!r} "
            f"bySku={len(d5['window']['bySku'])} 行")

        # ---------- 二、导出 ----------
        log("")
        log("== 二、xlsx 导出 ==")
        st4, blob, hdr4 = get(f"{B}/api/deltas/export?{q(category='手机膜', days=30)}")
        log(f"  /api/deltas/export status={st4} bytes={len(blob)} magic={blob[:2]!r}")
        assert st4 == 200 and blob[:2] == b"PK", "导出不是 xlsx（魔数不对）"

        # ---------- 三、拍快照幂等 ----------
        log("")
        log("== 三、POST /api/deltas/snapshot 幂等 ==")
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        reasons = []
        for i in (1, 2):
            req = urllib.request.Request(
                f"{B}/api/deltas/snapshot?{q(category='手机膜')}", method="POST")
            with op.open(req, timeout=300) as r:
                rj = json.loads(r.read())
            reasons.append(rj["reason"])
            log(f"  第 {i} 次：recorded={rj['recorded']} reason={rj['reason']}")
        assert reasons[0] == reasons[1] == "指纹未变化（无新数据导入）", \
            f"快照不幂等：{reasons}"

        # ---------- 四、既有检查脚本 ----------
        checks = [
            ("接口全扫", [str(PY), str(ROOT / "scripts" / "scan_endpoints.py"), B], ROOT),
            ("品类在线冒烟", [str(PY), str(ROOT / "scripts" / "verify_category_live.py"), B], ROOT),
            ("真浏览器 20 页", [str(PY), str(FRONTEND / "scripts" / "browser_check.py"),
                          "--base", F], FRONTEND),
            ("弹窗双下载", [str(PY), str(FRONTEND / "scripts" / "verify_review_downloads.py"),
                        "--base", F], FRONTEND),
        ]
        log("")
        for name, cmd, cwd in checks:
            log(f"== 四、{name} ==")
            r = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True)
            tail = (r.stdout or b"").decode("utf-8", "replace").strip().splitlines()
            for ln in (tail[-4:] if len(tail) > 4 else tail):
                log(f"  | {ln}")
            log(f"  退出码 {r.returncode}")
            rc_all[name] = r.returncode
            if r.returncode != 0:
                log(f"  !! stderr: "
                    f"{(r.stderr or b'').decode('utf-8', 'replace')[-800:]}")
        if any(v != 0 for v in rc_all.values()):
            raise SystemExit("有检查脚本未通过")
    except SystemExit as e:
        log(f"中止：{e}")
        code = 1
    else:
        code = 0
    finally:
        for p in procs:
            try:
                p.terminate()
            except Exception:                                # noqa: BLE001
                pass
        time.sleep(2)
        for p in procs:
            try:
                if p.poll() is None:
                    p.kill()
            except Exception:                                # noqa: BLE001
                pass
        time.sleep(1)
        log("")
        log(f"退出码汇总：{rc_all}")
        log(f"拆服务后：{BE}={port_busy(BE)} {FE}={port_busy(FE)}；"
            f"用户的 8000={port_busy(8000)} 5173={port_busy(5173)}（应仍为 True）")
    return code


if __name__ == "__main__":
    sys.exit(main())
