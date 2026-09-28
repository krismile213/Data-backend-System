# -*- coding: utf-8 -*-
"""probe_category_columns.py — 新品类接入前的「列结构 + 节点可替代性」探测
================================================================================
用途：把一个新的品类数据目录（或新批次）丢进来，先回答三个问题——
  1. 文件命名能不能被后台识别？（`list_batches()` 用 `name.split("_")[0]` 取 Part 号，
     **必须**形如 `Part1_产品开发-20260922.xlsx`；`Part1-xxx.xlsx` 会被静默丢弃）
  2. 这个品类的列结构和手机膜基线差在哪？（独有 / 缺少）
  3. 复盘引擎需要的 11 个节点，哪些有系统列、哪些没有、**能用哪些列替代**
     （按同义关键词自动推荐候选，并给出填充率与唯一值数，便于人工判定）

设计原则：规范列清单**只从 `app.seg_rules.NODES` 读**（单一真源），
本脚本不重复维护一份；新增节点后自动纳入探测。

用法：
    python scripts/probe_category_columns.py "D:\\...\\data\\手机壳\\手机壳"
    python scripts/probe_category_columns.py <目录> --baseline "D:\\...\\手机膜\\手机膜9.7"
    python scripts/probe_category_columns.py <目录> --out scripts\\_probe.txt

退出码：0 = 所有节点都有系统列；1 = 存在无系统列的节点（需按候选列确认替代或改口径）
"""
import argparse
import sys
import warnings
from collections import Counter
from datetime import datetime
from pathlib import Path

import openpyxl

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
warnings.filterwarnings("ignore")

from app.config import PART_CONFIGS  # noqa: E402
from app.seg_rules import NODES  # noqa: E402

DEFAULT_BASELINE = Path(r"D:\Desktop\work-main\data\手机膜\手机膜9.7")

# 节点 → 同义关键词（用于在该品类列名里搜替代列）
NODE_HINTS = {
    "提案": ["创建时间", "申请时间", "提交时间"],
    "取数": ["手机数据取得", "手机数据", "取数", "样机"],
    "签样": ["确样", "样品测试", "打样", "设计确认", "样品"],
    "采购": ["首单下单", "下单", "采购"],
    "本地仓到货": ["本地仓到货", "本地仓", "到货时间"],
    "首次上架": ["首次上架", "初次上架"],
    "寄送": ["寄送"],
    "拍摄交付": ["拍摄团队素材交付", "素材交付", "拍摄交付"],
    "素材加工": ["素材加工", "素材完成"],
    "上架": ["最终上架", "上架完成"],
    "广告": ["广告上架", "广告设置", "广告"],
}

TIME_KEYS = ("时间", "日期")


def pdt(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "-"):
        return None
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
              "%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s[:len(f) + 2], f)
        except Exception:
            pass
    return None


