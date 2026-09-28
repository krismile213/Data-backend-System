# -*- coding: utf-8 -*-
"""
test_seg_boundary.py — T09 产品部终节点口径（拍摄交付 → 素材交付）边界回归
==========================================================================
用户 2026-09-24 口径：**产品部终节点 = 素材交付**（= Part6 审批动作
`产品营销素材加工` 的时间，audit 优先；**不是** `拍摄团队素材交付日期`）。
运营段起点同步 = 素材交付。

本文件守住「单一真源 → 多消费方」这条链上最容易各写一遍、各改一半的地方：

  ① 单一真源：两个品类模块都有 `PRODUCT_END_NODE == "素材加工"`，且该 key 在各自
     `NODES` 里存在、动作 = `产品营销素材加工`、**系统列候选为空**（防止有人把它
     接到 `拍摄团队素材交付日期` 上）；
  ② 分发层：RuleSet 暴露 `product_end_node`，`describe()` 带 `productEndLabel=素材交付`；
     未知品类回退默认；
  ③ 预警消费方：`alerts._alert_specs` 的 `deliver` 取的是「素材加工」节点；
  ④ 复盘消费方：`review._dept_final` 产品部取「素材加工」那一刻（不是本地仓到货）；
  ⑤ 跨实现对齐：离线 `route_check_sys.build_overview` 的产品段/运营段天数与后端
     alert 口径公式算出的天数相等（离线脚本依赖 pandas/openpyxl，若本机隔离解释器
     缺依赖则退化为「源码静态断言」，见 `test_cross_impl_days_match`）。

不变量（本文件不改、只校验）：目标天 23/38 与 7 不变、SEGS 一行不改、节点名不变。
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]                       # …/数据后台系统
REPO = ROOT.parent                                               # …/work-main
sys.path.insert(0, str(ROOT / "backend"))

from app import seg_rules, seg_rules_shell                        # noqa: E402
from app.category_rules import DEFAULT_CATEGORY, rules_for        # noqa: E402

PRODUCT_END = "素材加工"
PRODUCT_END_ACTION = "产品营销素材加工"
PRODUCT_END_LABEL = "素材交付"
OFFLINE_SCRIPT = REPO / "流程复盘" / "route_check_sys.py"


# ---------------------------------------------------------------------------
# ① 单一真源：PRODUCT_END_NODE 必须指向「素材加工」，且节点本身无系统时间列
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mod", [seg_rules, seg_rules_shell], ids=["手机膜", "手机壳"])
def test_product_end_node_constant_points_to_material_processing(mod):
    assert mod.PRODUCT_END_NODE == PRODUCT_END, (
        f"{mod.__name__}: PRODUCT_END_NODE 应为 {PRODUCT_END}（素材交付）")
    assert PRODUCT_END in mod.NODES, f"{mod.__name__} 的 NODES 缺 {PRODUCT_END} 节点"

    part, sys_field, act, _owner = mod.NODES[PRODUCT_END]
    assert act == PRODUCT_END_ACTION, (
        f"{mod.__name__}: 素材交付动作应为「{PRODUCT_END_ACTION}」，实为「{act}」")
    # ⚠️ 关键防线：该节点**没有**系统业务时间列（按 audit > 动作取值）。
    #    若有人把它接到 `拍摄团队素材交付日期` 上，这条会红。
    assert sys_field in ("", (), None), (
        f"{mod.__name__}: 素材交付不应有系统时间列，实为 {sys_field!r} —— "
        f"确认没有误接到『拍摄团队素材交付日期』")


# ---------------------------------------------------------------------------
# ② 分发层：RuleSet.product_end_node / describe()
# ---------------------------------------------------------------------------
def test_membrane_product_end_node_and_label():
    rs = rules_for("手机膜")
    assert rs.product_end_node == PRODUCT_END
    desc = rs.describe()
    assert desc["productEndNode"] == PRODUCT_END
    assert desc["productEndLabel"] == PRODUCT_END_LABEL


def test_shell_product_end_node_and_label():
    rs = rules_for("手机壳")
    assert rs.product_end_node == PRODUCT_END
    assert rs.describe()["productEndLabel"] == PRODUCT_END_LABEL


@pytest.mark.parametrize("bad", [None, "", "不存在", "手机"])
def test_unknown_category_falls_back_default_end_node(bad):
    """未知品类回退默认（手机膜）：终节点同样是「素材加工」。"""
    assert rules_for(bad).product_end_node == PRODUCT_END
    assert rules_for(bad).key == DEFAULT_CATEGORY


# ---------------------------------------------------------------------------
# ③ 预警消费方：_alert_specs 的 deliver 取「素材加工」
# ---------------------------------------------------------------------------
def test_alert_specs_deliver_reads_material_processing():
    from app.routers import alerts

    spec = alerts._alert_specs(rules_for("手机膜"))["deliver"]
    part, act, sys_cands = spec
    assert act == PRODUCT_END_ACTION, "deliver 的动作名必须是「产品营销素材加工」"
    assert sys_cands == (), "deliver 的系统列候选必须为空（无系统时间列）"
    assert part == "Part6"

    # 手机壳同构：deliver 也指向「素材加工」
    spec_shell = alerts._alert_specs(rules_for("手机壳"))["deliver"]
    assert spec_shell[1] == PRODUCT_END_ACTION and spec_shell[2] == ()


def test_alert_nodes_constant_deliver_is_none():
    """_ALERT_NODES.deliver 必须是 None（由 rs.product_end_node 运行时解析），
    不能硬编码回「拍摄交付」。"""
    from app.routers import alerts

    assert alerts._ALERT_NODES["deliver"] is None
    assert alerts._ALERT_NODES["fetch"] is None


# ---------------------------------------------------------------------------
# ④ 复盘消费方：_dept_final 产品部取「素材加工」而非「本地仓到货」
# ---------------------------------------------------------------------------
def test_dept_final_product_uses_material_processing():
    from app.routers.review import _dept_final

    t_prod = datetime(2025, 7, 22, 15, 57, 30)      # 素材加工（audit）
    t_local = datetime(2025, 7, 20, 9, 0, 0)        # 本地仓到货（更早，仍存在）
    nodes = {
        PRODUCT_END: {"adopted": t_prod, "src": "audit"},
        "本地仓到货": {"adopted": t_local, "src": "系统"},
        "上架": {"adopted": None, "src": ""},
        "广告": {"adopted": None, "src": ""},
    }
    result = _dept_final(nodes, {}, ruleset=rules_for("手机膜"))
    # 两个节点都有值时，产品部必须取「素材加工」那一刻（不是本地仓到货）
    assert result["产品部"][0] == t_prod
    assert result["产品部"][0] != t_local


def test_dept_final_product_defaults_to_default_category():
    """ruleset=None 时按默认品类（手机膜）解析，仍取「素材加工」。"""
    from app.routers.review import _dept_final

    t_prod = datetime(2025, 7, 22, 15, 57, 30)
    nodes = {
        PRODUCT_END: {"adopted": t_prod, "src": "audit"},
        "上架": {"adopted": None, "src": ""},
        "广告": {"adopted": None, "src": ""},
    }
    result = _dept_final(nodes, {})
    assert result["产品部"][0] == t_prod


def test_dept_final_missing_material_processing_is_neutral():
    """任一节点缺失时判定照旧落「缺节点」（中性，不崩）。"""
    from app.routers.review import _dept_final

    nodes = {
        PRODUCT_END: {"adopted": None, "src": ""},
        "上架": {"adopted": None, "src": ""},
        "广告": {"adopted": None, "src": ""},
    }
    result = _dept_final(nodes, {}, ruleset=rules_for("手机膜"))
    assert result["产品部"][0] is None
    assert "素材交付" in result["产品部"][1]


# ---------------------------------------------------------------------------
# ⑤ 跨实现对齐：离线 build_overview ↔ 后端 alert 口径公式
# ---------------------------------------------------------------------------
def _load_offline_script():
    """用 importlib 从 流程复盘/route_check_sys.py 加载离线脚本。

    该脚本模块级 `import pandas / openpyxl / route_check / gantt_sheet`，
    若当前解释器缺这些依赖会抛 ModuleNotFoundError —— 由调用方退化为静态断言。
    """
    import importlib.util

    if not OFFLINE_SCRIPT.exists():
        raise FileNotFoundError(f"离线脚本不存在: {OFFLINE_SCRIPT}")
    sys.path.insert(0, str(OFFLINE_SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("route_check_sys_t09", OFFLINE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _offline_build_overview_source():
    """取 build_overview 函数源码（静态断言用；避免依赖离线脚本可 import）。"""
    src = OFFLINE_SCRIPT.read_text(encoding="utf-8")
    start = src.index("def build_overview(")
    end = src.index("\ndef ", start + 1)
    return src[start:end]


def test_cross_impl_days_match():
    """同一组节点时刻下，离线 build_overview 的产品段/运营段天数 == 后端 alert 公式。

    离线脚本依赖 pandas/openpyxl（隔离 pytest 解释器通常没有）→ 本机不稳定时
    退化为「源码静态断言」：build_overview 里产品段终点/运营段起点必须是
    `nodes["素材加工"]`，且不得再出现 `nodes["拍摄交付"]`。两条路径都保留，
    无论哪条都足以钉住「离线脚本用的是素材加工边界」。
    """
    base = datetime(2025, 7, 1, 0, 0, 0)
    # 取数 → 素材加工 = 30 天；素材加工 → max(上架,广告) = 40-30 = 10 天
    t_fetch = base
    t_prod_end = base + timedelta(days=30)
    t_up = base + timedelta(days=40)
    t_ad = base + timedelta(days=36)

    backend_prod = (t_prod_end.date() - t_fetch.date()).days
    backend_ops = (max(t_up, t_ad).date() - t_prod_end.date()).days

    try:
        offline = _load_offline_script()
    except Exception as exc:                                   # noqa: BLE001
        # —— 降级路径：静态断言（原因见 docstring）——
        body = _offline_build_overview_source()
        assert 'nodes["素材加工"]["adopted"]' in body, (
            f"离线 build_overview 未用「素材加工」作边界节点（降级自 import 失败: {exc}）")
        assert "拍摄交付" not in body, (
            "离线 build_overview 仍残留「拍摄交付」边界，应已改为「素材加工」")
        return

    nodes = {
        "取数": {"adopted": t_fetch, "src": "系统", "sys_date_only": True},
        PRODUCT_END: {"adopted": t_prod_end, "src": "audit"},
        "上架": {"adopted": t_up, "src": "系统", "sys_date_only": True},
        "广告": {"adopted": t_ad, "src": "系统", "sys_date_only": True},
        "提案": {"adopted": t_fetch, "src": "系统", "sys_date_only": True},
    }
    res = {"parts": {}, "nodes": nodes, "items": [], "seg_rows": []}
    tot = offline.build_overview("T09", res)

    assert tot["产品段耗时(天)"] == backend_prod, (
        f"离线产品段={tot['产品段耗时(天)']} != 后端口径={backend_prod}")
    assert tot["运营段耗时(天)"] == backend_ops, (
        f"离线运营段={tot['运营段耗时(天)']} != 后端口径={backend_ops}")
