# -*- coding: utf-8 -*-
"""outbound_guard.py — BFF 出站脱敏的**纯函数**实现（T10-a / docs/AI助手T10-P1实现契约.md §2）

本模块是平台后端「**唯一出站收口**」（``routers/agent_proxy.py::_sanitize_outbound``）背后的
脱敏逻辑，只做两件事：

  1. **请求体脱敏**（``sanitize_request_body``）：上行 JSON 请求体里，命中敏感词典的**键整键剔除**、
     字符串值里的 11 位手机号**掩码**为 ``138****8000``；
  2. **上下文头脱敏**（``sanitize_context``）：``X-Plat-Context`` 头只保留 6 个白名单键
     （``CONTEXT_ALLOWED_KEYS``），未知键剔除，值做掩码/截断，并**重新 percent-encode** 成纯 ASCII。

真实数据流向（须知，别收错地方）：平台侧只需收口**上行**（用户消息 + ``X-Plat-Context``）；
**工具结果回灌不经过 BFF**（Agent 自己调 ``:8000`` 拿数据后直接发云端模型），下行已由 Agent 侧
``plugins/workmain.py::_plat_sanitize`` 实装（词典剔键 + 手机号掩码）。任何「在 BFF 里洗工具结果」
的写法都是错的——那段数据根本不经过 BFF。

⚠️ 前端 ``scanSensitiveText`` 只是 UX 提醒层，真正的强制保障是 BFF ``_sanitize_outbound()``
与 Agent 侧 ``_plat_sanitize()``。前端检测命中不了就放行，挡不住任何东西，**不是安全边界**。

依赖纪律（与 ``env_loader.py`` 同一纪律）：**只允许依赖标准库**（``json``/``re``/``urllib.parse``），
**禁止** import 本项目其它模块（防循环依赖）。

硬要求：**任何路径都不得抛异常**。异常 = 破坏助手可用性 = 把安全做成故障源。所有公开函数
（``sanitize_obj`` / ``sanitize_context`` / ``sanitize_request_body``）都「脱敏失败即保可用」：
解析失败/畸形 → 原样转发 + 记理由，绝不raise。
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote, unquote

# ---------------------------------------------------------------------------
# 敏感键词典：与 Agent 侧 ``plugins/workmain.py::_SENS_SUBSTR`` 对齐（本轮为**超集**，只增不减）
# 命中即**整键剔除**（大小写不敏感：统一 ``str(key).lower()`` 后做子串匹配）。
# ---------------------------------------------------------------------------
SENSITIVE_KEY_SUBSTR: tuple = (
    # —— 逐字对齐 Agent 侧 workmain.py::_SENS_SUBSTR（必须包含，不得删减）——
    "负责人", "操作人", "创建人", "姓名", "手机", "电话", "联系方式",
    "备注", "owner", "creator", "operator", "phone", "mobile", "remark",
    # —— 本轮新增（宁可保守、不可漏挡；已核对不与上下文白名单键冲突）——
    "联系", "邮箱", "email", "mail", "微信", "wechat", "身份证", "idcard", "contact",
)

#: ``X-Plat-Context`` 头**只保留**这些键（任务书 §4.1 约定；「白名单 6 键」）。
CONTEXT_ALLOWED_KEYS = ("page", "page_label", "category", "batch", "selected_sku", "filters")

#: 单个上下文值最大字符数，超出截断（按字符计，不是字节）。
CONTEXT_VALUE_MAX = 80

#: 编码后头值最大长度，超出**整体丢弃**该头（防止超长头撑爆上游）。
CONTEXT_HEADER_MAX = 2048

#: 11 位手机号：前后不夹数字（``(?<!\d)…(?!\d)``），避免「138123456789」这类被误伤。
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def _mask_phones(text: str) -> str:
    """把 ``text`` 里的 11 位手机号掩码为 ``138****8000``（保留首 3 位 + 后 4 位）。"""
    return _PHONE_RE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[7:], text)


def _new_stats(reason: str | None = None) -> dict:
    """新建一份 stats（计数从 0 起）。``reason`` 非空时一并写入（供契约 §2.2 断言）。"""
    stats = {"dropped_keys": 0, "masked_values": 0, "changed": False}
    if reason is not None:
        stats["reason"] = reason
    return stats


def _sanitize_obj(obj, counter: dict):
    """``sanitize_obj`` 的递归实现：就地累加 ``counter`` 计数，返回清洗后的对象。

    Args:
        obj: 任意 JSON 值（dict/list/str/标量）。
        counter: 可变计数容器，键为 ``dropped_keys`` / ``masked_values`` / ``changed``。

    Returns:
        与 ``obj`` 同构、已脱敏的新对象（不修改入参）。
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = k if isinstance(k, str) else str(k)
            if any(sub in key.lower() for sub in SENSITIVE_KEY_SUBSTR):
                counter["dropped_keys"] += 1
                counter["changed"] = True
                continue
            out[key] = _sanitize_obj(v, counter)
        return out
    if isinstance(obj, list):
        return [_sanitize_obj(x, counter) for x in obj]
    if isinstance(obj, str):
        masked = _mask_phones(obj)
        if masked != obj:
            counter["masked_values"] += 1
            counter["changed"] = True
        return masked
    # int / float / bool / None 一律原样（不改数值）。
    return obj


