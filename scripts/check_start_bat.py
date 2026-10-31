#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_start_bat.py — 启动入口静态检查（bat 壳 + start.py 真源）

2026-09-28 起 `start.bat` 已瘦身为「双击壳」，所有行为都搬进了 `start.py`。
本机（以及自动化环境）cmd.exe 被安全策略拦，bat 改完**没法真的跑一遍**，
而它仍是人进系统的第一个入口，写错就是「双击没反应」——所以这里静态查。

A. start.bat（壳）
  1. 纯 ASCII + 全 CRLF：非 936 代码页下中文会乱码，裸 LF 会让 cmd 错行解析
  2. 必须把参数原样转发（`%*`）给 start.py
  3. 不许再有 label / `for %%X` 白名单 —— 老 start.bat 的「能力漂移」与
     「双击没反应」全部来自这类构造，壳里一个都不该留
B. start.py（唯一真源）
  4. `KNOWN` 白名单里的每个子命令，都要在 start.py 自身的用法说明里写清
     （`help` 直接打印 `__doc__`，所以「文档即 help」；缺一条 = 用户查不到）
  5. 用法说明里提到的子命令，也必须在 `KNOWN` 里（否则敲了会掉进普通启动）
  6. `KNOWN` 里的每个子命令，`main()` 里都要有对应分支（白名单 ≠ 已实现）

用法：python scripts/check_start_bat.py [start.bat 路径] [start.py 路径]
      （第 2 个参数是给「检查这个检查」用的：喂一份故意改坏的副本，它必须报错）
退出码 0 = 通过，1 = 有问题，2 = 找不到文件
"""

import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BAT = ROOT / "start.bat"
START_PY = ROOT / "start.py"


def check_bat(path: Path, problems: list[str]) -> None:
    raw = path.read_bytes()

    # --- 换行 + ASCII ---
    lf = raw.count(b"\n")
    crlf = raw.count(b"\r\n")
    print(f"  行数 {lf}，其中 CRLF {crlf}")
    if lf != crlf:
        problems.append(f"start.bat 有 {lf - crlf} 行是裸 LF —— cmd 解析批处理要求 CRLF")

    try:
        src = raw.decode("ascii")
    except UnicodeDecodeError as e:
        src = raw.decode("utf-8", errors="replace")
        problems.append(f"start.bat 含非 ASCII 字节（要求纯 ASCII）: 偏移 {e.start}")

    for i, ln in enumerate(src.splitlines(), 1):
        if any(ord(c) > 127 for c in ln):
            problems.append(f"第 {i} 行含非 ASCII 字符: {ln.strip()[:60]}")

    # --- 必须转发参数、且指向 start.py ---
    if "%*" not in src:
        problems.append("start.bat 没转发参数（找不到 %*）—— 子命令会全部失效")
    if "start.py" not in src:
        problems.append("start.bat 没调用 start.py —— 逻辑本该全在 start.py")

    # --- 壳里不许再出现批处理逻辑 ---
    labels = re.findall(r"(?m)^:([A-Za-z_]\w*)", src)
    if labels:
        problems.append(f"start.bat 里出现 label {labels} —— 壳里不该有子程序")
    if re.search(r"for\s+.*?%%[A-Za-z]", src):
        problems.append("start.bat 里出现 for %%X 白名单 —— 参数白名单只该留在 start.py")


def check_start_py(path: Path, problems: list[str]) -> None:
    if not path.is_file():
        problems.append(f"找不到 {path}")
        return
    text = io.open(path, encoding="utf-8").read()

    m = re.search(r"(?m)^KNOWN\s*=\s*\{(.*?)\}", text, re.S)
    if not m:
        problems.append("start.py 里找不到 KNOWN 白名单")
        return
    known = set(re.findall(r'"([^"]+)"', m.group(1)))

    # 用法说明 = 文件头 docstring（help 打印的就是它）
    parts = text.split('"""')
    doc = parts[1] if len(parts) >= 2 else ""
    documented = set(re.findall(r"python start\.py ([a-z][a-z0-9-]*)", doc))

    # main() 里真正分派到的子命令
    handled = set(re.findall(r'opt\s*==\s*"([a-z0-9-]+)"', text))
    for tup in re.findall(r"opt\s+in\s+\(([^)]*)\)", text):
        handled |= set(re.findall(r'"([^"]+)"', tup))

    print(f"  KNOWN      : {sorted(known)}")
    print(f"  用法说明   : {sorted(documented)}")
    print(f"  main 分支  : {sorted(handled)}")

    for a in sorted(known - documented):
        problems.append(f"KNOWN 里有 {a}，但 start.py 用法说明没写（start.py help 查不到）")
    for a in sorted(documented - known):
        problems.append(f"用法说明写了 {a}，但 KNOWN 里没有（敲了会掉进普通启动）")
    for a in sorted(known - handled):
        problems.append(f"KNOWN 里有 {a}，但 main() 里没有对应分支（白名单 ≠ 已实现）")


def main() -> int:
    # 第 2 个参数是为了**自测这个检查本身**：拿一份故意改坏的 start.py 副本进来，
    # 它必须报错。一个从不失败的守卫等于没有守卫。
    bat = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BAT
    py = Path(sys.argv[2]) if len(sys.argv) > 2 else START_PY
    if not bat.exists():
        print(f"[ERROR] 找不到 {bat}")
        return 2

    problems: list[str] = []
    print(f"[1/2] start.bat 壳：{bat.name}")
    check_bat(bat, problems)
    print(f"[2/2] start.py 真源：{py.name}")
    check_start_py(py, problems)

    print()
    if problems:
        print(f"发现 {len(problems)} 个问题:")
        for p in problems:
            print("  x", p)
        return 1
    print("OK 静态检查通过：壳纯 ASCII + CRLF + 只转发参数；"
          "start.py 的白名单 / 用法说明 / main 分支三者一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
