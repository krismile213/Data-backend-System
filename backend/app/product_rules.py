# -*- coding: utf-8 -*-
"""
product_rules.py — 商品资料档位规则（评级档位 → 日均销量区间）
================================================================
**权威来源**：`资产文件\\商品资料.xlsx` 的 `商品资料` 表 **A1 单元格说明全文**（原文见下）。

    A:1个月销量500套及以上。B:3个月销量500套及以上。C:6个月销量250套及以上。
    D:6个月销量200-250套。E:6个月销量200套以下的！
    X类为新品:XA：sku日均≥16.7,XB：16.7＞sku日均≥5.5,XC：5.5＞sku日均≥1.38,
              XD：1.38＞sku日均≥1.11,XE：1.11＞sku日均,X：暂无销售数据的。
    评级为E或者XE,不要采购。
    停售标准：…… 手机壳产品不参与以上产品评级规则！

为什么能自洽：A/B/C/D/E 是「套数/月数」阈值，X 系列是「日均」阈值，两者本是一套口径——
    500套 / 30天  ≈ 16.7  → A 下界 ≡ XA 下界
    500套 / 90天  ≈ 5.56  → B 下界 ≡ XB 下界
    250套 / 180天 ≈ 1.39  → C 下界 ≡ XC 下界
    200套 / 180天 ≈ 1.11  → D 下界 ≡ XD 下界、E 上界 ≡ XE 上界
    故本模块**优先解析 A1 原文**，再与内置表交叉校验；解析结果偏离内置表 >2% 时
    保留内置值并记警告（防止原文被改错而静默污染损益口径）。

对外只用两个东西：
    parse_grade_ranges(a1_text) -> (ranges, notes)   # ranges: {档位: (daily_min, daily_max|None)}
    GRADE_RANGES                                     # 内置兜底表（A1 缺失时用）
    range_of(grade, ranges=None)                     # 取档位区间（未命中 → (None, None)）
"""

import json
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# 内置兜底表（= 上面 A1 原文的解析结果；未命中档位一律 (None, None)，调用方降级处理）
# daily_max=None 表示上界开放（A/XA：日均≥16.7 无上限）
# ---------------------------------------------------------------------------
GRADE_RANGES = {
    "A":  (16.667, None),
    "B":  (5.556, 16.667),
    "C":  (1.389, 5.556),
    "D":  (1.111, 1.389),
    "E":  (0.0, 1.111),
    "XA": (16.7, None),
    "XB": (5.5, 16.7),
    "XC": (1.38, 5.5),
    "XD": (1.11, 1.38),
    "XE": (0.0, 1.11),
    "X":  (0.0, 0.0),          # 暂无销售数据
}

# 缺失/未命中档位时的兜底区间（rule_config 的 loss_daily_default 优先级更高，见 review 层）
DEFAULT_DAILY = {"min": 1.38, "max": 5.5}

_NUM = r"\d+(?:\.\d+)?"
# A1 里的套数档位：A:1个月销量500套及以上。 / D:6个月销量200-250套。
_VOL_RE = re.compile(rf"([A-E])\s*[:：]\s*(\d+)\s*个月\s*销量\s*({_NUM})"
                     rf"(?:\s*-\s*({_NUM}))?\s*套\s*(及以上|以下)?")
# A1 里的 X 系列档位：XA：sku日均≥16.7  / XB：16.7＞sku日均≥5.5 / X：暂无销售数据的
_X_RE = re.compile(r"(X[A-E]?)\s*[:：]\s*([^,，。;；！!]+)")


def _norm(s: str) -> str:
    """全角比较符归一 + 去掉干扰词。"""
    return (str(s).replace("＞", ">").replace("＜", "<")
            .replace("≧", "≥").replace("≦", "≤").replace("＝", "=")
            .replace("sku", "").replace("SKU", "").replace(" ", "")
            .replace("／", "/"))


def _parse_bound(cond: str):
    """把一段日均条件解析为 (min, max)；未出现的边为 None（表示开放）。

    支持：'日均≥16.7' / '16.7>日均≥5.5' / '1.11>日均' / '日均'（无数字）
    """
    t = _norm(cond)
    idx = t.find("日均")
    if idx < 0:
        return None, None
    left, right = t[:idx], t[idx + len("日均"):]
    lo = hi = None
    mr = re.search(rf"(≥|≤|>|<)\s*({_NUM})", right)
    if mr:
        op, v = mr.group(1), float(mr.group(2))
        if op in ("≥", ">"):
            lo = v
        else:            # ≤ / <
            hi = v
    ml = re.search(rf"({_NUM})\s*(≥|≤|>|<)", left)
    if ml:
        v, op = float(ml.group(1)), ml.group(2)
        if op in (">", "≥"):
            hi = v
        else:            # < / ≤
            lo = v
    return lo, hi


def _parse_x_grades(a1: str) -> dict:
    """解析 X 系列（XA..XE / X）→ {档位: (min, max)}。"""
    out = {}
    for grade, cond in _X_RE.findall(a1 or ""):
        c = _norm(cond)
        if "暂无销售数据" in c or ("日均" not in c and not re.search(_NUM, c)):
            out[grade] = (0.0, 0.0)
            continue
        lo, hi = _parse_bound(c)
        if lo is not None or hi is not None:
            out[grade] = (0.0 if lo is None else lo, hi)
    return out


