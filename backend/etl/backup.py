# -*- coding: utf-8 -*-
"""
backup.py — 命令行备份入口
============================
与 POST /api/etl/backup 共用同一套逻辑（app.routers.dataadmin.run_backup），
供 start.py 的 backup 子命令调用（start.bat 只是转发壳），避免两处实现漂移。

用法：
    backend\\etl\\backup.py
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.routers.dataadmin import run_backup  # noqa: E402


def main():
    r = run_backup()
    print(f"备份目录 : {r['path']}")
    print(f"state    : {', '.join(r['stateFiles']) or '(无)'}")
    print(f"数据库   : {r['dump'] or '(未导出)'}   状态: {r['dumpStatus']}")
    if r.get("dumpError"):
        print(f"导出错误 : {r['dumpError']}")


if __name__ == "__main__":
    main()
