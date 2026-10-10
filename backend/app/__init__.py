# -*- coding: utf-8 -*-
"""app 包入口 —— 在**包导入的最早时机**加载项目 .env（单一真源见 env_loader.py）。

为什么把加载放在这里（而不是各模块按需加载）：
  1) **修时序**：``config.py`` 在导入时就求值 ``os.environ.get("DATA_ROOT", <默认>)``（取不到才回落内置默认），但历史上
     ``load_dotenv`` 要等到 ``alerts -> db`` 被导入才发生（``main.py`` 的 routers 元组里
     ``agent_proxy`` 排在 ``alerts`` 之前），顺序倒置导致 .env 里的 ``DATA_ROOT``
     **从未生效**。把加载提到包导入前即可一并修正，且经实测为**零漂移**。
  2) **单一真源**：.env 的候选路径 / 顺序 / ``override`` 语义只在
     ``env_loader.load_project_env()`` 定义，这里只调用、不重复实现。
  3) **覆盖 AGENT_TOKEN**：``app.config`` 导入时 ``AGENT_TOKEN`` 已经就位，
     BFF 反向代理（``routers/agent_proxy.py``）即可在服务端注入 ``Authorization``。
"""

from .env_loader import load_project_env

# 最早时机：任何 ``import app.xxx`` 都会先执行本行。
load_project_env()
