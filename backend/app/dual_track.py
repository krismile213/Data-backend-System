"""双轨时间判定：系统轨（唯一判定依据）＋ audit 轨（交叉对照）。

移植自 流程复盘/route_check_sys.py 的双轨口径，三层各一条：
  - 节点  node_diff(nd)        = audit − sys（天，最多 1 位小数）
  - 段    seg_audit_days()     = 起点/终点**都取 audit** 的差值；任一端缺 → None
  - 货件  ship_gap(biz, au)    = audit − 业务日期

方向约定（与离线件实现一致，别照那句注释抄反）：
    正数 = audit 上报**晚于**系统业务日期。补录是常态，所以 +0.6 很常见；
    负数 = audit 比系统日期早，多半是系统日期填晚了。
    离线 md 里那句「差异 = 系统 − audit」与代码相反，以本模块为准。

🔴 铁律：audit 轨**永不参与正式判定**。判定只有一票，来自 `_seg_judge`（系统轨）。
   本模块只回答一件事：「这条判定，如果换审批时间去算，还站得住吗？」
   于是能分清两种超线——真慢（两条轨都超），还是日期填错（只有系统轨超）。

为什么不在这里自己实现判定：那会变成「同一口径两份实现」，正是这个项目反复
吃过的亏。判定函数由调用方注入（review._seg_judge），真源只有一个。
"""
from __future__ import annotations

DAY = 86400.0


def fmt_days(d):
    """天数展示：整天给 int，其余保留 1 位。None 原样返回（调用方决定显示什么）。"""
    if d is None:
        return None
    return int(d) if abs(d - round(d)) < 1e-9 else round(d, 1)


def _diff(a, b):
    """b − a。datetime 对 → 天；数字（已是天数）→ 直接相减。任一 None → None。"""
    if a is None or b is None:
        return None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(b) - float(a)
    return (b - a).total_seconds() / DAY


def node_diff(nd):
    """节点双轨差 = audit − sys。任一端缺失 → None（缺失就是不判，不拿动作顶替）。"""
    return fmt_days(_diff(nd.get("sys"), nd.get("audit")))


def seg_audit_days(nodes, n0, n1):
    """段级 audit 两端耗时。

    返回 {"from","to","days","missing":[...]}：
      days   = audit 终点 − audit 起点（天）；两端齐才给数
      missing= 缺 audit 的端点名，便于前端提示「哪一头没上报」

    ⚠️ 不做「纯日期取整」：audit 一律带时分秒，离线件同样不对 audit 取整。
       （取整规则只针对系统业务列，见 review._seg_days。）
    """
    nd0, nd1 = nodes.get(n0) or {}, nodes.get(n1) or {}
    a0, a1 = nd0.get("audit"), nd1.get("audit")
    missing = [n for n, a in ((n0, a0), (n1, a1)) if a is None]
    return {"from": a0, "to": a1,
            "days": None if missing else _diff(a0, a1),
            "missing": missing}


def seg_gap(sys_days, audit_days):
    """段级双轨差 = audit 耗时 − 系统耗时。正数 = 审批口径更久。"""
    return fmt_days(_diff(sys_days, audit_days))


def _cls(judge):
    """判定归类：ok=✓通过 / over=✗超线 / na=其它（缺时间、反向、未设目标、留空）。"""
    s = str(judge or "")
    if s.startswith("✓"):
        return "ok"
    if s.startswith("✗"):
        return "over"
    return "na"


VERDICT_TEXT = {
    "agree": "一致",
    "system_only": "仅系统轨超线",
    "audit_worse": "audit 轨更差",
    "incomplete": "缺 audit",
    "notarget": "不设目标",
    "na": "",
}


def cross(judge_sys, days_audit, target, judge_fn):
    """系统轨判定 vs audit 轨判定的一致性。

    judge_fn(days, target) 由调用方注入（= review._seg_judge），保证两条轨用**同一个**
    判定函数；本模块绝不复刻判定规则。

    返回 {"auditJudge","verdict","verdictText"}，verdict 取值见 VERDICT_TEXT。
      agree       两条轨结论相同（都 ✓ 或都 ✗，超多超少不影响归类）
      system_only 只有系统轨 ✗ → 超线可能来自系统日期填早/填晚，值得回查
      audit_worse 只有 audit 轨 ✗ → 表单日期比审批更"好看"，审批侧才是真慢
      incomplete  任一端无 audit → 不给结论
    """
    if days_audit is None:
        return {"auditJudge": None, "verdict": "incomplete",
                "verdictText": VERDICT_TEXT["incomplete"]}
    if target is None:
        return {"auditJudge": None, "verdict": "notarget",
                "verdictText": VERDICT_TEXT["notarget"]}
    if _cls(judge_sys) == "na":
        # 系统轨本身不是 ✓/✗（缺时间、反向、未设目标…）→ 没有可比的两票
        return {"auditJudge": None, "verdict": "na", "verdictText": ""}
    judge_audit = judge_fn(days_audit, target)
    a, b = _cls(judge_sys), _cls(judge_audit)
    if a == b:
        v = "agree"
    elif a == "over" and b == "ok":
        v = "system_only"
    else:
        v = "audit_worse"
    return {"auditJudge": judge_audit, "verdict": v, "verdictText": VERDICT_TEXT[v]}


def ship_gap(biz, au):
    """货件层：audit − 业务日期（发货/到货各一条）。"""
    return fmt_days(_diff(biz, au))
