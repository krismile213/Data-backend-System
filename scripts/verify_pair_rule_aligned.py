# -*- coding: utf-8 -*-
"""verify_pair_rule_aligned.py — 证明后端取轮口径已与 route_check_sys 完全一致。

背景：2026-09-24 起后端 `review.py::extract_node_times` 改为「同一 Part 多张单据
统一取『最早一轮』」（逐列按 create_time 升序取第一个非空），以对齐离线主口径
`流程复盘/route_check_sys.py`。本脚本用**同一份真实数据**跑两套真实代码，逐节点
比较「系统业务时间列」扫出来的原始值，差异必须为 0。

  左：backend/app/routers/review.py::extract_node_times  （后端真实函数）
  右：流程复盘/route_check_sys.py::extract_node_times     （离线主口径）

只读：不写任何文件、不改库。
用法（须后端受管解释器）：
    <envs/default>/Scripts/python.exe scripts/verify_pair_rule_aligned.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                          # 数据后台系统
MAIN = ROOT.parent                          # work-main
FUKPAN = MAIN / "复盘文件"

sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(MAIN / "流程复盘"))

from app.routers.review import extract_node_times as be_extract   # noqa: E402
import route_check as rc                                          # noqa: E402
import route_check_sys as sysmod                                  # noqa: E402


def to_backend_docs(parts):
    """把 route_check 的「中文列 dict 行」转成后端 extract_node_times 吃的单据结构。

    后端只读 d["raw"] / d["create_time"]；audit/动作轨这里不造（nodes=[]），
    因此比较的是**系统业务时间列**这一轨 —— 正是取轮规则作用的那一轨。
    """
    out = {}
    for part, rows in parts.items():
        out[part] = [{
            "doc_id": f"{part}-{i}",
            "part": part,
            "sku": "",
            "approval_no": "",
            "status": str(r.get("审批状态") or ""),
            "create_time": rc._parse_dt(str(r.get("创建时间") or "")),
            "raw": r,
            "nodes": [],
        } for i, r in enumerate(rows)]
    return out


def main():
    if not FUKPAN.exists():
        print(f"[核对失败] 数据根不存在：{FUKPAN}")
        return 2

    diffs, n_sku, n_cmp, n_multi = [], 0, 0, 0
    for sd in sorted(d for d in FUKPAN.iterdir() if d.is_dir()):
        try:
            parts = rc.load_sku_dir_parts(sd)
        except Exception as e:                                   # noqa: BLE001
            print(f"  [跳过] {sd.name}: {e}")
            continue
        if not parts:
            continue
        n_sku += 1
        if any(len(v) >= 2 for v in parts.values()):
            n_multi += 1

        be_nodes = be_extract(to_backend_docs(parts))
        sys_nodes = sysmod.extract_node_times(parts)

        for name in sysmod.NODES:
            if name not in be_nodes:
                continue
            be_raw = (be_nodes[name] or {}).get("sys_raw") or ""
            sys_raw = (sys_nodes.get(name) or {}).get("sys_raw") or ""
            n_cmp += 1
            if be_raw != sys_raw:
                diffs.append((sd.name, name, be_raw, sys_raw))

    print(f"数据根：{FUKPAN}")
    print(f"SKU {n_sku} 个（其中含多单据 Part 的 {n_multi} 个）；逐节点比较 {n_cmp} 次")
    if diffs:
        print(f"\n[不一致] {len(diffs)} 处（前 20）：")
        for s, n, b, y in diffs[:20]:
            print(f"  {s:<10} {n:<10} 后端={b!r}  sys={y!r}")
        return 1
    print("\n[一致] 所有节点『系统业务时间列』取值与 route_check_sys 完全相同（差异 0）。")
    print("       ⇒ 多轮提交取轮口径已对齐。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
