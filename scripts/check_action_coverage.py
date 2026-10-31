# -*- coding: utf-8 -*-
"""check_action_coverage.py — 检查一个批次目录的「关键动作覆盖率」

为什么需要它：时效引擎（`parser.get_key_action_time`）靠**精确匹配** `审批记录`
里的动作名取时间。如果导出把业务节点名写成了泛化的「同意」，引擎永远取不到时间，
于是所有时效指标为空、异常列表暴增几千条假异常 —— **页面打得开，数据全错**。
这个脚本就是用来区分「数据能算」和「数据算不出来」的。

背景（2026-09-21 实测）：
    手工导出（钉钉审批后台） 30/30 命中 —— 正常
    自动同步（dingtalk/approval_sync.py）9/30 命中 —— 只有「提交申请」命中
两类数据的差别就是 `审批记录` 的动作名。

用法：
    python scripts/check_action_coverage.py <批次目录>
    python scripts/check_action_coverage.py "C:\\...\\data\\手机膜\\手机膜9.7"
    python scripts/check_action_coverage.py <目录> --json     # 输出 JSON

退出码：0 = 全部关键动作命中；1 = 有缺失（导出需要修）
"""
import argparse
import collections
import json
import sys
import warnings
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
warnings.filterwarnings("ignore")

from app.config import PART_CONFIGS          # noqa: E402
from app.parser import load_part_file, parse_approval_record   # noqa: E402


def scan(batch_dir: Path):
    """返回 {part: {"key": [...], "hit": [...], "miss": [...], "counts": {...}, "rows": n}}"""
    out = {}
    for i in range(1, 10):
        pk = f"Part{i}"
        files = sorted(batch_dir.glob(f"{pk}_*.xlsx"))
        if not files:
            continue
        if len(files) > 1:
            print(f"  [WARN] {pk} 有 {len(files)} 个文件，只读第一个；"
                  f"多份会造成单据重复计数", file=sys.stderr)
        df = load_part_file(files[0], pk)
        counts = collections.Counter()
        for text in df["审批记录"]:
            for node in parse_approval_record(text):
                if node["action"]:
                    counts[node["action"]] += 1
        key = list(PART_CONFIGS[pk]["key_actions"])
        hit = [a for a in key if counts.get(a)]
        out[pk] = {
            "name": PART_CONFIGS[pk]["name"],
            "key": key,
            "hit": hit,
            "miss": [a for a in key if not counts.get(a)],
            "counts": {a: counts.get(a, 0) for a in key},
            "distinct_actions": len(counts),
            "rows": len(df),
        }
    return out


def main():
    ap = argparse.ArgumentParser(description="关键动作覆盖率检查")
    ap.add_argument("batch_dir", help="批次目录（含 Part1~9.xlsx）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    batch_dir = Path(args.batch_dir)
    if not batch_dir.is_dir():
        print(f"[ERROR] 目录不存在: {batch_dir}")
        return 2

    res = scan(batch_dir)
    if not res:
        print(f"[ERROR] {batch_dir} 里没有找到 Part*.xlsx")
        return 2

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if all(not v["miss"] for v in res.values()) else 1

    print(f"批次目录: {batch_dir}")
    print()
    total_hit = total_key = 0
    for pk, v in res.items():
        n_hit, n_key = len(v["hit"]), len(v["key"])
        total_hit += n_hit
        total_key += n_key
        flag = "OK  " if n_hit == n_key else "FAIL"
        print(f"  [{flag}] {pk} {v['name']:<12} {n_hit}/{n_key} 命中 "
              f"（{v['rows']} 单，动作 {v['distinct_actions']} 种）")
        for a in v["miss"]:
            print(f"         缺: {a}")
    print()
    pct = total_hit * 100 // total_key if total_key else 0
    print(f"=== 关键动作覆盖 {total_hit}/{total_key} = {pct}% ===")
    if total_hit < total_key:
        print()
        print("注意：低于 100% 有两种可能，需按下面顺序排查：")
        print("  1) 业务差异 —— 该品类的钉钉审批模板本身就没有这些节点")
        print("     （用 sync_cache.db 的 activity_nodes 按节点名检索确认；")
        print("      手机壳 22/30 即属此类，缺失清单见 dingtalk/README.md §7）")
        print("  2) 导出缺陷 —— 实例 tasks 里有该节点但导出写丢了名字")
        print("     （常见是写成「同意」，会导致指标为空 + 假异常暴增，")
        print("      参考 docs/手机壳品类接入方案与导出规格.md）")
        return 1
    print("导出格式正常，时效可以计算。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