def load(path):
    """返回 (header, rows_of_dict)。"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return [], []
    hdr = [str(v) if v is not None else "" for v in rows[0]]
    out = []
    for vals in rows[1:]:
        if not any(v is not None and str(v).strip() for v in vals):
            continue
        out.append({h: (vals[i] if i < len(vals) else None)
                    for i, h in enumerate(hdr)})
    return hdr, out


def files_of(d):
    """返回 {part: [files]}，并标记命名是否合规。"""
    got = {}
    for f in sorted(d.glob("Part*.xlsx")):
        pk = f.name.split("_")[0]
        got.setdefault(pk, []).append(f)
    return got


def main():
    ap = argparse.ArgumentParser(description="新品类列结构与节点可替代性探测")
    ap.add_argument("batch_dir", help="批次目录（含 Part*.xlsx）")
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE),
                    help="基线品类目录（默认手机膜9.7），用于列差异对比")
    ap.add_argument("--out", help="输出文件（UTF-8）。不传则打印到 stdout")
    args = ap.parse_args()

    d = Path(args.batch_dir)
    if not d.is_dir():
        print(f"[ERROR] 目录不存在: {d}")
        return 2

    lines = []
    def p(*a):
        lines.append(" ".join(str(x) for x in a))

    p("=" * 92)
    p(f"# 品类目录探测：{d}")
    p("=" * 92)

    got = files_of(d)
    p("")
    p("## 1. 文件命名合规性（不合规会被 list_batches() 静默丢弃）")
    bad = []
    for f in sorted(d.glob("Part*.xlsx")):
        pk_dash = f.name.split("-")[0]
        pk_us = f.name.split("_")[0]
        ok = pk_us in PART_CONFIGS
        if not ok:
            bad.append(f.name)
        p(f"  [{'OK ' if ok else 'BAD'}] {f.name:<52} -> Part 号 {pk_us!r}"
          f"{'' if ok else f'（连字符版应为 {pk_dash!r}，也需改成下划线）'}")
    if bad:
        p(f"  ⚠ {len(bad)} 个文件命名不合规 → 后台看不到，请重命名成 `PartN_名称-时间戳.xlsx`")
    else:
        p("  ✔ 全部合规")

    # 表头
    p("")
    p("## 2. 各 Part 列结构")
    cols = {}
    for i in range(1, 10):
        pk = f"Part{i}"
        fs = got.get(pk) or []
        if not fs:
            p(f"  [{pk}] 无文件")
            continue
        hdr, rows = load(fs[0])
        cols[pk] = hdr
        p(f"  [{pk}] {len(fs)} 个文件，读 {fs[0].name}，{len(rows)} 单据，{len(hdr)} 列")
        p("      " + " | ".join(hdr))

    # 基线差异
    p("")
    p("## 3. 与基线品类的列差异")
    bl = Path(args.baseline)
    if bl.is_dir():
        base = {}
        for i in range(1, 10):
            pk = f"Part{i}"
            fs = sorted(bl.glob(f"{pk}*.xlsx"))
            if fs:
                base[pk] = load(fs[0])[0]
        for i in range(1, 10):
            pk = f"Part{i}"
            a, b = set(cols.get(pk, [])), set(base.get(pk, []))
            if not a and not b:
                continue
            p(f"  [{pk}]")
            p(f"     本品类独有: {sorted(a - b)}")
            p(f"     基线独有  : {sorted(b - a)}")
    else:
        p(f"  (基线目录不存在，跳过) {bl}")

    # 节点可替代性
    p("")
    p("## 4. 复盘节点的系统列存在性 + 可替代候选")
    all_cols = {h for hdr in cols.values() for h in hdr}
    # 每列填充率（跨全部 Part 的单据）
    fill = Counter()
    tot = Counter()
    uniq = {}
    for pk, hdr in cols.items():
        fs = got.get(pk) or []
        if not fs:
            continue
        _h, rows = load(fs[0])
        for h in hdr:
            if not any(k in h for k in TIME_KEYS):
                continue
            tot[h] += len(rows)
            vals = [pdt(r.get(h)) for r in rows]
            nz = [v for v in vals if v]
            fill[h] += len(nz)
            if nz:
                uniq[h] = len({v.strftime("%Y-%m-%d") for v in nz})

    missing = []
    for node, (part, col, act, _owner) in NODES.items():
        if not col:
            p(f"  {node:<8} 规范列=(无·用审批动作)  ✔ 由动作名定位")
            continue
        exists = col in all_cols
        part_ok = not exists
        # 变体：同名字段在不同 Part（如 手机数据取得日期 vs 手机数据取得时间）
        variants = [c for c in all_cols
                    if c != col and any(k in c for k in NODE_HINTS.get(node, []))
                    and any(k in c for k in TIME_KEYS)]
        p(f"  {node:<8} 规范列 {part}.{col} -> "
          f"{'✔ 存在' if exists else '✘ 本品类无此列'}")
        if not exists:
            missing.append(node)
            if variants:
                p(f"      候选替代列：")
                for v in sorted(variants):
                    eff = f"{fill.get(v,0)}/{tot.get(v,0)}"
                    pct = (fill.get(v, 0) * 100 // tot[v]) if tot.get(v) else 0
                    p(f"        · {v:<26} 非空 {eff:<10} ({pct:>3}%)"
                      f"  唯一日期={uniq.get(v,'-')}")
            else:
                p("        （未找到同义候选列 → 该节点在本品类可能真的不存在，需业务确认）")

    p("")
    p(f"## 结论：{len(NODES)} 个节点中 {len(missing)} 个无规范系统列"
      f"{'：' + '、'.join(missing) if missing else ''}")

    text = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"OK -> {args.out}")
    else:
        print(text)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
