# -*- coding: utf-8 -*-
"""test_outbound_guard.py — BFF 出站脱敏纯函数单测（T10-a）

逐条覆盖 docs/AI助手T10-P1实现契约.md §2.2 的 17 条行为表，并额外守住硬要求：
**任何路径都不抛异常**（脱敏失败 = 破坏助手可用性 = 把安全做成故障源）。

纯函数测试，不依赖服务/网络，直接 import ``app.outbound_guard``。
"""

import json
from urllib.parse import quote, unquote

import pytest

from app.outbound_guard import (
    CONTEXT_ALLOWED_KEYS,
    CONTEXT_HEADER_MAX,
    CONTEXT_VALUE_MAX,
    SENSITIVE_KEY_SUBSTR,
    sanitize_context,
    sanitize_obj,
    sanitize_request_body,
)


# ===========================================================================
# 词典对齐（T06 §5.3 双保险：平台侧至少是 Agent 侧的超集）
# ===========================================================================

def test_sensitive_dict_is_superset_of_agent_side():
    """平台侧敏感词典必须**包含** Agent 侧 workmain.py::_SENS_SUBSTR（只增不减）。"""
    agent_substr = (
        "负责人", "操作人", "创建人", "姓名", "手机", "电话", "联系方式",
        "备注", "owner", "creator", "operator", "phone", "mobile", "remark",
    )
    missing = [s for s in agent_substr if s not in SENSITIVE_KEY_SUBSTR]
    assert missing == [], f"平台侧词典缺失 Agent 侧条目: {missing}"


# ===========================================================================
# §2.2 #1 敏感键整键剔除（大小写不敏感）
# ===========================================================================

def test_rule1_sensitive_keys_dropped():
    cleaned, stats = sanitize_obj({
        "当前负责人": "张三", "owner": "a", "备注": "内部", "sku": "S1", "count": 3,
    })
    assert cleaned == {"sku": "S1", "count": 3}
    assert stats["dropped_keys"] == 3
    assert stats["changed"] is True


def test_rule1_case_insensitive_key_match():
    """`OWNER` / `Phone` 等大小写变体同样命中（str(k).lower()）。"""
    cleaned, stats = sanitize_obj({"OWNER": "a", "Phone": "b", "Mobile": "c", "keep": 1})
    assert cleaned == {"keep": 1}
    assert stats["dropped_keys"] == 3


# ===========================================================================
# §2.2 #2 嵌套 dict / list / list[dict] 递归
# ===========================================================================

def test_rule2_recursive_nested_structures():
    obj = {
        "a": {"备注": "x", "keep": 1},
        "b": [{"owner": "o", "v": 2}],
        "c": [[{"phone": "p", "deep": 3}]],
    }
    cleaned, stats = sanitize_obj(obj)
    assert cleaned == {"a": {"keep": 1}, "b": [{"v": 2}], "c": [[{"deep": 3}]]}
    assert stats["dropped_keys"] == 3


# ===========================================================================
# §2.2 #3 字符串值里的 11 位手机号掩码
# ===========================================================================

def test_rule3_phone_masked_in_string_value():
    """掩码算法 = 首 3 位 + ``****`` + 末 4 位（与 Agent 侧 ``_plat_sanitize`` 逐字一致）。

    例：``13812348000`` → ``138****8000``。
    """
    cleaned, stats = sanitize_obj({"msg": "请联系 13812348000 确认"})
    assert cleaned["msg"] == "请联系 138****8000 确认"
    assert stats["masked_values"] == 1
    assert stats["changed"] is True


def test_rule3_mask_keeps_first3_last4():
    """明确守住「首 3 位 + **** + 末 4 位」——``13812345678`` → ``138****5678``。"""
    cleaned, _ = sanitize_obj({"n": "13812345678"})
    assert cleaned["n"] == "138****5678"


def test_rule3_phone_not_masked_when_adjacent_digits():
    """前后夹数字（如 12 位串）不掩码——守住 ``(?<!\\d)…(?!\\d)``。"""
    cleaned, stats = sanitize_obj({"n": "138123456789"})
    assert cleaned["n"] == "138123456789"
    assert stats["masked_values"] == 0


