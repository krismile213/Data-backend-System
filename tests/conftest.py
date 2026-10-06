# -*- coding: utf-8 -*-
"""conftest.py — pytest 全局配置：把 backend 目录加入 sys.path + 测试隔离前置。

测试隔离（2026-09-30 修复）：
    `routers/rules.py` / `main.py::lifespan` 会调 `rule_service.export_rules_snapshot()`，
    其默认路径是**真实**的 `资产文件\\规则快照.json`。测试里 `with TestClient(app)` 会触发
    lifespan → 于是每跑一轮 pytest 就把用户的真实快照 `generated_at` 漂移一次。
    这里在**最早时机**（模块导入、早于任何 `TestClient` 进入 lifespan）统一下发环境变量
    `RULES_SNAPSHOT_JSON`，指向仓库根被 `/_*` 忽略的临时文件，使所有 TestClient 启动路径都
    写不进真实文件。`rule_service.snapshot_path()` 每次调用都重读 env，故只要在这里设好即可。
    （与 `prewarm` 的测试前置同一思路：全局在收集/启动之前设好，而非仅单个用例的 fixture。）
"""

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = _REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 测试专用快照路径（仓库根 `_` 开头临时物，已被 .gitignore 的 `/_*` 忽略）。
# setdefault：若开发者已显式 export RULES_SNAPSHOT_JSON，则尊重之、不覆盖。
os.environ.setdefault(
    "RULES_SNAPSHOT_JSON",
    str(_REPO_ROOT / "_pytest_rules_snapshot.json"))
