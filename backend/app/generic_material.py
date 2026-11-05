# -*- coding: utf-8 -*-
"""
generic_material.py — 「素材通用」判定的**单一真源**
=====================================================
口径移植自 `流程复盘/route_check.py: is_generic_material`，2026-11-05 收敛到本模块，
供两个消费方共用，避免各写一套后漂移：

  - `backend/app/routers/alerts.py`  产品段目标（通用 ≤23 / 不通用 ≤38）
  - `backend/app/inflight_service.py` 事中日报总表「素材」列标注

判定依据（按单据创建时间倒序，取第一条能定性的记录）：
  1. `是否决定拍摄` = 否            → 通用（无需真机拍摄）
  2. `素材参考SKU` / `素材参考型号` 非空 → 通用（复用已有素材）
  3. `是否决定拍摄` = 是            → 不通用（需真机拍摄）
  4. 全部未填                       → 不通用（保守默认，按需拍摄）

⚠️ 为什么默认「不通用」：Part6 单据只在 Part3 完成后才创建（2026-11-05 实测：
   108 个可比 SKU 里 85 个与 Part3 完成同一秒建单，建单时间差中位 0 天）。
   也就是说流程走到 Part3 之前，「是否决定拍摄」**根本没有地方填**。
   此时若默认通用，会把 15 天拍摄 + 3 天素材加工从路径里抹掉，等于虚增进度。

⚠️ 素材通用省掉的时效：签样→寄送(5) + 寄送→拍摄交付(10) + 拍摄交付→素材加工(3)
   = 18 天（业务口径，2026-11-05 用户确认）。注意它与 alerts 的「通用≤23 / 不通用≤38」
   差 15 天**不是同一个数**，两者尚未对齐，改动前先跟业务确认口径，别直接相减。
"""

from datetime import datetime

# 「Part6 单据还没建（或建了但拍摄列没填）」的判定依据文案。
# 消费方（如日报总表）用它区分「需真机拍摄」与「还测不出来」，别写死中文字面量。
BASIS_UNKNOWN = "未标注拍摄/参考(默认按需拍摄)"


def _clean(v):
    """单元格值归一：None / nan / '-' / '--' / 空串 → ''。"""
    if v is None:
        return ""
    s = str(v).strip()
    if s in ("", "-", "--", "nan", "NaN", "None"):
        return ""
    return s


def _create(d):
    """建单时间：兼容 review 的 `create_time` 与 alerts 的 `create` 两种字段名。"""
    return d.get("create_time") or d.get("create") or datetime.min


def judge(part6_docs):
    """判定一个 SKU 的素材是否通用。

    参数：该 SKU 的 Part6 单据列表（每项含 `raw` 字段与建单时间）。
    返回：(是否通用, 判定依据说明)。
    """
    docs = sorted(part6_docs or [], key=_create, reverse=True)
    for d in docs:
        raw = d.get("raw") or {}
        decide = _clean(raw.get("是否决定拍摄"))
        ref_sku = _clean(raw.get("素材参考SKU"))
        ref_model = _clean(raw.get("素材参考型号"))
        if decide == "否" or ref_sku or ref_model:
            basis = []
            if decide == "否":
                basis.append("是否决定拍摄=否")
            if ref_sku or ref_model:
                basis.append(f"素材参考={ref_sku or ref_model}")
            return True, "；".join(basis)
        if decide == "是":
            return False, "是否决定拍摄=是(需真机拍摄)"
    return False, BASIS_UNKNOWN