def sanitize_obj(obj) -> tuple:
    """递归脱敏任意 JSON 对象。

    规则（契约 §2.2 #1~#5）：
      - dict：键名（``str(k).lower()``）命中 ``SENSITIVE_KEY_SUBSTR`` → **整键剔除**；
      - list / 嵌套结构：**递归**同样处理（深度不设限）；
      - str：掩码 11 位手机号；
      - 其它类型（int/float/bool/None）：原样；
      - 顶层非 dict（如 list）：仍递归处理。

    Args:
        obj: 任意可 JSON 化的对象。

    Returns:
        ``(清洗后的对象, stats)``；``stats`` = ``{"dropped_keys": int,
        "masked_values": int, "changed": bool}``。**绝不抛异常**（异常时原样返回 + reason）。
    """
    counter = _new_stats()
    try:
        return _sanitize_obj(obj, counter), counter
    except Exception:  # noqa: BLE001 — 兜底：脱敏失败即保可用，不打断助手
        return obj, _new_stats("error")


def _parse_context(value: str):
    """解析上下文头：先按原样 JSON，失败再 ``unquote`` 后 JSON（与 Agent 侧一致）。

    Returns:
        解析出的对象；两种都失败返回 ``None``。
    """
    for candidate in (value, unquote(value)):
        try:
            return json.loads(candidate)
        except Exception:  # noqa: BLE001 — 尝试下一候选
            continue
    return None


def _sanitize_context_value(v, counter: dict) -> str:
    """清洗单个白名单键的标量值：强转 str → 掩码手机号 → 超长截断。"""
    if not isinstance(v, str):
        v = str(v)  # 契约 §2.2 #9：非字符串强转 str（不抛）
    masked = _mask_phones(v)
    if masked != v:
        counter["masked_values"] += 1
        counter["changed"] = True
    if len(masked) > CONTEXT_VALUE_MAX:
        masked = masked[:CONTEXT_VALUE_MAX]  # 契约 §2.2 #10：截断并计入 stats
        counter["changed"] = True
    return masked


def _sanitize_filters(v, counter: dict):
    """清洗 ``filters``：只收**扁平标量**，嵌套（dict/list）一律丢；非 dict 丢弃该键。

    Returns:
        清洗后的扁平 dict；``v`` 非 dict 时返回 ``None``（调用方据此丢弃该键）。
    """
    if not isinstance(v, dict):
        return None
    flat = {}
    for fk, fv in v.items():
        key = fk if isinstance(fk, str) else str(fk)
        if isinstance(fv, (dict, list)):
            counter["dropped_keys"] += 1
            counter["changed"] = True
            continue
        val = fv if isinstance(fv, str) else str(fv)
        masked = _mask_phones(val)
        if masked != val:
            counter["masked_values"] += 1
            counter["changed"] = True
        if len(masked) > CONTEXT_VALUE_MAX:
            masked = masked[:CONTEXT_VALUE_MAX]
            counter["changed"] = True
        flat[key] = masked
    return flat


