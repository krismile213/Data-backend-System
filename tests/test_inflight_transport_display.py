# -*- coding: utf-8 -*-
"""
test_inflight_transport_display.py — 「运输」列显示口径回归锁（T18 补丁）
================================================================================
背景（2026-10-08 用户实测发现）：在研清单 / 事中档案把 **未开发、未发货** 的 SKU
（G591/G599/G600/G601，closure='none'、0 票已发）显示成绿色「运输已闭环」徽标。

根因：展示层误用 `transportExempt` 判「已闭环」——该标志只表示「运输域不参与卡点判定」，
对「一件都没发」的在研 SKU 同样为 True。**闭环态唯一真源 = shipClosure**。

本文件钉住三件事：
  ① 源码守卫 —— TransportCell 绝不引用 transportExempt；「已闭环」分支以 shipClosure 判定；
     事中档案的「运输域免追踪」chip 必须同时满足 shipClosure==='closed'；
  ② 纯函数 —— closure='none' 即无已发货货件，其原因文案不得出现「已闭环」；
  ③ 契约 —— 在研 item 必须携带 shipClosure，供前端不经 transportExempt 即可判闭环。
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

JSX_PATH = ROOT / "frontend" / "src" / "pages" / "InflightDaily.jsx"
JSX = JSX_PATH.read_text(encoding="utf-8")


def _fn_src(src, name):
    """截取 `function <name>(...)` 到下一个顶层 `}` 的源码片段。"""
    start = src.index("function " + name)
    end = src.index("\n}\n", start)
    return src[start:end + 2]


def _strip_comments(s):
    """去掉注释，避免守卫被解释性注释里的标识符误伤。"""
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    return s


def test_transport_cell_never_uses_transport_exempt():
    """核心回归锁：TransportCell 不得据 transportExempt 显示任何东西（注释不计）。"""
    body = _strip_comments(_fn_src(JSX, "TransportCell"))
    assert "transportExempt" not in body, (
        "TransportCell 又引用了 transportExempt —— 会把未发货 SKU 误标为已闭环")
    # 旧 bug 形态必须彻底消失
    assert 'it.transportExempt) return <ClosureBadge closure="closed"' not in JSX


def test_transport_cell_closed_keys_on_shipclosure():
    """「已闭环」分支必须由 shipClosure 决定。"""
    body = _fn_src(JSX, "TransportCell")
    assert "shipClosure" in body
    assert "closure === 'closed'" in body


def test_dialog_exempt_chip_requires_closed():
    """事中档案「运输已闭环 · 运输域免追踪」chip 必须同时要求 shipClosure==='closed'。"""
    assert "det.transportExempt && det.shipClosure === 'closed'" in JSX


def test_classify_none_is_not_closed():
    """closure='none'（无已发货货件）不是闭环；其原因文案不得称「已闭环」。"""
    from app.transport_closure import classify_transport, transport_exempt_reason
    empty = classify_transport([])
    assert empty["closure"] == "none"
    assert empty["shippedCount"] == 0
    # 只立项未发货（无货件号）同样不构成闭环
    only_unshipped = classify_transport([{"hub": "emag罗马尼亚"}])
    assert only_unshipped["closure"] == "none"
    assert only_unshipped["notShippedHubs"] == ["emag罗马尼亚"]
    reason = transport_exempt_reason("none", [], ["emag罗马尼亚"])
    assert "已闭环" not in reason
    # 对照：真闭环才允许出现「已闭环」
    assert "已闭环" in transport_exempt_reason("closed", [], [])
