# -*- coding: utf-8 -*-
"""sample_cycle.py — 样机流转（签样→拍摄交付）的**唯一**计算与展示入口。

背景（2026-11-06 业务口径）
---------------------------
产品部开发阶段真正要考核的是「签样后多久真的拿到素材」，而不是把
`签样→寄送(5天)` + `寄送→拍摄交付(10天)` 两段拆开各判各的 —— 拆开看会被
中间的寄送日期带偏，而且寄送日期常年**没有系统列**：

  · Part6 表单只有「拍摄团队素材交付日期」，**没有「寄送拍摄团队日期」**；
  · 于是 adopted 只能退回 audit（钉钉补录上报时间），补录一拖就倒挂：
    G600 实测 寄送 audit 2026-11-05 17:11、拍摄交付系统列 2026-10-22
    → 「寄送→拍摄交付」= **-14 天**，看着像流程倒挂，其实是数据质量。

所以本模块的间隔**只认系统业务日期**（nodes[*]["sys"]），任一端缺失就
`days=None`（前端/日报写空），**绝不拿 audit 顶替实际**。

真源唯一
--------
合格线 = `seg_rules.SAMPLE_CYCLE["limit"]`（15 = 5 + 10）。
`tests/test_review_sample_cycle.py` 对**两个品类**都断言「两段目标之和 == 合格线」，
谁改了 SEGS 目标天却没复核这条线，测试立刻红（不再拿实现自证）。

消费方
------
  · `routers/review.py` —— SKU 档案「样机流转」块 + 节点核对表；
  · `reporting.daily_report` —— 事中日报总表 4 列（签样/拍摄交付/间隔/判定）。
两处走**同一个** `compute()`，不允许各自再算一遍。
"""

from __future__ import annotations

from datetime import date, datetime

from .seg_rules import SAMPLE_CYCLE

__all__ = ["compute", "label", "limit"]


def limit() -> int:
    """合格线（天）。单独暴露，避免调用方各自写死 15。"""
    return int(SAMPLE_CYCLE["limit"])


def _fmt(v):
    """datetime/date/字符串 → 'YYYY-MM-DD'（系统业务日期只到天就够，不显示时间）。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return None
    return s[:10] if len(s) >= 10 else s


def compute(nodes, limit=None):
    """签样 → 拍摄交付 的实际间隔（**只认系统业务日期**）。

    nodes: {节点名: {"sys": datetime|None, ...}} —— 即 `extract_node_times()` 的产物；
           也接受已格式化的字符串（复查缓存里存的是 'YYYY-MM-DD HH:MM'）。

    返回 {from, to, limit, start, end, days, ok, missing, recordedMissing}
      days = 自然日差（end - start），任一缺失 → None
      ok   = days < limit（**严格小于**：正好 15 天算超线）
      missing = 缺系统业务日期的节点名（用于前端提示「待补」而不是「待催」）
      recordedMissing = missing 中**已有 audit/审批动作**的节点 —— 单据建了却
        没填系统列（补录缺失），与「流程还没走到这一步」是两件事：
        前者该去催填表，后者只是还没发生。日报/复盘靠它区分「待补 / 未交付」。
    """
    cfg = SAMPLE_CYCLE
    lim = cfg["limit"] if limit is None else limit
    a = (nodes.get(cfg["from"]) or {}).get("sys")
    b = (nodes.get(cfg["to"]) or {}).get("sys")
    a_dt, b_dt = _as_date(a), _as_date(b)
    missing = [n for n, v in ((cfg["from"], a_dt), (cfg["to"], b_dt))
               if v is None]
    recorded = [n for n in missing
                if _has_record(nodes.get(n) or {})]
    out = {"from": cfg["from"], "to": cfg["to"], "limit": lim,
           "start": _fmt(a), "end": _fmt(b),
           "days": None, "ok": None,
           "missing": missing, "recordedMissing": recorded}
    if a_dt is not None and b_dt is not None:
        out["days"] = int((b_dt - a_dt).days)
        out["ok"] = out["days"] < lim
    return out


def _has_record(node):
    """该节点是否已有 audit（补录上报）或审批动作 —— 即「单据已经走到这里」。"""
    for k in ("audit", "action", "adopted"):
        v = node.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s and s.lower() not in ("nan", "nat", "none"):
            return True
    return False


def _as_date(v):
    """任意时间表示 → date（用于算自然日差）；不可解析返回 None。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:len(fmt) + 2].strip(), fmt).date()
        except ValueError:
            continue
    try:                                    # 兜底：前缀 10 位日期
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def label(cyc):
    """把 compute() 的结果转成可展示文本（前端/日报共用同一套措辞）。

    返回 {"days": "10天" | "—", "judge": 判定词, "tone": "ok" | "bad" | "muted",
          "note": 人话解释}

    缺日期时**分两种**，别混成一个「待补」：
      · recordedMissing 非空 → 「待补」：单据已建、只是系统列没填（该催填表）；
      · 否则 → 「未交付 / 未签样」：流程还没走到那一步，不是数据问题。
    这样日报不会把 15 条「还没拍照」全打成数据异常。
    """
    if not cyc:
        return {"days": "—", "judge": "—", "tone": "muted", "note": ""}
    lim = cyc.get("limit") or limit()
    days = cyc.get("days")
    if days is None:
        miss = cyc.get("missing") or []
        rec = cyc.get("recordedMissing") or []
        if rec:
            return {"days": "—", "judge": "待补", "tone": "bad",
                    "note": f"{'、'.join(rec)} 已建单但系统业务日期为空"
                            "（补录时间不能替代实际，需补填）"}
        if cyc.get("from") in miss:
            return {"days": "—", "judge": "未签样", "tone": "muted",
                    "note": "签样未完成，尚不能起算"}
        return {"days": "—", "judge": "未交付", "tone": "muted",
                "note": f"{'、'.join(miss)} 还没到（无系统业务日期）"}
    if cyc.get("ok"):
        return {"days": f"{days}天", "judge": "✓合格", "tone": "ok",
                "note": f"{days} < {lim} 天"}
    # 正好 15 天（excess=0）也是超线 —— 用户口径是「＜15 天合格」，等于不算合格。
    # 此时不写「超线0天」（读起来像没超），直接写「✗超线」。
    excess = days - lim
    judge = "✗超线" if excess <= 0 else f"✗超线{excess}天"
    return {"days": f"{days}天", "judge": judge, "tone": "bad",
            "note": f"{days} ≥ {lim} 天"}