def _encode_context(obj: dict) -> str:
    """序列化 + percent-encode，返回**纯 ASCII** 的头值（``quote(..., safe='')`` 与 JS 的
    ``encodeURIComponent`` 行为一致）。"""
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return quote(raw, safe="")


def sanitize_context(value) -> tuple:
    """``X-Plat-Context`` 头值 → 可安全转发的已编码头值（契约 §2.2 #6~#13）。

    规则：
      - 缺失/空串 → ``(None, {"reason": "absent"})``（**不添加该头**，向后兼容）；
      - 合法 JSON（ASCII 或 percent-encoded）→ 只保留 ``CONTEXT_ALLOWED_KEYS``，
        未知键计入 ``dropped_keys``；值强转 str、掩码手机号、超长截断；``filters`` 只收扁平标量；
      - 解析失败/顶层非 dict → ``(None, {"reason": "malformed"})``（行为同「没带」）；
      - 编码后长度 > ``CONTEXT_HEADER_MAX`` → ``(None, {"reason": "toolong"})``。

    Args:
        value: 原始头值（``str``；缺失时为 ``None``）。

    Returns:
        ``(可安全转发的已编码头值 | None, info)``；``info`` 含 ``reason`` 与 stats。**绝不抛异常**。
    """
    try:
        if value is None:
            return None, _new_stats("absent")
        if not isinstance(value, str):
            value = str(value)  # 契约 §2.2 #9：不抛
        if value.strip() == "":
            return None, _new_stats("absent")

        obj = _parse_context(value)
        if not isinstance(obj, dict):
            return None, _new_stats("malformed")

        counter = _new_stats()
        out = {}
        for k, v in obj.items():
            key = k if isinstance(k, str) else str(k)
            if key not in CONTEXT_ALLOWED_KEYS:
                counter["dropped_keys"] += 1
                counter["changed"] = True
                continue
            if key == "filters":
                flat = _sanitize_filters(v, counter)
                if flat is None:
                    counter["dropped_keys"] += 1
                    counter["changed"] = True
                    continue
                out[key] = flat
            else:
                out[key] = _sanitize_context_value(v, counter)

        encoded = _encode_context(out)
        if len(encoded) > CONTEXT_HEADER_MAX:
            return None, _new_stats("toolong")
        counter["reason"] = "ok"
        return encoded, counter
    except Exception:  # noqa: BLE001 — 兜底：解析/编码任何失败都退化成「没带」
        return None, _new_stats("malformed")


def _is_json_content_type(content_type) -> bool:
    """``content-type`` 是否 JSON（忽略 ``; charset=...`` 后缀与大小写）。"""
    if not content_type:
        return False
    main = str(content_type).split(";", 1)[0].strip().lower()
    return main == "application/json"


def sanitize_request_body(body: bytes, content_type) -> tuple:
    """出站请求体 → 可安全转发的字节（契约 §2.2 #14~#16）。

    规则：
      - ``content-type`` 非 JSON → **原样返回**（绝不改写非 JSON）；理由记入 stats；
      - JSON 但解析失败 → 原样返回（绝不抛）；
      - JSON 解析成功 → ``sanitize_obj`` 后以 ``ensure_ascii=False`` 重序列化为 UTF-8（中文不转义）。

    Args:
        body: 即将转发的请求体字节（UTF-8）。
        content_type: 请求头里的 ``content-type``。

    Returns:
        ``(可安全转发的字节, stats)``。**绝不抛异常**。
    """
    try:
        if _is_json_content_type(content_type):
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception:  # noqa: BLE001 — 解析失败即原样转发
                return body, _new_stats("parse_error")
            cleaned, stats = sanitize_obj(data)
            try:
                return json.dumps(cleaned, ensure_ascii=False).encode("utf-8"), stats
            except Exception:  # noqa: BLE001 — 序列化失败即原样转发
                return body, _new_stats("serialize_error")
        return body, _new_stats("not_json")
    except Exception:  # noqa: BLE001 — 兜底：脱敏失败即保可用
        return body, _new_stats("error")
