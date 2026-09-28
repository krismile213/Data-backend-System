# -*- coding: utf-8 -*-
"""
causal.py — 核心原因因果链定位（P0-5）
========================================
目标：**不满足于"列出最差段"**。一个段耗时长，可能只是表象；真正要回答的是
「上一个节点做完之后，下游到底卡在谁手上、卡了多久、是不是根本没人上报」。

判据（对每个超期段：days > max(target, MIN_DAYS)）：

    上游完成 up_done   = 起点节点 adopted 时间
    下游完成 down_done = 终点节点 adopted 时间（None = 至今未完成）
    下游启动 down_start = min(down_done, 下游 Part 的最早单据 create_time)
                          —— 单据已建但一直没干完时，用建单时间更贴近"启动"
    wait = down_start − up_done                    # 段内真实等待天数

窗口内是否有下游单据（create_time ∈ (up_done, down_done)）：
    无 → type="漏报嫌疑"  人根本没启动/没上报，上游做完就一直空着（G506 即此类）
    有 → type="搁置"      活接了但拖着，责任在下游执行人
    终点在 Part5/Part8 或段名含"运输" → type="运输"

取 wait 最大者为 rootCause，并给出从「提案」到根因终点的**步进式因果链**。

G506 期望：seg=签样→采购、waitDays≈170、suspect=True，
evidence 含「签样 2025-11-11 完成 → Part2 单据 2026-04-30 才创建」与两轮 Part1 间隔说明。
"""

from .seg_rules import NODES, SEGS

# 段耗时低于该值不参与根因排序（避免 1~6 天的正常抖动段夺魁）
MIN_DAYS = 7.0

# 节点 → 所属 Part（下游单据搜索范围）；默认取手机膜口径，
# 手机壳走 ruleset 传入（节点链不同：无「取数」、签样落 Part1）
NODE_PART = {name: spec[0] for name, spec in NODES.items()}

# 运输类特征（终点落在这两个 Part 或段名带"运输" → 归运输）
_TRANSPORT_PARTS = ("Part5", "Part8")


def _fmt(t):
    if t is None:
        return None
    if t.hour == 0 and t.minute == 0:
        return t.strftime("%Y-%m-%d")
    return t.strftime("%Y-%m-%d %H:%M")


def _node_part_map(ruleset=None):
    """节点→Part 映射；ruleset 为空时用手机膜默认（行为与改造前一致）。"""
    if ruleset is None:
        return NODE_PART
    return {name: spec["part"] for name, spec in ruleset.nodes.items()}


def _segs_of(ruleset=None):
    return list(ruleset.segs) if ruleset is not None else list(SEGS)


def _dep_graph(ruleset=None):
    """SEGS 的段关系 → 有向邻接表（提案→取数→签样→采购 / 签样→寄送→…）。"""
    adj = {}
    for _dept, _name, n0, n1, *_rest in _segs_of(ruleset):
        adj.setdefault(n0, [])
        if n1 not in adj[n0]:
            adj[n0].append(n1)
    return adj


def _chain_to(target, nodes, ruleset=None, origin="提案"):
    """在段依赖图上找「origin → … → target」路径，输出步进链 [{node, at, wait}]。

    origin 固定为「提案」：两个品类的依赖图都以提案为唯一根
    （手机膜 提案→取数→…；手机壳 提案→签样→…），这样链能完整覆盖全程。
    """
    if not target:
        return []
    adj = _dep_graph(ruleset)
    start = origin
    prev = {start: None}
    queue = [start]
    found = False
    while queue:
        cur = queue.pop(0)
        if cur == target:
            found = True
            break
        for nxt in adj.get(cur, []):
            if nxt not in prev:
                prev[nxt] = cur
                queue.append(nxt)
    if not found:
        return []
    path, cur = [], target
    while cur is not None:
        path.append(cur)
        cur = prev.get(cur)
    path.reverse()

    out, last = [], None
    for name in path:
        t = (nodes.get(name) or {}).get("adopted")
        wait = None
        if t is not None and last is not None:
            wait = float((t.date() - last.date()).days)   # 与段口径一致：按日历天
        out.append({"node": name, "at": _fmt(t), "wait": wait})
        if t is not None:
            last = t
    return out