# ===========================================================================
# §2.2 #4 其它类型原样
# ===========================================================================

def test_rule4_non_string_types_untouched():
    obj = {"n": 1, "f": 1.5, "b": True, "z": None, "neg": -7}
    cleaned, stats = sanitize_obj(obj)
    assert cleaned == obj
    assert stats["changed"] is False


# ===========================================================================
# §2.2 #5 顶层非 dict（list）仍递归
# ===========================================================================

def test_rule5_top_level_list_recurses():
    cleaned, stats = sanitize_obj([{"owner": "x", "v": 1}, {"msg": "13800001111"}])
    assert cleaned == [{"v": 1}, {"msg": "138****1111"}]
    assert stats["dropped_keys"] == 1
    assert stats["masked_values"] == 1


# ===========================================================================
# §2.2 #6 上下文缺失/空串 → (None, {"reason":"absent"})
# ===========================================================================

def test_rule6_context_absent_and_empty():
    for raw in (None, "", "   "):
        value, info = sanitize_context(raw)
        assert value is None
        assert info["reason"] == "absent"


# ===========================================================================
# §2.2 #7 合法 JSON 上下文 → 只保留白名单键
# ===========================================================================

def test_rule7_context_whitelist_only():
    raw = json.dumps({"page": "alerts", "page_label": "加急预警",
                      "unknown": "y", "owner": "张三"})
    encoded, info = sanitize_context(raw)
    assert encoded is not None
    obj = json.loads(unquote(encoded))
    assert set(obj.keys()) <= set(CONTEXT_ALLOWED_KEYS)
    assert "unknown" not in obj and "owner" not in obj
    assert obj["page"] == "alerts"
    assert info["dropped_keys"] == 2


# ===========================================================================
# §2.2 #8 percent-encoded JSON 兼容
# ===========================================================================

def test_rule8_percent_encoded_context_accepted():
    raw = json.dumps({"page": "records", "category": "手机膜"}, ensure_ascii=False)
    encoded, info = sanitize_context(quote(raw, safe=""))
    assert encoded is not None
    obj = json.loads(unquote(encoded))
    assert obj["category"] == "手机膜"
    assert info["reason"] == "ok"


# ===========================================================================
# §2.2 #9 上下文值非字符串 → 强转 str（不抛）
# ===========================================================================

def test_rule9_non_string_context_value_coerced():
    encoded, info = sanitize_context(json.dumps({"batch": 12345, "page": True}))
    assert encoded is not None
    obj = json.loads(unquote(encoded))
    assert obj["batch"] == "12345"
    assert obj["page"] == "True"


# ===========================================================================
# §2.2 #10 单值超长截断
# ===========================================================================

def test_rule10_value_truncated():
    long_value = "x" * (CONTEXT_VALUE_MAX + 50)
    encoded, info = sanitize_context(json.dumps({"page": long_value}))
    obj = json.loads(unquote(encoded))
    assert len(obj["page"]) == CONTEXT_VALUE_MAX
    assert info["changed"] is True


# ===========================================================================
# §2.2 #11 filters 非 dict → 丢弃；嵌套 → 丢
# ===========================================================================

def test_rule11_filters_non_dict_dropped():
    encoded, info = sanitize_context(json.dumps({"filters": "notadict", "page": "x"}))
    obj = json.loads(unquote(encoded))
    assert "filters" not in obj
    assert obj["page"] == "x"


def test_rule11_filters_flat_scalars_only():
    encoded, info = sanitize_context(json.dumps({"filters": {"a": 1, "n": {"z": 2}}}))
    obj = json.loads(unquote(encoded))
    assert obj["filters"] == {"a": "1"}          # 标量保留（强转 str），嵌套丢弃
    assert info["dropped_keys"] == 1


# ===========================================================================
# §2.2 #12 编码后超长 → 整体丢弃
# ===========================================================================

def test_rule12_oversize_header_dropped():
    big = "中" * CONTEXT_VALUE_MAX               # 中文 → percent-encode 后 9 字节/字
    raw = json.dumps({"page": big, "page_label": big, "category": big,
                      "batch": big, "selected_sku": big}, ensure_ascii=False)
    encoded, info = sanitize_context(raw)
    assert encoded is None
    assert info["reason"] == "toolong"


