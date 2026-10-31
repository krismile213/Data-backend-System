# -*- coding: utf-8 -*-
"""撞锁改名落盘（2026-10-20 实测事故：14:10 手机膜日报被 Excel 占用，
`_with_lock_retry` 等 60s×5 仍写不进 → 整轮 FAIL，当天停在 09:00 版）。

`_write_report_bytes` 的契约必须钉死：
- 正式名写成功 → 原样返回 (path, False)，内容落正式名；
- 正式名撞锁 → 写真名_HHMMSS 后缀，返回 (alt, True)，内容一样不丢；
- 两个名字都撞锁 → 抛 RuntimeError（人话 + 含"被占用"，_is_lock_error 认得，
  auto_pipeline 的重试/告警语义不断）；
- `_drop_fallback_reports`：正式名写成功时清同日残留；别的日期的后缀文件、
  正式名本体都不能动；数据根之外的路径一律不碰（防索引被改坏后误删）。
"""

import builtins
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import inflight_service as SUT  # noqa: E402


def _lock_open(targets):
    """返回一个 open 替身：对 targets 里的路径以写模式打开就抛 PermissionError。"""
    real = builtins.open
    locked = {str(t) for t in targets}

    def _fake(file, mode="r", *a, **kw):
        if "w" in mode and str(file) in locked:
            raise PermissionError(13, "Permission denied")
        return real(file, mode, *a, **kw)

    return _fake


def test_primary_write_ok(tmp_path, monkeypatch):
    path = tmp_path / "事中日报_20261020.xlsx"
    got, fallback = SUT._write_report_bytes(path, b"xlsx-bytes")
    assert fallback is False
    assert got == path
    assert path.read_bytes() == b"xlsx-bytes"


def test_primary_locked_falls_back_with_hhmmss(tmp_path, monkeypatch):
    path = tmp_path / "事中日报_20261020.xlsx"
    path.write_bytes(b"stale-0900")          # 正式名存在且被"占用"
    monkeypatch.setattr(builtins, "open", _lock_open([path]))

    got, fallback = SUT._write_report_bytes(path, b"fresh-1530")
    assert fallback is True
    assert got.name.startswith("事中日报_20261020_")
    assert got.name.endswith(".xlsx")
    stamp = got.stem.rsplit("_", 1)[-1]
    assert len(stamp) == 6 and stamp.isdigit()      # HHMMSS
    assert got.read_bytes() == b"fresh-1530"        # 内容不丢
    assert path.read_bytes() == b"stale-0900"       # 被占用的旧文件没被动


def test_both_locked_raises_human_error(tmp_path, monkeypatch):
    path = tmp_path / "事中日报_20261020.xlsx"
    # 正式名 + 任何 _HHMMSS 后缀都锁死（用前缀匹配做不到，锁整个目录的写）
    real = builtins.open

    def _fake(file, mode="r", *a, **kw):
        if "w" in mode and str(file).startswith(str(tmp_path)):
            raise PermissionError(13, "Permission denied")
        return real(file, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", _fake)
    with pytest.raises(RuntimeError, match="被占用"):
        SUT._write_report_bytes(path, b"never-written")


def _make(root, name):
    p = Path(root) / name
    p.write_bytes(b"x")
    return p


def test_drop_fallback_removes_same_date_only(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.DATA_ROOTS", {"手机膜": str(tmp_path)})
    keep_proper = _make(tmp_path, "事中日报_20261020.xlsx")
    drop_today = _make(tmp_path, "事中日报_20261020_153000.xlsx")
    keep_other = _make(tmp_path, "事中日报_20261019_183000.xlsx")
    keep_legacy = _make(tmp_path, "复盘报告_20261020.xlsx")  # 旧命名不归我管

    removed = SUT._drop_fallback_reports(tmp_path, "20261020")
    assert removed == [str(drop_today)]
    assert keep_proper.exists() and keep_other.exists() and keep_legacy.exists()


def test_drop_fallback_respects_data_root_guard(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.DATA_ROOTS", {"手机膜": str(tmp_path / "root")})
    outside = _make(tmp_path, "事中日报_20261020_153000.xlsx")
    assert SUT._drop_fallback_reports(tmp_path, "20261020") is None
    assert outside.exists()          # 数据根之外一律不碰
