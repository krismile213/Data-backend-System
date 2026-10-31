# -*- coding: utf-8 -*-
"""test_env_loader.py — .env「单一真源」（app/env_loader.py）与「最早时机」加载的回归测试。

覆盖（全部用 tmp_path + monkeypatch，**绝不触碰真实 .env**）：
  1. 幂等：连续多次调用只真正加载一次（第二次即便环境变量已被改动也不重读文件）；
  2. 只加载**首个存在**的候选（第二个候选文件里的键不得进入 os.environ）；
  3. 核心回归（subprocess 起新解释器隔离）：在**不导入 app.db** 的前提下，
     ``import app.config`` 之后 ``os.environ["AGENT_TOKEN"]`` 已从 .env 就位，
     且 ``DATA_ROOT`` 的时序修正生效（.env 的值真正到达 config）；
  4. ``override=False``：预先存在的环境变量不被 .env 覆盖；
  5. 候选全不存在 → 返回 ``None``，不抛异常；
  6. 零漂移：``config.DATA_ROOTS["手机膜"]`` 与生产 .env 的 ``DATA_ROOT`` 路径等价。
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

import pytest

import app.env_loader as env_loader

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"

#: 生产部署目录下的 .env（本机实际生效文件）
_PROD_ENV_FILE = Path(r"D:\apps\data-admin\.env")


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------
def _write_env(path: Path, mapping: Dict[str, str]) -> None:
    """把 ``mapping`` 写成一个极简 .env 文件（每行一个 ``KEY=VALUE``）。"""
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in mapping.items()),
        encoding="utf-8",
    )


def _reset_loader_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """把幂等哨兵重置为「未加载」，并隔离固定回退候选（避免碰到真实路径）。"""
    monkeypatch.setattr(env_loader, "_LOADED", False)
    monkeypatch.setattr(env_loader, "_LOADED_PATH", None)


def _parse_env_value(path: Path, key: str) -> Optional[str]:
    """从 .env 文本里手工取一个键的值（不污染 os.environ）。找不到返回 None。"""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return None


# ---------------------------------------------------------------------------
# 1. 幂等
# ---------------------------------------------------------------------------
def test_load_project_env_is_idempotent(tmp_path, monkeypatch):
    env_file = tmp_path / "idem.env"
    _write_env(env_file, {"ENVLOADER_IDEM_KEY": "one"})

    monkeypatch.setenv("DATA_ADMIN_ENV_FILE", str(env_file))
    monkeypatch.setattr(env_loader, "_static_fallback_env_files", lambda: [])
    monkeypatch.delenv("ENVLOADER_IDEM_KEY", raising=False)
    _reset_loader_state(monkeypatch)

    # 包装真实的 load_dotenv，统计「真正被调用」的次数
    calls = {"n": 0}
    real_load_dotenv = env_loader.load_dotenv

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return real_load_dotenv(*args, **kwargs)

    monkeypatch.setattr(env_loader, "load_dotenv", _counting)

    first = env_loader.load_project_env()
    assert first == env_file
    assert os.environ["ENVLOADER_IDEM_KEY"] == "one"
    assert calls["n"] == 1

    # 第二次调用必须是空操作：即便环境变量已被改动，也不重新读取文件
    monkeypatch.delenv("ENVLOADER_IDEM_KEY", raising=False)
    second = env_loader.load_project_env()
    assert second == first
    assert calls["n"] == 1                          # 未再次加载
    assert "ENVLOADER_IDEM_KEY" not in os.environ   # 也未被重新写回


# ---------------------------------------------------------------------------
# 2. 只加载首个存在的候选
# ---------------------------------------------------------------------------
def test_only_first_existing_candidate_is_loaded(tmp_path, monkeypatch):
    first = tmp_path / "first.env"
    second = tmp_path / "second.env"
    _write_env(first, {"ENVLOADER_FIRST": "a"})
    _write_env(second, {"ENVLOADER_SECOND": "b"})

    monkeypatch.setenv("DATA_ADMIN_ENV_FILE", str(first))          # 候选 1（存在）
    monkeypatch.setattr(env_loader, "_static_fallback_env_files",
                        lambda: [second])                          # 候选 2（也存在）
    monkeypatch.delenv("ENVLOADER_FIRST", raising=False)
    monkeypatch.delenv("ENVLOADER_SECOND", raising=False)
    _reset_loader_state(monkeypatch)

    loaded = env_loader.load_project_env()
    assert loaded == first
    assert os.environ.get("ENVLOADER_FIRST") == "a"
    assert "ENVLOADER_SECOND" not in os.environ   # 第二个候选被短路、未被加载


# ---------------------------------------------------------------------------
# 3. 核心回归：不导入 app.db，import app.config 后 AGENT_TOKEN 已就位
# ---------------------------------------------------------------------------
_CORE_REGRESSION_SCRIPT = r'''
import os
import sys

backend = sys.argv[1]
env_file = sys.argv[2]
sys.path.insert(0, backend)

# 保证「就位」的 AGENT_* / DATA_ROOT 只可能来自 .env，而非父进程继承
for key in ("AGENT_TOKEN", "AGENT_BASE_URL", "AGENT_TIMEOUT", "DATA_ROOT"):
    os.environ.pop(key, None)

os.environ["DATA_ADMIN_ENV_FILE"] = env_file

import app.config as config            # 导入 app 包 -> __init__ 调 load_project_env()
import app.env_loader as env_loader

print("DB_IMPORTED", "app.db" in sys.modules)
print("AGENT_TOKEN", os.environ.get("AGENT_TOKEN"))
print("LOADED_PATH", str(env_loader._LOADED_PATH))
print("CONFIG_DATA_ROOT", str(config.DATA_ROOT))
'''


def test_config_import_loads_env_without_importing_db(tmp_path):
    env_file = tmp_path / "subproc.env"
    data_root = tmp_path / "dataroot"
    _write_env(env_file, {
        "AGENT_TOKEN": "tok-from-tmp-env",
        "AGENT_BASE_URL": "http://127.0.0.1:9999",
        "AGENT_TIMEOUT": "123",
        "DATA_ROOT": str(data_root),
    })

    child_env = dict(os.environ)
    for key in ("AGENT_TOKEN", "AGENT_BASE_URL", "AGENT_TIMEOUT", "DATA_ROOT"):
        child_env.pop(key, None)
    child_env["DATA_ADMIN_ENV_FILE"] = str(env_file)
    child_env["PYTHONIOENCODING"] = "utf-8"

    result = subprocess.run(
        [sys.executable, "-c", _CORE_REGRESSION_SCRIPT,
         str(BACKEND_DIR), str(env_file)],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(REPO_ROOT), env=child_env,
    )
    assert result.returncode == 0, (
        f"subprocess 非零退出：\nstdout={result.stdout!r}\nstderr={result.stderr!r}")

    reported = dict(
        line.split(" ", 1)
        for line in result.stdout.strip().splitlines()
        if " " in line
    )
    assert reported["DB_IMPORTED"] == "False"                # 全程未依赖 app.db
    assert reported["AGENT_TOKEN"] == "tok-from-tmp-env"     # config 导入时已就位
    assert reported["LOADED_PATH"] == str(env_file)          # 命中的正是本地 .env
    assert reported["CONFIG_DATA_ROOT"] == str(data_root)    # DATA_ROOT 时序修正生效


# ---------------------------------------------------------------------------
# 4. override=False：预置环境变量不被覆盖
# ---------------------------------------------------------------------------
def test_existing_env_var_not_overridden(tmp_path, monkeypatch):
    env_file = tmp_path / "override.env"
    _write_env(env_file, {"ENVLOADER_OVERRIDE": "fromfile"})

    monkeypatch.setenv("DATA_ADMIN_ENV_FILE", str(env_file))
    monkeypatch.setattr(env_loader, "_static_fallback_env_files", lambda: [])
    monkeypatch.setenv("ENVLOADER_OVERRIDE", "preexisting")
    _reset_loader_state(monkeypatch)

    env_loader.load_project_env()
    assert os.environ["ENVLOADER_OVERRIDE"] == "preexisting"


# ---------------------------------------------------------------------------
# 5. 候选全不存在 → None，不抛异常
# ---------------------------------------------------------------------------
def test_no_candidate_returns_none(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_ADMIN_ENV_FILE", raising=False)
    monkeypatch.setattr(env_loader, "_static_fallback_env_files",
                        lambda: [tmp_path / "nope-1.env", tmp_path / "nope-2.env"])
    _reset_loader_state(monkeypatch)

    assert env_loader.load_project_env() is None


# ---------------------------------------------------------------------------
# 6. 零漂移：config.DATA_ROOTS 与生产 .env 的 DATA_ROOT 路径等价
# ---------------------------------------------------------------------------
def test_zero_drift_data_root_matches_env_file():
    import app.config as config

    if not _PROD_ENV_FILE.is_file():
        pytest.skip(f"本机生产 .env 不存在：{_PROD_ENV_FILE}")

    env_data_root = _parse_env_value(_PROD_ENV_FILE, "DATA_ROOT")
    if not env_data_root:
        pytest.skip("生产 .env 未定义 DATA_ROOT")

    expected = Path(env_data_root)
    assert os.path.normcase(str(config.DATA_ROOTS["手机膜"])) == \
        os.path.normcase(str(expected))
