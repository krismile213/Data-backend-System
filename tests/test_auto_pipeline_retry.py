# -*- coding: utf-8 -*-
"""auto_pipeline 撞锁重试（2026-10-19 实测事故：14:10 那次手机膜日报被 WPS 占用，
PermissionError 直接让整轮 FAIL，当天手机膜停在 09:00 版）。

重试的边界必须钉死：
- 只有「占用/拒绝访问」类错误才重试，业务错误要立即抛出（不能把真 bug 拖 5 分钟）；
- 重试耗尽仍失败要抛错，不能静默出个空报告；
- sleep 可注入，测试不真的等。
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from etl.auto_pipeline import _is_lock_error, _with_lock_retry  # noqa: E402


class _Boom(Exception):
    pass


def test_lock_error_detection():
    assert _is_lock_error(PermissionError(13, "Permission denied"))
    # generate_daily_report 的真实形态：RuntimeError(msg) from PermissionError
    try:
        try:
            raise PermissionError(13, "Permission denied")
        except PermissionError as e:
            raise RuntimeError("日报文件被占用，写不进去：x.xlsx") from e
    except RuntimeError as e:
        assert _is_lock_error(e)
    # 纯文本兜底（跨进程传出来的错误只剩字符串）
    assert _is_lock_error(RuntimeError("Permission denied: 'a.xlsx'"))
    assert _is_lock_error(RuntimeError("[Errno 13] Permission denied"))
    # 业务错误不算锁
    assert not _is_lock_error(ValueError("缺少列：产品SKU"))
    assert not _is_lock_error(RuntimeError("复盘失败"))


def test_retry_then_success():
    """第 1 次撞锁、第 2 次成功 → 返回结果 + 重试 1 次，且真的 sleep 了一次。"""
    calls = {"n": 0}
    slept = []

    def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(13, "Permission denied")
        return {"path": "ok.xlsx"}

    r, tries = _with_lock_retry(fn, "手机膜", retries=3, wait=5,
                                sleep=slept.append)
    assert r == {"path": "ok.xlsx"}
    assert tries == 1
    assert calls["n"] == 2
    assert slept == [5]


def test_exhausted_raises():
    """一直撞锁 → 跑满 retries+1 次后抛错（不能静默返回空）。"""
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise PermissionError(13, "Permission denied")

    with pytest.raises(PermissionError):
        _with_lock_retry(fn, "手机膜", retries=2, wait=1, sleep=lambda s: None)
    assert calls["n"] == 3


def test_non_lock_error_fails_fast():
    """业务错误不重试：第 1 次就抛，sleep 不调用。"""
    slept = []

    def fn():
        raise ValueError("缺少列：产品SKU")

    with pytest.raises(ValueError):
        _with_lock_retry(fn, "手机膜", retries=5, wait=60, sleep=slept.append)
    assert slept == []


def test_retries_zero_means_single_attempt():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise PermissionError(13, "Permission denied")

    with pytest.raises(PermissionError):
        _with_lock_retry(fn, "手机膜", retries=0, wait=0, sleep=lambda s: None)
    assert calls["n"] == 1