# ===========================================================================
# §2.2 #13 解析失败/畸形 → (None, {"reason":"malformed"})，不抛
# ===========================================================================

def test_rule13_malformed_context():
    for raw in ("not json {{{", "}{", "%zz", "[1,2,3]", "123", '"a"'):
        value, info = sanitize_context(raw)
        assert value is None
        assert info["reason"] == "malformed"


# ===========================================================================
# §2.2 #14 非 JSON body 原样返回
# ===========================================================================

def test_rule14_non_json_body_passthrough():
    body = "手机号 13812345678".encode("utf-8")
    for ct in ("text/plain", "", None, "application/octet-stream"):
        out, stats = sanitize_request_body(body, ct)
        assert out == body
        assert stats["reason"] == "not_json"


# ===========================================================================
# §2.2 #15 JSON 但解析失败 → 原样返回
# ===========================================================================

def test_rule15_json_parse_failure_passthrough():
    body = b"{not valid json"
    out, stats = sanitize_request_body(body, "application/json")
    assert out == body
    assert stats["reason"] == "parse_error"


# ===========================================================================
# §2.2 #16 JSON 成功 → 脱敏 + ensure_ascii=False 重序列化
# ===========================================================================

def test_rule16_json_body_sanitized_and_utf8_kept():
    body = json.dumps(
        {"owner": "张三", "msg": "call 13812348000", "category": "手机膜"},
        ensure_ascii=False).encode("utf-8")
    out, stats = sanitize_request_body(body, "application/json; charset=utf-8")
    obj = json.loads(out.decode("utf-8"))
    assert "owner" not in obj
    assert obj["msg"] == "call 138****8000"
    assert obj["category"] == "手机膜"
    assert "手机膜".encode("utf-8") in out        # 中文以原字节写入，未被 \u 转义
    assert stats["dropped_keys"] == 1
    assert stats["masked_values"] == 1


# ===========================================================================
# §2.2 #17 编码后头值必须纯 ASCII
# ===========================================================================

def test_rule17_encoded_header_is_ascii():
    raw = json.dumps({"page_label": "时效看板", "category": "手机膜"},
                     ensure_ascii=False)
    encoded, info = sanitize_context(raw)
    assert encoded is not None
    assert encoded.isascii()
    assert encoded == encoded.encode("ascii").decode("ascii")


# ===========================================================================
# 硬要求：任何路径都不抛异常（脱敏失败即保可用）
# ===========================================================================

@pytest.mark.parametrize("weird", [
    None, "", 123, 1.5, True, b"{}", [], {}, "}{", "%", "%%%", "null", "true",
    json.dumps({"filters": {"a": {"b": 1}}}),
    {"page": "x"},
    "[\u0000]",
    "\u0000",
])
def test_never_raises_on_weird_obj_and_context(weird):
    """fuzz：异常输入下 sanitize_obj / sanitize_context 都必须**不抛**。"""
    sanitize_obj(weird)
    sanitize_context(weird)


@pytest.mark.parametrize("ct", [None, "", "text/plain", "application/json",
                                "APPLICATION/JSON; charset=utf-8", 123, b"json"])
@pytest.mark.parametrize("body", [b"", b"{}", b"{", b"[]", b"null",
                                  "手机".encode("utf-8"), b'{"owner":"x"}'])
def test_never_raises_on_weird_body(ct, body):
    """fuzz：任意 body × content-type 组合下 sanitize_request_body 都**不抛**。"""
    sanitize_request_body(body, ct)


def test_oversize_thresholds_sanity():
    """常量与契约一致（防止有人改动阈值导致语义漂移）。"""
    assert CONTEXT_ALLOWED_KEYS == ("page", "page_label", "category", "batch",
                                    "selected_sku", "filters")
    assert CONTEXT_VALUE_MAX == 80
    assert CONTEXT_HEADER_MAX == 2048


