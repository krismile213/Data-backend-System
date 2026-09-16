# -*- coding: utf-8 -*-
"""conftest.py — pytest 全局配置：把 backend 目录加入 sys.path"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
