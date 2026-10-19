# -*- coding: utf-8 -*-
"""
test_category_rules.py — 品类分规则的结构与口径回归测试
=========================================================
用户 2026-09-22 决策：手机膜与手机壳**各一份规则代码**（seg_rules.py /
seg_rules_shell.py），由 category_rules.py 按品类分发。本文件守住三条底线：

  ① 结构自洽：每个品类的段/L4 端点必须在自己的节点表里；部门必须在 G_ORDER 里；
  ② **手机膜零回归**：rules_for("手机膜") 必须与 seg_rules 模块逐字节一致
     （节点顺序、段、评级线、目标天），否则现有 17 页与 G506 口径会被悄悄改掉；
  ③ 手机壳口径正确：无「取数」节点、起点=提案、签样落 Part1 且带候选列链、
     首段为「提案→签样」、目标/评级线标注为待校准。

另外覆盖跨品类**不变量**（任意品类都必须满足），将来加第三个品类时自动生效。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import seg_rules, seg_rules_shell                            # noqa: E402
from app.category_rules import (CATEGORIES, DEFAULT_CATEGORY, REGISTRY,  # noqa: E402
                                categories_meta, category_of_text,
                                is_known_category, part_configs_for, rules_for)

PARTS = [f"Part{i}" for i in range(1, 10)]


# ---------------------------------------------------------------------------
# ① 注册表与分发
# ---------------------------------------------------------------------------
def test_registry_covers_all_categories():
    assert set(REGISTRY) == set(CATEGORIES)
    assert DEFAULT_CATEGORY in REGISTRY


@pytest.mark.parametrize("bad", [None, "", "  ", "不存在的品类", "手机"])
def test_unknown_category_falls_back_to_default(bad):
    """未知品类必须回退默认（手机膜），绝不能抛异常把接口打挂。"""
    assert rules_for(bad).key == DEFAULT_CATEGORY
    assert is_known_category(bad) is False


def test_category_of_text_prefers_longer_keyword():
    """「手机壳」必须先于「壳」命中，避免"手机壳"被误判成手机膜。"""
    assert category_of_text("手机壳9.7") == "手机壳"
    assert category_of_text(r"D:\x\data\手机壳\手机壳") == "手机壳"
    assert category_of_text("K010-001") == DEFAULT_CATEGORY      # 无线索 → 默认
    assert category_of_text("") == DEFAULT_CATEGORY
    assert category_of_text("手机膜9.7") == "手机膜"


def test_categories_meta_exposes_start_node_and_provisional():
    meta = {m["category"]: m for m in categories_meta()}
    assert meta["手机膜"]["startNode"] == "取数"
    assert meta["手机膜"]["targetsProvisional"] is False
    assert meta["手机壳"]["startNode"] == "提案"
    assert meta["手机壳"]["targetsProvisional"] is True


# ---------------------------------------------------------------------------
# ② 手机膜零回归（最关键：改造不能改掉手机膜任何口径）
# ---------------------------------------------------------------------------
def test_membrane_ruleset_matches_legacy_module_exactly():
    rs = rules_for("手机膜")
    assert rs.start_node == "取数"
    assert rs.segs == tuple(seg_rules.SEGS)
    assert rs.l4 == tuple(seg_rules.L4)
    assert rs.route_targets == {k: dict(v)
                                for k, v in seg_rules.ROUTE_TARGETS.items()}
    assert (rs.air_target, rs.sea_target) == (seg_rules.AIR_TARGET,
                                              seg_rules.SEA_TARGET)
    assert rs.four_countries == tuple(seg_rules.FOUR_COUNTRIES)
    assert rs.g_order == dict(seg_rules.G_ORDER)
    assert rs.node_names == list(seg_rules.NODES.keys())      # 顺序也必须一致
    # 节点三元组（Part/系统列/动作）逐项一致；系统列被规范化为候选元组
    for name, (part, sys_field, act, _owner) in seg_rules.NODES.items():
        spec = rs.nodes[name]
        assert spec["part"] == part
        assert spec["act"] == (act or "")
        expect_sys = ((str(sys_field),) if isinstance(sys_field, str) and sys_field
                      else tuple(str(x) for x in (sys_field or ()) if x))
        assert spec["sys"] == expect_sys, f"节点 {name} 系统列不一致"
    assert rs.proposal_warn_days == 1           # 提案→取数 异常预警线（2026-10-19 由 75 收紧）
    assert rs.proposal_backfilled is False      # 手机膜不做"补录"宽容


def test_membrane_seg_meta_and_targets_shape_unchanged():
    """seg_meta / default_seg_targets 与入口的旧函数输出一致（配置中心不能变）。"""
    rs = rules_for("手机膜")
    assert rs.seg_meta() == seg_rules.seg_meta()
    assert rs.default_seg_targets() == seg_rules.default_seg_targets()


def test_membrane_part_configs_come_from_config_module():
    from app.config import PART_CONFIGS
    assert rules_for("手机膜").part_configs == PART_CONFIGS


# ---------------------------------------------------------------------------
# ③ 跨品类不变量（参数化 → 将来加品类自动覆盖）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cat", CATEGORIES)
def test_seg_endpoints_exist_in_nodes(cat):
    rs = rules_for(cat)
    names = set(rs.nodes)
    for dept, name, n0, n1, _t, _o, _note, _gap in rs.segs:
        assert n0 in names, f"{cat} 段 {name} 起点 {n0} 不在节点表"
        assert n1 in names, f"{cat} 段 {name} 终点 {n1} 不在节点表"
        assert dept in rs.g_order, f"{cat} 段 {name} 部门 {dept} 未登记 G_ORDER"
    assert rs.l4[2] in names, f"{cat} L4 起点 {rs.l4[2]} 不在节点表"


@pytest.mark.parametrize("cat", CATEGORIES)
def test_flow_origin_is_unique_graph_root_and_start_reachable(cat):
    """依赖图的唯一根必须是「提案」（因果链推导的起点）。

    注意区分两个"起点"：
      · **流程起点** = 提案（图的根，两个品类一致）
      · **考核时钟起点** = ruleset.start_node（膜=取数 / 壳=提案），
        它必须是图里可达的节点，且不能有上游之外的孤立情况。
    """
    rs = rules_for(cat)
    assert "提案" in rs.nodes and rs.start_node in rs.nodes
    graph = rs.dep_graph()
    targets = {t for vs in graph.values() for t in vs}
    assert set(graph) - targets == {"提案"}, f"{cat} 依赖图根不唯一"
    assert "提案" not in targets
    if rs.start_node != "提案":
        assert rs.start_node in targets, f"{cat} 考核起点 {rs.start_node} 不可达"


@pytest.mark.parametrize("cat", CATEGORIES)
def test_route_targets_and_transport_sane(cat):
    rs = rules_for(cat)
    assert set(rs.route_targets) == {"运营", "空运", "海运"}
    for k, cfg in rs.route_targets.items():
        assert cfg["good"] > 0, f"{cat} {k} 优线必须为正"
        if cfg["limit"] is not None:
            assert cfg["limit"] >= cfg["good"], f"{cat} {k} 底线不能小于优线"
    assert rs.proposal_warn_days is None or rs.proposal_warn_days > 0
    mode, tgt = rs.transport_mode("亚马逊美国")
    assert tgt > 0 and rs.ship_class(mode) in ("空运", "海运")


@pytest.mark.parametrize("cat", CATEGORIES)
def test_part_configs_cover_nine_parts(cat):
    cfg = part_configs_for(cat)
    assert list(cfg) == PARTS, f"{cat} 必须完整覆盖 9 个 Part"
    for part, c in cfg.items():
        assert c["name"] and c["key_actions"], f"{cat} {part} 缺 name/key_actions"
        assert isinstance(c["biz_time_cols"], list)
    # 节点引用的 Part 必须都在 Part 定义里
    rs = rules_for(cat)
    for name, spec in rs.nodes.items():
        assert spec["part"] in cfg, f"{cat} 节点 {name} 的 Part {spec['part']} 未定义"


@pytest.mark.parametrize("cat", CATEGORIES)
def test_nodes_have_at_least_one_time_source(cat):
    """每个节点至少要能从系统列或审批动作取到时间，否则该节点永远为空。"""
    rs = rules_for(cat)
    for name, spec in rs.nodes.items():
        assert spec["sys"] or spec["act"], f"{cat} 节点 {name} 无任何时间来源"


# ---------------------------------------------------------------------------
# ④ 手机壳专属口径
# ---------------------------------------------------------------------------
def test_shell_has_no_data_fetch_node():
    assert "取数" not in rules_for("手机壳").nodes
    assert rules_for("手机壳").proposal_warn_days is None


def test_shell_sampling_node_uses_part1_confirm_columns():
    rs = rules_for("手机壳")
    spec = rs.nodes["签样"]
    assert spec["part"] == "Part1"                       # 膜是 Part2，壳是 Part1
    assert spec["sys"][0] == "三方确样时间"                # 首选列（实测 34/34 非空）
    assert set(spec["sys"]) == {"三方确样时间", "打样完成时间", "全部设计确认时间"}


def test_shell_first_segment_is_proposal_to_sampling():
    rs = rules_for("手机壳")
    dept, name, n0, n1, target, *_ = rs.segs[0]
    assert (n0, n1) == ("提案", "签样") and name == "提案→签样"
    assert dept == "产品" and target > 0
    assert rs.proposal_backfilled is True                # 实测 34/34 确样早于建单


def test_shell_targets_flagged_provisional():
    """目标天/评级线是继承值 → 必须带标记，避免被当成已校准口径。"""
    rs = rules_for("手机壳")
    assert rs.provisional is True
    assert rs.targets_source and "待" in rs.targets_source


def test_shell_provisional_flag_and_source_stay_consistent():
    """防「改了一半」：标记与来源文案必须同进同退。

    校准手机壳时若只把 PROVISIONAL 改成 False、忘了改 TARGETS_SOURCE（或反之），
    下游就会拿到自相矛盾的口径——一边说已校准、一边写着「待业务校准」。
    这里把两者的绑定关系钉死（校准步骤见 seg_rules_shell.py 顶部「校准清单」）。
    """
    for key in ("手机膜", "手机壳"):
        rs = rules_for(key)
        marker = "待业务校准" in (rs.targets_source or "")
        assert rs.provisional == marker, (
            f"{key}: provisional={rs.provisional} 与 targets_source={rs.targets_source!r} 不自洽")


def test_shell_calibration_checklist_points_at_every_entry():
    """校准清单必须覆盖全部可调入参，否则业务照着改会漏项。"""
    src = Path(seg_rules_shell.__file__).read_text(encoding="utf-8")
    assert "校准清单" in src
    for token in ("SEGS", "L4", "ROUTE_TARGETS", "AIR_TARGET", "SEA_TARGET",
                  "PROVISIONAL = False"):
        assert token in src, f"校准清单未提到 {token}"
    # 校验函数也必须点名，否则业务不知道改完要同步断言
    for doc in ("test_category_rules.py", "verify_shell_rules.py",
                "verify_category_live.py"):
        assert doc in src, f"校准清单未点名 {doc}"


def test_shell_part_configs_reflect_real_export():
    """列/动作按真实导出实测：Part1 只有「提交申请」、无取数字段。"""
    cfg = part_configs_for("手机壳")
    assert cfg["Part1"]["key_actions"] == ["提交申请"]
    assert "手机数据取得日期" not in cfg["Part1"]["biz_time_cols"]
    assert "全套样品测试完成时间" not in cfg["Part2"]["biz_time_cols"]
    assert "手机上架日期" not in cfg["Part8"]["biz_time_cols"]
    assert seg_rules_shell.PART_CONFIGS == cfg


# ---------------------------------------------------------------------------
# ⑤ 引擎能真的吃下手机壳规则集（离线纯函数路径，不碰 DB）
# ---------------------------------------------------------------------------
def test_engine_segment_calc_works_with_shell_ruleset():
    from datetime import datetime, timedelta

    from app.routers.review import extract_node_times

    t0 = datetime(2026, 1, 16, 8, 58)
    docs = {"Part1": [{
        "doc_id": "x", "part": "Part1", "sku": "K-1", "model": "M",
        "approval_no": "1", "status": "已结束", "create_time": t0,
        "raw": {"创建时间": t0, "三方确样时间": "2026-01-13",
                "打样完成时间": "2026-01-13"},
        "nodes": [],
    }]}
    rs = rules_for("手机壳")
    nodes = extract_node_times(docs, ruleset=rs)
    assert nodes["提案"]["adopted"] == t0
    assert nodes["签样"]["adopted"] == datetime(2026, 1, 13)
    assert nodes["签样"]["sys_field"] == "三方确样时间"     # 命中的候选列可追溯
    assert "取数" not in nodes

    # 首选列缺失时回退到候选链的下一列
    docs["Part1"][0]["raw"]["三方确样时间"] = ""
    nodes2 = extract_node_times(docs, ruleset=rs)
    assert nodes2["签样"]["sys_field"] == "打样完成时间"
    assert nodes2["签样"]["adopted"] == datetime(2026, 1, 13)

    # 手机膜口径下同一份数据取不到「签样」（列名不同）→ 证明分发确实生效
    mem = extract_node_times(docs, ruleset=rules_for("手机膜"))
    assert mem["签样"]["adopted"] is None
    assert mem["签样"]["part"] == "Part2"
    assert "取数" in mem

    # 首个 Part1 段应为「—补录」而非「⚠反向」
    from app.routers.review import _seg_judge
    assert _seg_judge(-3.0, 87, "提案→签样", backfill_ok=True).startswith("—补录")
    assert _seg_judge(-3.0, 87, "提案→签样", backfill_ok=False).startswith("⚠反向")
    assert _seg_judge(5.0, 7) == "✓"


def test_assessment_start_node_is_category_aware():
    """损益起点必须跟着品类走（膜=取数 / 壳=提案），否则手机壳永远算不出。"""
    import inspect

    from app.assessment import compute
    sig = inspect.signature(compute)
    assert sig.parameters["start_node"].default == "取数"
    assert "start_node" in sig.parameters

    from datetime import datetime, timedelta
    rules = {"loss_route_limit": 60,
             "loss_daily_default": {"min": 1.38, "max": 5.5}}
    t0 = datetime(2026, 1, 16)
    items = [{"cls": "空运", "arr": t0 + timedelta(days=50)}]
    nodes = {"提案": {"adopted": t0}, "取数": {"adopted": None}}
    # 手机壳口径：起点=提案 → 50 天 < 60 底线 → 提前完成（增益）
    shell = compute(sku="K", nodes=nodes, items=items, product=None,
                    rules=rules, start_node="提案")
    assert shell["startNode"] == "提案" and shell["delayDays"] == -10.0
    assert shell["direction"] == "gain"
    assert "评级未命中" in shell["degraded"]        # 手机壳不参与评级 → 口径正确
    # 手机膜口径：起点=取数（本例缺）→ 无法评估
    assert compute(sku="G", nodes=nodes, items=items, product=None,
                   rules=rules, start_node="取数") is None


# ---------------------------------------------------------------------------
# ⑥ 提案锚点：钉钉 Part1「提案日期」优先，未填回退「创建时间」（2026-10-19）
# ---------------------------------------------------------------------------
def test_membrane_proposal_anchor_prefers_proposal_date():
    """新字段接入 + 目标 1 天：提案日 10-05 → 取数 10-07 应判「超1天」而非反向。

    背景：审批单常在业务提案后几天才提交，只用「创建时间」会把
    提案→取数 算成负数（G602/G603 实测 -3 天），在复盘里误标 ⚠反向。
    """
    from datetime import datetime

    from app.routers.review import _seg_judge, extract_node_times

    def _doc(raw):
        return {"doc_id": "x", "part": "Part1", "sku": "G602", "model": "M",
                "approval_no": "1", "status": "已结束",
                "create_time": datetime(2026, 10, 10, 14, 36),
                "raw": raw, "nodes": []}

    rs = rules_for("手机膜")
    raw = {"创建时间": "2026-10-10 14:36:57", "提案日期": "2026-10-05",
           "手机数据取得日期": "2026-10-07"}

    nd = extract_node_times({"Part1": [_doc(dict(raw))]}, ruleset=rs)
    assert nd["提案"]["sys_field"] == "提案日期"          # 命中候选链第 1 列
    assert nd["提案"]["adopted"] == datetime(2026, 10, 5)
    assert nd["取数"]["adopted"] == datetime(2026, 10, 7)
    # 2 天 > 目标 1 天
    assert _seg_judge(2.0, rs.proposal_warn_days, "提案→取数") == "✗超1天"

    # 老单据没有「提案日期」→ 回退创建时间，不掉时刻（也不会漏掉这一段）
    raw.pop("提案日期")
    nd2 = extract_node_times({"Part1": [_doc(dict(raw))]}, ruleset=rs)
    assert nd2["提案"]["sys_field"] == "创建时间"
    assert nd2["提案"]["adopted"] == datetime(2026, 10, 10, 14, 36, 57)
    # 这正是改前的负值场景：取数(10-07) 早于建单(10-10)
    assert nd2["取数"]["adopted"] < nd2["提案"]["adopted"]