# ===========================================================================
# T10-b 词典扩容（2026-10-10，**真实数据取证**）
# 依据：docs/AI助手脱敏词典扩容-20261010.md
# 证据来源：起 :8000 后逐个 platform_* 端点取真实响应、递归收集 499 个 dict 键。
# ===========================================================================

#: 真实响应里**确实出现过**的人员类键（探针实测）
REAL_PERSON_KEYS = ["executor", "owner", "operator",
                    "当前负责人", "创建人", "创建人部门", "历史审批人"]

#: 真实响应里的**合法业务键** —— 绝不能被剔除（防「扩词扩大化」把助手问瞎）
REAL_BUSINESS_KEYS = [
    "name", "l4Name", "partName", "pendingPartName", "_partName",
    "sku", "model", "batch", "status", "total", "count", "part", "node",
    "page", "pageSize", "approvalNo", "computedAt", "segDef", "byMode",
    "arrived", "overDays", "shipClosure", "transportExempt",
    "审批编号", "审批状态", "审批结果", "审批记录", "审批单标题",
    "新机型号", "参考型号", "序号", "目标区域", "站点需求收集",
]


def _survivors(keys):
    """把 ``keys`` 每个键配一个无害值送进 sanitize_obj，返回**存活**的键集合。"""
    cleaned, _ = sanitize_obj({k: "v" for k in keys})
    return set(cleaned.keys())


def test_expansion_forbids_bare_name_token():
    """扩容纪律：**禁止裸 ``name``**（实测会一次误伤 name/l4Name/partName/... 5 个合法键）。"""
    assert "name" not in SENSITIVE_KEY_SUBSTR


def test_expansion_has_no_duplicates_and_grew():
    """词典无重复条目，且确实扩容（≥ 57 条）。"""
    assert len(SENSITIVE_KEY_SUBSTR) == len(set(SENSITIVE_KEY_SUBSTR))
    assert len(SENSITIVE_KEY_SUBSTR) >= 57


def test_expansion_covers_real_person_keys():
    """真实数据里出现过的人员类键必须**全被剔除**（含本轮新发现的 executor / 历史审批人）。"""
    left = _survivors(REAL_PERSON_KEYS)
    assert left == set(), f"真实人员键未剔除: {sorted(left)}"


def test_expansion_covers_chinese_role_words():
    """中文职务 / 身份 / 联系方式类字段名（钉钉审批·物流表单高频）。"""
    words = ["申请人", "审核人", "经办人", "对接人", "主管",
             "收货人", "收件人", "寄件人", "处理人", "提交人", "跟进人", "执行人",
             "用户", "客户", "地址", "座机", "固话", "传真"]
    left = _survivors(words)
    assert left == set(), f"未剔除: {sorted(left)}"


def test_expansion_covers_english_variants():
    """英文职务 / 物流角色 / 联系方式 / name 族复合词。"""
    words = ["handler", "approver", "applicant", "receiver", "consignee", "shipper",
             "addr", "tel", "fax", "qq", "username", "customername", "realname",
             "fullname"]
    left = _survivors(words)
    assert left == set(), f"未剔除: {sorted(left)}"


def test_expansion_does_not_touch_real_business_keys():
    """**误伤守卫**：真实业务键一个都不能被剔除。"""
    left = set(REAL_BUSINESS_KEYS) - _survivors(REAL_BUSINESS_KEYS)
    assert left == set(), f"误伤合法业务键: {sorted(left)}"


def test_known_overblock_by_bare_mobile_substring_documented():
    """⚠️ **已知误伤（既有行为，非本轮引入，尚未修）**：词典含裸 ``手机``，
    会把「手机膜类型 / 手机数据取得日期」这类**业务字段**一并剔除。

    已记入扩容文档「待决策（需要显式同意才改）」。若将来把 ``手机`` 收紧为 ``手机号``，
    本用例须同步改为断言「保留」。此用例存在的意义：让这个缺陷**不可能被遗忘**。
    """
    over_blocked = ["手机膜类型", "手机数据取得日期", "手机数据取得时间", "手机壳确样日期"]
    assert _survivors(over_blocked) == set(), "若此断言失败说明缺陷已被修复，请更新本用例"
