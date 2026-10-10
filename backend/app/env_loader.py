# -*- coding: utf-8 -*-
"""env_loader.py — 项目 .env 加载的**单一真源**（single source of truth）
=========================================================================
本模块把「打开哪个 .env、按什么顺序、能否覆盖已有环境变量」这一件事**只在这里**定义，
供两处消费：

  - ``app/__init__.py``：在**包被导入的最早时机**调用一次 ``load_project_env()``，
    保证任何 ``import app.xxx`` 之前 .env 已经就位（顺带修复历史上 ``DATA_ROOT``
    因加载时序倒置而「从未生效」的静默缺陷，见下）；
  - ``app/db.py``：``_load_env()`` 改为调用本模块，``ENV_FILE`` 语义（首个命中的
    ``Path`` 或 ``None``）保持不变。

候选文件与顺序（**逐字沿用**原 ``db.py::_load_env``，**不得改动**——它决定 DB 凭据来源）：
 1) 环境变量 ``DATA_ADMIN_ENV_FILE`` 指向的文件；
 2) ``D:\\apps\\data-admin\\.env``（生产部署目录，C 盘外）；
 3) ``<仓库根>/.env``；
 4) ``<仓库>/backend/.env``。

语义（务必守住）：
 - **只加载首个存在的文件**随即返回，后续候选一律不看；
 - ``load_dotenv(..., override=False)``：**不覆盖**已存在的环境变量
   （真实环境变量 / CI 注入值优先）；
 - **幂等**：模块级哨兵，多次调用只真正加载一次，重复调用直接返回首次结果。

本机实际情况：候选 2 ``D:\\apps\\data-admin\\.env`` **已存在**（含 ``MYSQL_*`` / ``DATA_ROOT``），
故它会被命中，候选 3/4 被遮蔽——**这正是要把 ``AGENT_TOKEN`` 放进候选 2 的原因**
（在仓库根新建 .env 是无效的）。

时序修正（零漂移）：``config.py`` 在**导入时**就会求值 ``os.environ.get("DATA_ROOT", <默认>)``
（取不到才回落内置默认），而历史上 ``load_dotenv`` 要等到 ``alerts -> db`` 被导入才发生
（``main.py`` 的 routers 元组里 ``agent_proxy`` 排在 ``alerts`` 之前）——顺序倒置使 .env 里的
``DATA_ROOT`` **从未生效**。把加载提到包导入的最早时机即修好此缺陷；本机 .env 的 ``DATA_ROOT``
与代码默认值等价（见 ``tests/test_env_loader.py::test_zero_drift_data_root_matches_env_file``），
故为 **零漂移**。

依赖约束：**只依赖标准库 + python-dotenv**，禁止 import 本项目其它模块（防循环依赖）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Union

from dotenv import load_dotenv

#: 本文件位于 ``<仓库根>/backend/app/env_loader.py``
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_BACKEND_DIR: Path = Path(__file__).resolve().parents[1]

#: 候选 2：生产部署目录下的 .env（C 盘外，本机实际生效文件）
_PROD_ENV_FILE: str = r"D:\apps\data-admin\.env"

# ---------------------------------------------------------------------------
# 幂等哨兵（模块级）：与 load_project_env 成对使用，勿在别处直接读写
# ---------------------------------------------------------------------------
_LOADED: bool = False                  #: 是否已加载过（含「已确认无文件」的情形）
_LOADED_PATH: Optional[Path] = None    #: 实际加载的文件路径；None 表示一个都没命中

#: 候选路径元素类型（str 或 Path，load_dotenv / Path 均接受）
_Candidate = Union[str, Path]


def _static_fallback_env_files() -> List[_Candidate]:
    """候选 2~4（固定回退项）。抽成函数便于测试注入，**勿删**。

    Returns:
        依次为：生产 .env、``<仓库根>/.env``、``<仓库>/backend/.env``。
    """
    return [
        _PROD_ENV_FILE,
        _REPO_ROOT / ".env",
        _BACKEND_DIR / ".env",
    ]


def env_candidates() -> List[_Candidate]:
    """按优先级返回候选 .env 列表（顺序即加载优先级）。

    第一项取自环境变量 ``DATA_ADMIN_ENV_FILE``（可能为 ``None``），随后是固定回退项。
    """
    return [os.environ.get("DATA_ADMIN_ENV_FILE"), *_static_fallback_env_files()]


def load_project_env() -> Optional[Path]:
    """加载项目 .env（**幂等**）。

    Returns:
        首个实际加载的 .env 文件 ``Path``；一个候选都没命中则返回 ``None``。

    Notes:
        - 重复调用是**空操作**，直接返回首次结果（不会二次读取任何文件）；
        - 每个候选都用 ``load_dotenv(c, override=False)`` 加载，**不覆盖**已有环境变量；
        - 只加载**首个存在**的候选文件，随即返回（与历史 ``db._load_env`` 逐字一致）。
    """
    global _LOADED, _LOADED_PATH
    if _LOADED:
        return _LOADED_PATH

    # 先置哨兵，保证即使中途异常也不会重复进入（加载失败即视为「无文件」）
    _LOADED = True
    _LOADED_PATH = None
    for candidate in env_candidates():
        if candidate and Path(candidate).is_file():
            load_dotenv(candidate, override=False)
            _LOADED_PATH = Path(candidate)
            break
    return _LOADED_PATH
