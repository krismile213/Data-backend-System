#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_start_bat.py — start.bat 静态检查

为什么需要它：本机（以及自动化环境）里 cmd.exe 被安全策略禁止调用，
`start.bat` 改完**没法真的跑一遍验证**。而它是所有人进系统的入口，
一旦写错（引号、括号、标签名、变量名），后果就是「双击了没反应」。
这个脚本把批处理里最容易犯的结构错误一次性查出来。

检查项：
  1. call :label / goto label 是否都有对应的 :label 定义
  2. %VAR% / !VAR! 引用是否都赋过值（排除内置变量与 %1..%9）
  3. 括号是否配对
  4. 每个子程序段是否会正常 return（含 :xxx_loop 这类续接标签）
  5. help 文本列出的可选参数与 OPT_KNOWN 白名单是否一致

用法：python scripts/check_start_bat.py [start.bat 路径]
退出码 0 = 通过，1 = 有问题
"""

import io
import re
import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[1] / "start.bat"

BUILTIN = {
    "errorlevel", "cd", "date", "time", "random", "path", "temp", "tmp",
    "username", "userprofile", "computername", "os", "programfiles",
    "appdata", "localappdata", "systemroot", "windir", "pathext",
    "processor_architecture", "userdomain", "logonserver", "sessionname",
}


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not path.exists():
        print(f"[ERROR] 找不到 {path}")
        return 2
    src = io.open(path, encoding="utf-8").read()
    lines = src.splitlines()
    problems = []

    # 1) 非 ASCII 检查：脚本头部明确要求纯 ASCII，否则非 936 代码页下会乱码
    for i, ln in enumerate(lines, 1):
        if any(ord(c) > 127 for c in ln):
            problems.append(f"第 {i} 行含非 ASCII 字符（该文件要求纯 ASCII）: {ln.strip()[:60]}")

    # 2) label
    defs = set(re.findall(r"(?m)^:([A-Za-z_]\w*)", src))
    refs = set(re.findall(r"call\s+:([A-Za-z_]\w*)", src))
    refs |= set(re.findall(r"goto\s+:?([A-Za-z_]\w*)", src))
    refs.discard("eof")
    for m in sorted(refs - defs):
        problems.append(f"引用了不存在的 label: :{m}")
    print(f"  label: 定义 {len(defs)} 个，引用 {len(refs)} 个")

    for label in sorted(defs):
        idx = next((i for i, ln in enumerate(lines) if ln.strip() == f":{label}"), None)
        if idx is None:
            continue
        body = []
        for ln in lines[idx + 1:]:
            m2 = re.match(r"^:([A-Za-z_]\w*)", ln)
            if m2:
                # :wait_url 故意掉进 :wait_url_loop —— 名字带同一前缀的续接标签
                # 属于同一段，继续往下扫，否则会误报「没有 exit /b」
                if not m2.group(1).startswith(label):
                    break
            body.append(ln)
        if not any(l.strip().startswith("exit /b") for l in body):
            problems.append(f":{label} 段内没有 exit /b（执行到底会掉进下一个标签）")

    # 3) 变量
    assigned = set()
    for ln in lines:
        if ln.strip().lower().startswith("rem"):
            continue
        for m in re.finditer(r'set\s+(?:/a\s+|/p\s+\w+\s*=?)?[^\w]?([A-Za-z_]\w*)=', ln):
            assigned.add(m.group(1).lower())
        for m in re.finditer(r'for\s+.*?%%([A-Za-z])', ln):
            assigned.add("%" + m.group(1).lower())
    for i in range(1, 10):
        assigned.add(str(i))

    used, used_bang = set(), set()
    for ln in lines:
        if ln.strip().lower().startswith("rem"):
            continue
        used |= {m.group(1).lower() for m in re.finditer(r"%([A-Za-z_]\w*)%", ln)}
        used_bang |= {m.group(1).lower() for m in re.finditer(r"!([A-Za-z_]\w*)!", ln)}
    for v in sorted((used | used_bang) - assigned - BUILTIN):
        problems.append(f"引用了未赋值的变量: {v}")
    print(f"  变量: set 过 {len(assigned)} 个，引用 %VAR% {len(used)} / !VAR! {len(used_bang)}")

    # 4) 括号配对（跳过 rem 行、引号内的、被 ^ 转义的）
    depth = 0
    for ln in lines:
        s = ln.strip()
        if s.lower().startswith("rem"):
            continue
        in_q, i = False, 0
        while i < len(s):
            c = s[i]
            if c == '"':
                in_q = not in_q
            elif not in_q:
                if c == "^" and i + 1 < len(s) and s[i + 1] in "()":
                    i += 1
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth < 0:
                        problems.append(f"多余的 ) : {s[:70]}")
                        depth = 0
            i += 1
    if depth != 0:
        problems.append(f"括号未配对，结束时还剩 {depth} 个未闭合的 (")

    # 5) 可选参数白名单 vs help 文本
    #    只认「两列对齐」的写法：参数名后面跟着 2+ 个空格才开始写说明。
    #    否则句子里的 "Run start.bat to bring ..." 会被误当成参数名 "to"。
    m = re.search(r"for %%O in \(([^)]*)\)", src)
    known = set(m.group(1).split()) if m else set()
    helped = set(re.findall(r"start\.bat\s+([a-z]+)\s{2,}", src))
    for a in sorted(helped - known):
        problems.append(f"help 里写了参数 {a}，但 OPT_KNOWN 白名单里没有")
    for a in sorted(known - helped):
        problems.append(f"OPT_KNOWN 里有 {a}，但 help 文本没写")
    print(f"  可选参数: {sorted(known)}")

    print()
    if problems:
        print(f"发现 {len(problems)} 个问题:")
        for p in problems:
            print("  ✗", p)
        return 1
    print("✓ 静态检查通过：无非法字符、label 完整、变量均已赋值、括号配对、参数一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
