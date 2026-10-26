# -*- coding: utf-8 -*-
"""
owner_config.py — 默认责任人外部化（资产文件/责任人配置.xlsx）
================================================================
背景（2026-10-26 用户决策）：换一个环节的责任人以前要改代码（离线脚本
`流程复盘/route_check*.py` + 后端 `seg_rules*.py` 四处硬编码）。现在抽成
一张可编辑表，四个消费方读同一份文件。

表结构（sheet 名不限，取第 1 个 sheet；表头必须在第 1 行）：
    | 口径  | 类型 | 名称   | 默认责任人 | 备注 |
    | sys   | 环节 | 取数→签样 | 李杰华   | ... |
    | sys   | 节点 | 取数      | 李杰华   | ... |

口径（scope）约定：
    audit — 离线 audit 口径（route_check.py），键 = 环节说明
    sys   — 系统口径（route_check_sys.py / 后端 seg_rules*.py），
            键 = 环节名（SEGS[1]）或 节点名（NODES 的 key）
    ⚠️ 后端手机膜与手机壳**共用 sys 口径**（节点名/段名各自独立匹配，不会互串）；
       想只改手机壳的人请用 sys_shell 口径（本模块支持，表里没行就不生效）。

容错约定（很重要）：
  · 文件不存在 / 读失败 / 某行「默认责任人」为空 → 一律沿用代码内置默认值；
  · 读取失败只打印一行提示，绝不抛异常（责任人配置不该拖垮复盘/预警接口）。

⚠️ 调用方必须在**模块加载末尾**用本模块原地改写 NODES/SEGS：
    `from .seg_rules import NODES, SEGS` 这类导入绑的是**对象引用**，若在别处
    重新赋值 `SEGS = [...]`，两边会看到不同的表（一半生效一半不生效）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CFG_FILENAME = "责任人配置.xlsx"

# (口径, 类型, 名称) → 默认责任人
OverrideKey = Tuple[str, str, str]

_CACHE: Dict[Tuple[str, float], List[Dict[str, str]]] = {}
_ENV_WARNED = False


def _candidates() -> List[Path]:
    """按优先级返回候选配置路径（第一个存在的生效）。

    1. $OWNER_CFG_XLSX —— 一旦设置就**只认它**（企业化部署最可控；设错时宁可
       全用代码默认，也不要静默跑去读别处的表，否则"改了表没生效"极难排查）
    2. <数据后台系统>/资产文件/责任人配置.xlsx  （部署时可就近放一份）
    3. <数据后台系统>/../资产文件/责任人配置.xlsx（本机 work-main 共用一份，
       与离线脚本 route_check*.py 完全同源）
    """
    env = os.environ.get("OWNER_CFG_XLSX")
    if env:
        return [Path(env)]
    proj = Path(__file__).resolve().parents[2]      # backend/app/owner_config.py → 数据后台系统/
    return [proj / "资产文件" / CFG_FILENAME,
            proj.parent / "资产文件" / CFG_FILENAME]


def config_path() -> Optional[Path]:
    """返回实际生效的配置文件路径；一个都不存在时返回 None（= 全用代码默认）。"""
    global _ENV_WARNED
    for p in _candidates():
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    env = os.environ.get("OWNER_CFG_XLSX")
    if env and not _ENV_WARNED:
        _ENV_WARNED = True
        print(f"[责任人配置] OWNER_CFG_XLSX 指向的文件不存在({env})，改用代码内置默认责任人")
    return None


def _read_rows(p: Path) -> List[Dict[str, str]]:
    """读 xlsx → 行字典列表（按 mtime 缓存，避免三次 import 各读一遍）。"""
    key = (str(p), p.stat().st_mtime)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    import pandas as pd

    df = pd.read_excel(p, sheet_name=0, dtype=str).fillna("")
    rows = [{str(k).strip(): str(v).strip() for k, v in r.items()}
            for r in df.to_dict("records")]
    _CACHE.clear()
    _CACHE[key] = rows
    return rows


def load_overrides(scope: str = "sys", path: Optional[Path] = None) -> Dict[Tuple[str, str], str]:
    """读配置表 → {(类型, 名称): 默认责任人}，只保留指定口径的行。

    异常一律吞掉并返回 {}（= 全部沿用代码默认，行为与引入本模块前一致）。
    """
    if path is None:
        path = config_path()
    if path is None:
        return {}
    p = Path(path)
    try:
        rows = _read_rows(p)
    except Exception as e:                                    # noqa: BLE001
        print(f"[责任人配置] 读取失败({p})：{e}，使用代码内置默认责任人")
        return {}

    want = str(scope).strip().lower()
    out: Dict[Tuple[str, str], str] = {}
    for r in rows:
        if r.get("口径", "").strip().lower() != want:
            continue
        kind = r.get("类型", "").strip()
        name = r.get("名称", "").strip()
        owner = r.get("默认责任人", "").strip()
        if kind and name and owner:
            out[(kind, name)] = owner
    return out


# ---------------------------------------------------------------------------
# 三个 apply_*：原地改（in-place）调用方模块里的常量，返回实际改动的条数
# ---------------------------------------------------------------------------
def apply_to_nodes(nodes: Dict[str, tuple], scope: str = "sys") -> int:
    """NODES[name] = (Part, 系统列, 审批动作, 默认责任人) → 只换第 4 位。"""
    ov = load_overrides(scope)
    if not ov:
        return 0
    hit = 0
    for name, spec in list(nodes.items()):
        new = ov.get(("节点", name))
        if new and new != spec[3]:
            nodes[name] = (spec[0], spec[1], spec[2], new)
            hit += 1
    return hit


def apply_to_segs(segs: List[tuple], scope: str = "sys") -> int:
    """SEGS[i] = (部门, 环节, 起点, 终点, 目标天, 默认责任人, 说明, 留空段) → 只换第 6 位。"""
    ov = load_overrides(scope)
    if not ov:
        return 0
    hit = 0
    for i, seg in enumerate(segs):
        new = ov.get(("环节", seg[1]))
        if new and new != seg[5]:
            segs[i] = seg[:5] + (new,) + seg[6:]
            hit += 1
    return hit


def apply_to_l4(l4: tuple, scope: str = "sys") -> tuple:
    """L4 = (部门, 环节, 起点, 目标天, 默认责任人, 说明) → 换第 5 位（元组不可原地改，返回新值）。"""
    new = load_overrides(scope).get(("环节", l4[1]))
    if new and new != l4[4]:
        return l4[:4] + (new,) + l4[5:]
    return l4