def locate(seg_rows, nodes, parts_docs, multi_round=None, min_days=MIN_DAYS,
           ruleset=None):
    """定位核心原因。

    seg_rows   : review_sku_segments 产出的段级明细（含 t0/t1/days/target/dept/seg）
    nodes      : {节点名: {adopted, ...}}
    parts_docs : {Part: [去重后单据]} —— 用于取"下游 Part 最早建单时间"
    multi_round: resolve_part1_rounds 产出的多轮提交信息（可选，用于补充 evidence）
    ruleset    : 品类规则集（category_rules.RuleSet）；None = 手机膜默认

    返回 rootCause dict；无候选段时返回 None。
    """
    node_part = _node_part_map(ruleset)
    cands = []
    for r in seg_rows:
        if r.get("gap") or r.get("noShip") or r.get("days") is None:
            continue
        t0 = r.get("t0")
        if t0 is None:
            continue
        days = float(r["days"])
        target = float(r.get("target") or 0)
        if days <= max(target, min_days):
            continue                      # 未超期也不够长，不进根因候选

        from_node, to_node = r.get("from"), r.get("to")
        down_part = node_part.get(to_node)
        # 只有"仍在进行"的下游单据才算这次停滞的启动信号；已结束的单据属历史，
        # 采信它的 create_time 会把 wait 压到 0（G506 的 Part2 单 2025-11-11 就已建、
        # 2026-04-30 才完成，用建单时间会把 170 天停滞算成 0 天，根因就丢了）。
        cts = [d.get("create_time") for d in (parts_docs.get(down_part) or [])
               if d.get("create_time") is not None
               and d.get("status") != "已结束"]
        t1 = r.get("t1")
        pool = ([t1] if t1 is not None else []) + cts
        down_start = min(pool) if pool else None
        wait = ((down_start - t0).total_seconds() / 86400.0
                if down_start is not None else days)

        # 窗口内是否已有下游动作（严格开区间：端点即"这次完成"本身）
        mid = [t for t in cts if t1 is not None and t0 < t < t1]

        seg_name = str(r.get("seg") or "")
        # 终点落在 Part5/Part8（含「发货末批(Part5)」这类拼接名）或段名带"运输" → 归运输
        if ("运输" in seg_name
                or any(tp in str(to_node) for tp in _TRANSPORT_PARTS)
                or down_part in _TRANSPORT_PARTS):
            ctype, suspect = "运输", False
        elif not mid and wait > min_days:
            ctype, suspect = "漏报嫌疑", True
        else:
            ctype, suspect = "搁置", False

        if mid:
            evidence = (f"{from_node} {_fmt(t0)} 完成 → {down_part} 单据 "
                        f"{_fmt(min(mid))} 已创建，但仍拖到 {_fmt(t1)} 才完成，"
                        f"间隔 {wait:.0f} 天")
        elif down_start is not None:
            evidence = (f"{from_node} {_fmt(t0)} 完成 → {down_part or to_node} "
                        f"下一动作 {_fmt(down_start)} 才出现，间隔 {wait:.0f} 天"
                        f"（期间无任何单据）")
        else:
            evidence = (f"{from_node} {_fmt(t0)} 完成 → {to_node} 至今未完成，"
                        f"已等待 {wait:.0f} 天")

        cands.append({
            "type": ctype, "seg": r.get("seg"), "dept": r.get("dept"),
            "owner": r.get("executor") or r.get("owner") or "",
            "waitDays": round(wait, 1), "segDays": round(days, 1),
            "suspect": suspect, "evidence": evidence,
            "from": from_node, "to": to_node,
            "target": r.get("target"),
        })

    if not cands:
        return None

    # 排序：≥30 天的「漏报嫌疑」（人根本没启动/没上报）优先于「搁置」——
    # G506 实测：签样→采购 170 天无人上报（漏报）应压过 寄送→拍摄交付 181 天（搁置），
    # 前者是更上游的"该动的没人动"，后者只是同一停滞期在下游的表现。
    cands.sort(key=lambda c: (0 if (c["suspect"] and c["waitDays"] >= 30) else 1,
                              -c["waitDays"], -c["segDays"]))
    top = dict(cands[0])

    # 多轮提交（人工漏报）补充到 evidence —— G506 类：新一轮提案未上报取数
    if top["suspect"] and multi_round and multi_round.get("flag"):
        gap = multi_round.get("gapDays")
        if gap is not None:
            top["evidence"] += (
                f"；另：Part1 存在多轮提交（间隔 {gap:.0f} 天）——"
                f"取数完成后无人上报后续流程，属人工漏报而非系统缺失")
        else:
            top["evidence"] += "；另：Part1 存在多轮提交，属人工漏报而非系统缺失"

    top["chain"] = _chain_to(top.get("to"), nodes, ruleset=ruleset)
    # 同停滞期的其它超期段（如 G506 的「签样→采购 170 天」与主因同起于 2025-11-11）——
    # 设计原则是"不能只列最差段"，这些一并给出，避免漏看并行分支上的堵塞。
    top["related"] = [{
        "seg": c["seg"], "dept": c["dept"], "type": c["type"],
        "waitDays": c["waitDays"], "segDays": c["segDays"],
        "suspect": c["suspect"],
    } for c in cands[1:6]]
    return top