def _parse_vol_grades(a1: str) -> dict:
    """解析 A~E 套数档位并换算日均 → {档位: (min, max)}。

    换算假设：'1个月'=30 天、'3个月'=90 天、'6个月'=180 天（与 X 系列下界逐一对齐，
    见本模块头部说明的验证式）。
    """
    MONTH_DAYS = {1: 30, 3: 90, 6: 180}
    out = {}
    for g, months, n1, n2, tail in _VOL_RE.findall(a1 or ""):
        days = MONTH_DAYS.get(int(months))
        if not days:
            continue          # 非 1/3/6 个月的口径不认（避免猜错）
        a = float(n1) / days
        if n2:                # 200-250 套 → 区间
            b = float(n2) / days
            out[g] = (min(a, b), max(a, b))
        elif tail == "以下":
            out[g] = (0.0, a)
        else:                 # 及以上
            out[g] = (a, None)
    return out


def _close(x, y) -> bool:
    """相对差 ≤2% 视为一致；None 与 None 一致。"""
    if x is None and y is None:
        return True
    if x is None or y is None:
        return False
    m = max(abs(x), abs(y), 1e-9)
    return abs(x - y) / m <= 0.02


# A~E 是**有序**套数档（A 最高、E 最低）。原文只给下界（"3个月销量500套及以上"），
# 上界是隐含的：等于相邻更高一档的下界。补齐后 A~E 与 XA~XE 逐档重合。
_LETTER_DESC = ["A", "B", "C", "D", "E"]


def _fill_letter_upper(ranges: dict, notes: list):
    """把 A~E 的开放上界补成「相邻更高档的下界」（A 本身保持开放）。"""
    filled = []
    for i in range(1, len(_LETTER_DESC)):
        higher, cur = _LETTER_DESC[i - 1], _LETTER_DESC[i]
        hr, cr = ranges.get(higher), ranges.get(cur)
        if hr and cr and cr[1] is None and hr[0] is not None:
            ranges[cur] = (cr[0], hr[0])
            filled.append(f"{cur}.max={hr[0]:.4g}(={higher}.min)")
    if filled:
        notes.append("A~E 开放上界按相邻高档下界补齐：" + ", ".join(filled))



# A–E 与 X* 的锚点对应关系（用于交叉校验：套数换算 vs 原文日均）
_ANCHORS = [
    ("A", 0, "XA", 0, "下界"), ("B", 0, "XB", 0, "下界"),
    ("C", 0, "XC", 0, "下界"), ("D", 0, "XD", 0, "下界"),
    ("E", 1, "XE", 1, "上界"),
]


def parse_grade_ranges(a1_text: str):
    """从 A1 说明解析档位→日均区间。

    返回 (ranges, notes)：ranges 为 {档位: (min, max)}；notes 为解析/校验过程说明（供导入接口回显）。
    解析失败或偏离内置表 >2% 时回退内置值并写入 notes，绝不静默使用可疑数字。
    """
    notes = []
    if not a1_text or "日均" not in str(a1_text):
        notes.append("A1 说明缺失或不含『日均』，全部使用内置档位表")
        return dict(GRADE_RANGES), notes

    out = dict(GRADE_RANGES)          # 以内置表为底，逐项覆盖
    xg = _parse_x_grades(a1_text)
    for g, (lo, hi) in xg.items():
        base = GRADE_RANGES.get(g)
        if base and not (_close(lo, base[0]) and _close(hi, base[1])):
            notes.append(f"档位 {g} 原文解析为 ({lo}, {hi})，与内置 ({base[0]}, {base[1]}) 不符，"
                         f"采用原文值")
        out[g] = (lo, hi)
    if not xg:
        notes.append("A1 中未解析到 X 系列档位，沿用内置值")
    else:
        notes.append(f"A1 解析到 X 系列档位 {len(xg)} 个：{sorted(xg)}")

    vg = _parse_vol_grades(a1_text)
    for g, (lo, hi) in vg.items():
        out[g] = (lo, hi)
    if vg:
        notes.append(f"A1 换算 A~E 套数档位 {len(vg)} 个：{sorted(vg)}")
        _fill_letter_upper(out, notes)

    # 交叉校验：套数换算值 vs 原文日均锚点
    for ga, ia, gx, ix, label in _ANCHORS:
        a = out.get(ga)
        x = out.get(gx)
        if not a or not x:
            continue
        va, vx = a[ia], x[ix]
        if not _close(va, vx):
            notes.append(f"⚠ 交叉校验不一致：{ga} {label} {va} vs {gx} {label} {vx}"
                         f"（保留原文值，请核对商品资料 A1）")
    return out, notes


def range_of(grade, ranges=None):
    """取档位日均区间 → (min, max)；未命中返回 (None, None) 由调用方降级。"""
    tbl = ranges or GRADE_RANGES
    if not grade:
        return None, None
    g = str(grade).strip().upper()
    r = tbl.get(g) or GRADE_RANGES.get(g)
    if not r:
        return None, None
    return r[0], r[1]


# ---------------------------------------------------------------------------
# 档位表落盘（backend/state/product_grades.json）——供「口径说明卡」展示与排障
# ---------------------------------------------------------------------------
def state_path(state_dir: Path) -> Path:
    return Path(state_dir) / "product_grades.json"


def save_grades(state_dir: Path, ranges: dict, notes=None, source: str = "") -> str:
    from datetime import datetime
    p = state_path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "notes": notes or [],
        "ranges": {k: {"min": v[0], "max": v[1]} for k, v in sorted(ranges.items())},
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


def load_grades(state_dir: Path):
    """读回落盘的档位表；不存在返回 None。"""
    p = state_path(state_dir)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {k: (v.get("min"), v.get("max"))
                for k, v in (d.get("ranges") or {}).items()}
    except Exception:
        return None
