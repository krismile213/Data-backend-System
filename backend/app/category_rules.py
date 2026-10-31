# -*- coding: utf-8 -*-
"""
category_rules.py — 品类 → 规则集 的分发层（单开一份代码区分品类）
====================================================================
背景（用户 2026-09-22 决策）：手机膜与手机壳虽同根同源，但走的是**两套钉钉流程模板**
（processCode 不同、列不同、节点链不同）。若硬塞进同一份 NODES/SEGS，任一方后续加节点
都会互相污染 —— 所以**按品类分文件**，本模块只做「按品类取哪一份」的路由。

    Ruleset（手机膜）  ← backend/app/seg_rules.py        （起点=取数）
    Ruleset（手机壳）  ← backend/app/seg_rules_shell.py  （起点=提案，无取数节点）

用法：
    from ..category_rules import rules_for, DEFAULT_CATEGORY, category_of_batch
    rs = rules_for("手机壳")
    rs.nodes / rs.segs / rs.l4 / rs.route_targets / rs.start_node / rs.product_end_node ...

约定：
  · `seg_rules.py` **保持零改动**（15 处现存导入全部不受影响），本模块只读取它；
  · 引擎（review / assessment / causal / reporting）新增的 `ruleset=` 参数默认取
    DEFAULT_CATEGORY，因此**手机膜在线行为与改造前逐字节一致**；
  · 手机壳的目标天/评级线为继承名义值 → `provisional=True`，对外必须带出该标记。
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from . import seg_rules as _membrane
from . import seg_rules_shell as _shell
from .config import part_configs_for as _part_configs_for


def _membrane_part_configs() -> Dict:
    """手机膜的 Part 环节定义（真源在 config.py，与 parser/analysis 共用）。"""
    return _part_configs_for("手机膜")

# 品类清单（顺序即前端展示顺序；第一个为默认）
CATEGORIES = ("手机膜", "手机壳")
DEFAULT_CATEGORY = "手机膜"

# 品类识别的别名（批次名 / 数据目录名可能只写简称）
_ALIASES = {
    "手机膜": ("手机膜", "钢化膜", "膜"),
    "手机壳": ("手机壳", "壳"),
}


@dataclass(frozen=True)
class RuleSet:
    """一个品类的完整规则集（不可变；节点表已规范化为 dict，含 sys 候选列链）。"""

    key: str                       # 品类名
    label: str                     # 展示名
    start_node: str                # 考核时钟起点节点（手机膜=取数 / 手机壳=提案）
    product_end_node: str          # 产品部终节点（= 素材交付 := 节点「素材加工」）
    nodes: Dict[str, Dict[str, Any]]
    segs: Tuple[Tuple, ...]        # (部门, 环节, 起点, 终点, 目标天, 责任人, 说明, 留空段)
    l4: Tuple
    route_targets: Dict[str, Dict]
    air_target: int
    sea_target: int
    four_countries: Tuple[Tuple[str, str], ...]
    g_order: Dict[str, int]
    provisional: bool              # 目标天/评级线是否待业务校准
    targets_source: str
    part_configs: Dict[str, Dict]
    transport_mode: Any            # callable(hub) -> (方式名, 目标天)
    ship_class: Any                # callable(方式名) -> "空运"|"海运"
    proposal_backfilled: bool = False   # 起点节点是否"完成后补录"（负值属记录约定）

    # ---- 派生属性 ----
    @property
    def proposal_warn_days(self) -> Optional[int]:
        """「提案→取数」异常预警线（手机壳无此段 → None，即该项不适用）。"""
        for _dept, name, _n0, _n1, target, *_rest in self.segs:
            if name == "提案→取数":
                return int(target)
        return None

    @property
    def node_names(self) -> List[str]:
        return list(self.nodes.keys())

    def node(self, name: str) -> Dict[str, Any]:
        return self.nodes[name]

    def node_part(self, name: str) -> Optional[str]:
        spec = self.nodes.get(name)
        return spec["part"] if spec else None

    def sys_candidates(self, name: str) -> Tuple[str, ...]:
        """节点可用的系统业务时间列（候选链，按序取第一个非空）。"""
        spec = self.nodes.get(name)
        return spec["sys"] if spec else ()

    def dep_graph(self) -> Dict[str, List[str]]:
        """段关系 → 有向邻接表（因果链推导用）。"""
        adj: Dict[str, List[str]] = {}
        for _dept, _name, n0, n1, *_rest in self.segs:
            adj.setdefault(n0, [])
            if n1 not in adj[n0]:
                adj[n0].append(n1)
        return adj

    def seg_meta(self) -> List[Dict]:
        """段定义元数据（配置中心/前端展示用），保持 SEGS 顺序 + L4。"""
        out = []
        for dept, name, n0, n1, target, owner, note, is_gap in self.segs:
            out.append({
                "name": name, "dept": dept, "from": n0, "to": n1,
                "fromPart": self.node_part(n0), "toPart": self.node_part(n1),
                "defaultTarget": target, "owner": owner,
                "note": note, "gap": is_gap,
            })
        dept, name, n0, target, owner, note = self.l4
        out.append({
            "name": name, "dept": dept, "from": n0, "to": "发货末批(Part5)",
            "fromPart": self.node_part(n0), "toPart": "Part5",
            "defaultTarget": target, "owner": owner, "note": note, "gap": False,
        })
        return out

    def default_seg_targets(self) -> Dict[str, Dict]:
        """seg_targets 默认配置：{环节名: {target, available}}（留空段不进配置）。"""
        out = {}
        for dept, name, _n0, _n1, target, _owner, _note, is_gap in self.segs:
            if is_gap or dept == "__GAP__":
                continue
            out[name] = {"target": target, "available": True}
        out[self.l4[1]] = {"target": self.l4[3], "available": True}
        return out

    def describe(self) -> Dict[str, Any]:
        """对外暴露的口径元信息（接口/报告里带出，避免"静默用了哪套口径"）。"""
        return {
            "category": self.key,
            "categoryLabel": self.label,
            "startNode": self.start_node,
            "productEndNode": self.product_end_node,
            "productEndLabel": "素材交付",
            "proposalWarnDays": self.proposal_warn_days,
            "routeTargets": dict(self.route_targets),
            "targetsProvisional": self.provisional,
            "targetsSource": self.targets_source,
            "proposalBackfilled": self.proposal_backfilled,
        }


def _normalize_nodes(raw: Dict) -> Dict[str, Dict[str, Any]]:
    """(Part, 系统列[或候选元组], 动作, 责任人) → dict；系统列统一成元组候选链。"""
    out = {}
    for name, spec in raw.items():
        part, sys_field, act, owner = spec
        if isinstance(sys_field, (tuple, list)):
            cands = tuple(str(x) for x in sys_field if x)
        else:
            cands = (str(sys_field),) if sys_field else ()
        out[name] = {"part": part, "sys": cands, "act": act or "", "owner": owner or ""}
    return out


def _build(key: str, mod, *, label: str, start_node: str,
           provisional: bool, targets_source: str,
           part_configs: Optional[Dict] = None,
           proposal_backfilled: bool = False) -> RuleSet:
    if part_configs is None:
        part_configs = getattr(mod, "PART_CONFIGS", None)
    return RuleSet(
        key=key,
        label=label,
        start_node=start_node,
        product_end_node=getattr(mod, "PRODUCT_END_NODE", "素材加工"),
        nodes=_normalize_nodes(mod.NODES),
        segs=tuple(mod.SEGS),
        l4=tuple(mod.L4),
        route_targets={k: dict(v) for k, v in mod.ROUTE_TARGETS.items()},
        air_target=int(mod.AIR_TARGET),
        sea_target=int(mod.SEA_TARGET),
        four_countries=tuple(mod.FOUR_COUNTRIES),
        g_order=dict(mod.G_ORDER),
        provisional=provisional,
        targets_source=targets_source,
        part_configs=dict(part_configs),
        transport_mode=mod.transport_mode,
        ship_class=mod.ship_class,
        proposal_backfilled=proposal_backfilled,
    )


# 手机膜的 Part 环节定义在 config.py（与 parser/analysis 共用一份），此处显式取用
_MEMBRANE = _build(
    "手机膜", _membrane,
    label="手机膜（考核起点=取数）",
    start_node="取数",
    provisional=False,
    targets_source="业务已确认（时效确认规则表）",
    part_configs=_membrane_part_configs(),
)

_SHELL = _build(
    _shell.CATEGORY, _shell,
    label=_shell.LABEL,
    start_node=_shell.START_NODE,
    provisional=_shell.PROVISIONAL,
    targets_source=_shell.TARGETS_SOURCE,
    proposal_backfilled=_shell.PROPOSAL_BACKFILLED,
)

REGISTRY: Dict[str, RuleSet] = {
    "手机膜": _MEMBRANE,
    "手机壳": _SHELL,
}


def rules_for(category: Optional[str] = None) -> RuleSet:
    """取品类的规则集；None/未知品类 → 默认品类（手机膜），不抛异常。"""
    return REGISTRY.get(str(category or "").strip(), REGISTRY[DEFAULT_CATEGORY])


def is_known_category(category: Optional[str]) -> bool:
    return str(category or "").strip() in REGISTRY


def category_of_text(*texts: Optional[str]) -> str:
    """按名称/路径文本推断品类（批次名、数据目录路径）。

    先按「长关键词优先」匹配（"手机壳" 优先于 "壳"），命中即返回；
    全部不命中 → DEFAULT_CATEGORY（保持既有行为不变）。
    """
    blob = " ".join(str(t) for t in texts if t)
    if not blob:
        return DEFAULT_CATEGORY
    hits = []
    for cat, words in _ALIASES.items():
        for w in words:
            if w in blob:
                hits.append((len(w), cat))
    if not hits:
        return DEFAULT_CATEGORY
    hits.sort(reverse=True)
    return hits[0][1]


def part_configs_for(category: Optional[str] = None) -> Dict[str, Dict]:
    """品类的 Part 环节定义（列名/关键动作/业务时间列）。"""
    return rules_for(category).part_configs


def categories_meta() -> List[Dict[str, Any]]:
    """全部品类的口径元信息（前端品类切换/排查口径用）。"""
    return [REGISTRY[c].describe() for c in CATEGORIES]
