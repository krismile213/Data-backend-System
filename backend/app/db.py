# -*- coding: utf-8 -*-
"""
db.py — MySQL 数据库连接层（企业化数据层）
==========================================
- 连接参数优先读环境变量，其次依次尝试 .env 文件：
    1) DATA_ADMIN_ENV_FILE 指定的路径
    2) D:\\apps\\data-admin\\.env   （生产部署目录，C 盘外）
    3) 仓库根目录 .env             （开发默认）
- 表结构定义见 models.py；ETL 导入见 etl/import_excel.py
"""

import os
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _load_env():
    """按优先级加载 .env，返回实际加载的文件路径（未找到返回 None）"""
    candidates = [
        os.environ.get("DATA_ADMIN_ENV_FILE"),
        r"D:\apps\data-admin\.env",
        Path(__file__).resolve().parents[2] / ".env",
        Path(__file__).resolve().parents[1] / ".env",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            load_dotenv(c, override=False)
            return Path(c)
    return None


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
