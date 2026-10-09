# -*- coding: utf-8 -*-
"""
db.py — MySQL 数据库连接层（企业化数据层）
==========================================
- 连接参数优先读环境变量，其次尝试 .env 文件（候选 / 顺序 / override 语义统一在
  app/env_loader.py，本模块仅薄封装，**不再自带候选逻辑**）：
    1) DATA_ADMIN_ENV_FILE 指定的路径
    2) D:\\apps\\data-admin\\.env   （生产部署目录，C 盘外；本机实际生效文件）
    3) 仓库根目录 .env
    4) backend/.env
- 表结构定义见 models.py；ETL 导入见 etl/import_excel.py
"""

import os
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .env_loader import load_project_env


def _load_env():
    """薄封装：.env 加载统一到单一真源 ``app.env_loader``（候选/顺序/override）。

    返回值语义**保持不变**：首个实际加载的 ``.env`` 的 ``Path``；一个都没命中返回 ``None``。
    幂等：重复调用直接返回首次结果（底层哨兵保证只真正加载一次）。
    """
    return load_project_env()


ENV_FILE = _load_env()

MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = os.environ.get("MYSQL_PORT", "3306")
MYSQL_USER = os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "data_admin")

DATABASE_URL = (
    f"mysql+pymysql://{MYSQL_USER}:{quote_plus(MYSQL_PASSWORD)}"
    f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,   # 取连接前探活，避免 MySQL 8h 断连
    pool_recycle=3600,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session():
    """FastAPI 依赖：会话（用完自动关闭）"""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
