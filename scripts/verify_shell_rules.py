# -*- coding: utf-8 -*-
"""
verify_shell_rules.py — 手机壳品类规则集的**离线**验收（不依赖手机壳已入库）
================================================================================
为什么能离线：复盘的**核心计算函数是纯函数**（接收 parts_docs / ruleset，不碰 DB）——
    review_sku_segments(sku, parts_docs, rules, ruleset=...)
所以直接用 `parser.load_part_file` 读手机壳目录，构造与 ETL 入库后同形的
parts_docs（含 approval_node），即可验证手机壳规则集是否真的跑得通。

验收内容：
  ① 品类分发：rules_for("手机壳") 的起点/节点链/段数符合预期（无「取数」、起点=提案）
  ② 逐 SKU 段级明细（用手机壳规则集）——打印最完整 SKU 的全部段
  ③ 路线总时长（**起点=提案**）× 壳版 ROUTE_TARGETS 评级（并标注"待校准"）
  ④ 损益（手机壳无评级 → 走兜底区间并标 degraded，属口径正确）
  ⑤ **按型号从手机膜反推「取数」时间**：仅作参考展示，**不列入考核**
     （用户 2026-09-22 定：手机壳不把取数计入考核，起点用提案）

用法：
    python scripts/verify_shell_rules.py [手机壳目录] [--sku K011-008]
输出：scripts/verify_shell_rules.txt（UTF-8），退出码 0=PASS / 1=FAIL
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.analysis import jsonable                                   # noqa: E402
from app.assessment import compute as assess_one                    # noqa: E402
from app.category_rules import rules_for                            # noqa: E402
from app.parser import load_part_file, parse_approval_record        # noqa: E402
from app.rule_service import get_rules                              # noqa: E402

DEFAULT_SHELL_DIR = r"D:\Desktop\work-main\data\手机壳\手机壳"
DEFAULT_MEMBRANE_ROOT = r"D:\Desktop\work-main\data\手机膜"

L = []
OK = True


def p(s=""):
    L.append(str(s))


def fail(msg):
    global OK
    OK = False
    p(f"  [FAIL] {msg}")


def to_dt(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    if not s or s.lower() in ("nan", "nat"):
        return None
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
              "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def _text(v):
    if v is None:
        return None
    try:
        import pandas as pd
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s or None


def load_dir(dirpath: Path):
    """按 ETL 入库后的形状构造 parts_docs（离线，不写库）。"""
    docs = []
    for f in sorted(dirpath.glob("Part*.xlsx")):
        part = f.name.split("_")[0]
        df = load_part_file(f, part)
        records = df.to_dict("records")
        cols = [c for c in df.columns if not str(c).startswith("_")]
        sku_col = ("新品SKU" if "新品SKU" in df.columns
                   else ("产品SKU" if "产品SKU" in df.columns else None))
        has_model = "新机型号" in df.columns
        for idx, row in enumerate(records):
            docs.append({
                "doc_id": f"{part}#{idx}", "batch": dirpath.name, "part": part,
                "sku": _text(row.get(sku_col)) if sku_col else None,
                "model": _text(row.get("新机型号")) if has_model else None,
                "approval_no": _text(row.get("审批编号")) or "",
                "status": _text(row.get("审批状态")),
                "create_time": to_dt(row.get("创建时间")),
                "finish_time": to_dt(row.get("完成时间")),
                "update_time": to_dt(row.get("更新时间")),
                "raw": {str(c): jsonable(row.get(c)) for c in cols},
                "nodes": [{"operator": n["operator"], "action": n["action"],
                           "action_time": n["actionTime"], "remark": n["remark"]}
                          for n in (parse_approval_record(row.get("审批记录")) or [])],
            })
    return docs


_MODEL_STRIP = ("apple", "手机壳", "手机膜", "钢化膜", "手机", "款")


def _norm_model(s):
    """型号归一化：小写、去品牌/品类词、去 5G 后缀、只留字母数字。

    ⚠️ 实测必要性：手机壳写 `iPhone 17 Pro`，手机膜写 `Apple iPhone 17 Pro 5G`
    —— 直接字符串比对**命中 0 个**，必须归一化。
    """
    import re
    t = str(s or "").lower()
    for w in _MODEL_STRIP:
        t = t.replace(w, " ")
    t = re.sub(r"\b5g\b", " ", t)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", t)


def membrane_ref(root: Path):
    """手机膜各批次 Part1 → ({SKU: 取数时间}, {归一化型号: 取数时间})。

    同名多值时取**最早**取数时间（与"该型号首次取得手机数据"一致）。
    """
    by_sku, by_model = {}, {}
    for f in sorted(root.glob("*/Part1_*.xlsx")):
        try:
            df = load_part_file(f, "Part1")
        except Exception:
            continue
        if "手机数据取得日期" not in df.columns:
            continue
        sku_col = ("新品SKU" if "新品SKU" in df.columns
                   else ("产品SKU" if "产品SKU" in df.columns else None))
        for _, row in df.iterrows():
            dt = to_dt(row.get("手机数据取得日期"))
            if dt is None:
                continue
            if sku_col:
                s = _text(row.get(sku_col))
                if s and (s not in by_sku or dt < by_sku[s]):
                    by_sku[s] = dt
            m = _norm_model(row.get("新机型号"))
            if m and (m not in by_model or dt < by_model[m]):
                by_model[m] = dt
    return by_sku, by_model


def shell_ref_keys(docs):
    """手机壳 SKU → {'refSku':…, 'refModel':…}（取 Part1 单据上的参考列）。

    实测手机壳 Part1 自带 `参考SKU`/`参考型号`，**直指对应的手机膜 SKU/型号**
    （如 K011-008 → 参考SKU=G475、参考型号=Xiaomi 15T Pro 5G）——比按型号串猜更可靠。
    """
    out = {}
    for d in docs:
        if d["part"] != "Part1" or not d["sku"]:
            continue
        raw = d["raw"] or {}
        rec = out.setdefault(d["sku"], {"refSku": "", "refModel": "",
                                        "model": d.get("model") or ""})
        for key, col in (("refSku", "参考SKU"), ("refModel", "参考型号")):
            v = _text(raw.get(col))
            if v and not rec[key]:
                rec[key] = v
    return out


def route_totals(nodes, items, rs):
    """三条路线总时长（起点 = rs.start_node）。返回 {路线: (起点, 终点, 天数)}。"""
    t0 = (nodes.get(rs.start_node) or {}).get("adopted")
    res = {}
    up = (nodes.get("上架") or {}).get("adopted")
    ad = (nodes.get("广告") or {}).get("adopted")
    ops_end = max([t for t in (up, ad) if t is not None], default=None)
    res["运营"] = ops_end
    for cls in ("空运", "海运"):
        arrs = [it["arr"] for it in items
                if it.get("cls") == cls and it.get("arr") is not None]
        res[cls] = max(arrs) if arrs else None
    out = {}
    for k, end in res.items():
        days = ((end - t0).total_seconds() / 86400.0
                if (t0 is not None and end is not None) else None)
        out[k] = (t0, end, days)
    return out


def grade_of(days, cfg):
    if days is None:
        return "—缺时间"
    good, limit = cfg.get("good"), cfg.get("limit")
    if limit is not None and days > limit:
        return f"差(超底线{days - limit:.0f}天)"
    if good is not None and days <= good:
        return "优"
    return "良"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", nargs="?", default=DEFAULT_SHELL_DIR)
    ap.add_argument("--sku", default=None, help="指定展示明细的 SKU")
    ap.add_argument("--membrane-root", default=DEFAULT_MEMBRANE_ROOT)
    args = ap.parse_args()

    shell_dir = Path(args.dir)
    if not shell_dir.is_dir():
        p(f"目录不存在: {shell_dir}")
        return 1

    rs = rules_for("手机壳")
    rules, ver = get_rules()

    p("=" * 78)
    p("手机壳品类规则集 离线验收（起点=提案·无取数节点）")
    p("=" * 78)
    p(f"数据目录 : {shell_dir}")
    p(f"规则集   : {rs.key} | {rs.label}")
    p(f"考核起点 : {rs.start_node}   段数={len(rs.segs)}(+L4)  "
      f"目标待校准={rs.provisional}  来源={rs.targets_source}")
    p(f"规则版本 : {ver}")

    # ---- ① 规则集结构 ----
    p("")
    p("[1] 规则集结构检查")
    if "取数" in rs.nodes:
        fail("手机壳节点表里仍有「取数」")
    else:
        p("  · 无「取数」节点（品类级结构性缺失）→ PASS")
    if rs.start_node != "提案":
        fail(f"起点应为提案，实为 {rs.start_node}")
    else:
        p("  · 考核起点 = 提案 → PASS")
    if rs.proposal_warn_days is not None:
        fail("手机壳不应有「提案→取数」预警线")
    else:
        p("  · 无「提案→取数」预警段（该项对手机壳不适用）→ PASS")
    p(f"  · 节点 {len(rs.nodes)} 个：{rs.node_names}")
    p(f"  · 首段：{rs.segs[0][1]}  目标 {rs.segs[0][4]} 天（名义值·待校准）")
    p(f"  · 签样落点：{rs.node_part('签样')} 候选列 {rs.sys_candidates('签样')}")
    p(f"  · 路线评级线（{rs.targets_source}）：{rs.route_targets}")

    # ---- 载入数据 ----
    p("")
    p("[2] 载入手机壳数据（离线解析，不写库）")
    docs = load_dir(shell_dir)
    by_sku = {}
    for d in docs:
        if d["sku"]:
            by_sku.setdefault(d["sku"], {}).setdefault(d["part"], []).append(d)
    p(f"  单据 {len(docs)} 条；有 SKU 的 {sum(len(v) for v in by_sku.values())} 条；"
      f"SKU {len(by_sku)} 个")
    dirty = [s for s in by_sku
             if not s.strip() or s.strip().lower() in ("aaa", "aaaa")]
    if dirty:
        p(f"  ⚠ 疑似脏 SKU（宜在导入前清洗）：{dirty}")

    # ---- 逐 SKU 复盘 ----
    from app.routers.review import (dedupe_docs, resolve_part1_rounds,
                                    review_sku_segments)

    p("")
    p("[3] 逐 SKU 段级复盘（壳规则集）")
    results = {}
    for sku, parts_map in sorted(by_sku.items()):
        parts_docs = {}
        for part, ds in parts_map.items():
            kept, _rm = dedupe_docs(ds)
            kept.sort(key=lambda x: x.get("create_time") or datetime.min,
                      reverse=True)
            parts_docs[part] = kept
        _p1_base, p1_rounds, multi = resolve_part1_rounds(
            parts_docs.get("Part1") or [], ruleset=rs)
        seg_rows, nodes, items = review_sku_segments(
            sku, parts_docs, rules, ruleset=rs)
        cover = sum(1 for r in seg_rows
                    if not r["gap"] and not r.get("noShip") and r["days"] is not None)
        results[sku] = {"segs": seg_rows, "nodes": nodes, "items": items,
                        "parts": sorted(parts_docs), "cover": cover,
                        "rounds": p1_rounds, "multi": multi}
    order = sorted(results, key=lambda s: (-results[s]["cover"], s))
    p(f"  覆盖段数分布："
      + ", ".join(f"{n}段×{sum(1 for s in results if results[s]['cover'] == n)}个"
                  for n in sorted({results[s]['cover'] for s in results},
                                  reverse=True)))
    p(f"  覆盖最全：{order[:5]}")

    show = args.sku or (order[0] if order else None)
    if show and show in results:
        r = results[show]
        p("")
        p(f"[4] {show} 段级明细（Part：{r['parts']}）")
        p(f"  {'部门':<8}{'环节':<24}{'耗时':>7}{'目标':>7}  判定")
        for s in r["segs"]:
            d = "—" if s["days"] is None else s["days"]
            t = "—" if s["target"] is None else s["target"]
            p(f"  {str(s['dept']):<8}{str(s['seg']):<24}{str(d):>7}{str(t):>7}  "
              f"{s['judge']}")
        p("")
        p(f"[5] {show} 三条路线总时长（**起点 = {rs.start_node}**，壳版评级线）")
        totals = route_totals(r["nodes"], r["items"], rs)
        t0 = (r["nodes"].get(rs.start_node) or {}).get("adopted")
        p(f"  起点 {rs.start_node} = {t0:%Y-%m-%d}" if t0 else "  起点缺失")
        for k in ("运营", "空运", "海运"):
            s0, end, days = totals[k]
            cfg = rs.route_targets[k]
            g = grade_of(days, cfg)
            p(f"  {k}路线: → {end.strftime('%Y-%m-%d') if end else '—'}  "
              f"{('%.0f' % days) if days is not None else '—':>5} 天   "
              f"优≤{cfg['good']} 底线={cfg['limit']}  → {g}")
        p("  ⚠️ 手机壳目标天/评级线为**继承手机膜的名义值**（起点由取数改提案后同阈值更严）"
          "，需业务校准")
        asmt = assess_one(sku=show, nodes=r["nodes"], items=r["items"],
                          product=None, rules=rules, start_node=rs.start_node)
        p("")
        p(f"[6] {show} 损益（手机壳无评级 → 兜底区间 + degraded，属口径正确）")
        if asmt:
            p(f"  起点={asmt['startAt']} 终点基准={asmt['basis']} "
              f"跨度={asmt['spanDays']}天 推迟={asmt['delayDays']}天 "
              f"方向={asmt['direction']}")
            p(f"  日均兜底={asmt['dailyMin']}~{asmt['dailyMax']}  "
              f"件数={asmt['qtyMin']}~{asmt['qtyMax']}  "
              f"金额={asmt['amountMin']}~{asmt['amountMax']}  "
              f"degraded={asmt['degraded']}")
        else:
            p("  无法评估（缺起点或缺终点）")

    # ---- 按型号/参考SKU 反推取数（参考，不计考核） ----
    p("")
    p("[7] 从手机膜反推「取数」时间 —— **仅参考，不列入手机壳考核**")
    p("     （用户 2026-09-22 定：手机壳不把取数计入考核，起点用提案）")
    mem_sku, mem_model = membrane_ref(Path(args.membrane_root))
    p(f"  手机膜侧：有取数日期的 SKU {len(mem_sku)} 个 / 型号 {len(mem_model)} 个")
    refs = shell_ref_keys(docs)
    hit_a = hit_b = 0
    rows = []
    for sku in order:
        ref = refs.get(sku) or {}
        a = mem_sku.get(ref.get("refSku") or "")
        b = (mem_model.get(_norm_model(ref.get("refModel")))
             or mem_model.get(_norm_model(ref.get("model"))))
        hit_a += 1 if a else 0
        hit_b += 1 if b else 0
        rows.append((sku, ref.get("refSku") or "", ref.get("refModel") or "", a, b))
    for sku, rs_, rm, a, b in rows[:14]:
        disp = a or b
        p(f"  · {sku:<12}参考SKU={rs_:<7}参考型号={rm:<30}"
          f"{'取数 ' + disp.strftime('%Y-%m-%d') if disp else '（未命中）'}")
    p(f"  命中：策略A(参考SKU 直连膜 SKU) {hit_a}/{len(order)}；"
      f"策略B(型号归一化) {hit_b}/{len(order)}")
    p("  ⚠️ 两策略均只作人工参考；手机壳考核**一律以提案为起点**，不受此列影响")

    # ---- 提案 vs 确样 时序诊断 ----
    p("")
    p("[8] 手机壳「提案(建单) vs 确样」时序诊断（决定起点口径的实际影响）")
    offs = []
    for d in docs:
        if d["part"] != "Part1":
            continue
        ct = d.get("create_time")
        raw = d["raw"] or {}
        qy = None
        for col in ("三方确样时间", "打样完成时间", "全部设计确认时间"):
            qy = to_dt(raw.get(col))
            if qy is not None:
                break
        if ct and qy:
            offs.append((qy - ct).days)
    if offs:
        neg = sum(1 for x in offs if x < 0)
        p(f"  样本 {len(offs)} 行；确样早于建单 {neg} 行"
          f"（{100.0 * neg / len(offs):.0f}%）；"
          f"偏移 min={min(offs)} / max={max(offs)} 天")
        p("  → 手机壳 Part1 是**确样/打样/设计确认完成后补录立项单**，"
          "故「提案→签样」段天然为 0 或小幅负值；")
        p("    若业务希望考核「从确样起算」，起点应改为签样（确样）——**需业务确认**。")

    p("")
    p("=" * 78)
    p(f"验收：{'PASS' if OK else 'FAIL'}")
    p("=" * 78)
    (Path(__file__).with_suffix(".txt")).write_text("\n".join(L), encoding="utf-8")
    print(f"exit={0 if OK else 1}  →  {Path(__file__).with_suffix('.txt')}")
    return 0 if OK else 1


if __name__ == "__main__":
    sys.exit(main())
