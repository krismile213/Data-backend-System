#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_doc_delta.py — 未闭环 SKU 单据增量模型：自检 / 报告 / 导出（T10）

两个用途（同一个脚本，避免再拆一份）：

  1) 只读自检（默认）——`start.py verify` 的离线链路调它
       契约断言：单据键不含自增 id、新单据的动作不重复计、未闭环集合与复盘一致…
       任一条不成立退出码 1。

  2) 人工查看 / 落盘 —— `start.py delta` 调它（带 --record）
       --record       先拍快照（指纹没变不会产生新事件），再打印增量报告
       --export PATH  另外导出 xlsx（5 sheet）
       --days N       统计窗口（默认 30）
       --category C   只处理某品类（默认全部品类）
       --sku S        只看某个 SKU

为什么需要它：批次目录名固定后批次对比（/api/compare）失去意义，日常要看的是
「这批未闭环 SKU 又多了哪些单据」。模型的口径错一点，业务看到的增量就是假的，
所以这里把三条最容易写错的契约钉成断言（见 build 里的注释）。

用法：
    python scripts/verify_doc_delta.py
    python scripts/verify_doc_delta.py --record --export %TEMP%\\单据增量.xlsx
退出码 0 = 通过，1 = 有问题，2 = 环境问题（无数据/无 DB）
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import doc_delta_service as svc          # noqa: E402
from app.config import DATA_ROOTS                 # noqa: E402


def _hr(title):
    print("-" * 68, flush=True)
    print(title, flush=True)
    print("-" * 68, flush=True)


def _check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""),
          flush=True)
    return bool(ok)


def contract_checks():
    """不依赖数据的契约断言（口径错了这里必须先红）。"""
    _hr("一、契约断言（与数据无关）")
    results = []

    # 1) 单据键必须是 SKU|Part|审批编号，**不能**含数据库自增 id
    k = svc.doc_key("G506", "Part6", "20250722155730", None)
    results.append(_check("单据键 = SKU|Part|审批编号", k == "G506|Part6|20250722155730", k))
    k2 = svc.doc_key("G506", "Part6", "", "2026-09-28 09:30:00")
    results.append(_check("审批编号缺失时用创建时间兜底（稳于自增 id）",
                          k2 == "G506|Part6|c:2026-09-28 09:30:00", k2))

    # 2) 节点指纹三段式，动作时间缺失也不能错位
    sig = svc.node_sig({"action": "抄送", "action_time": None, "operator": "甲"})
    results.append(_check("节点指纹三段式（时间缺失留空占位）",
                          sig == "抄送||甲", sig))

    # 3) 新单据的动作不重复计入 newNodes
    cur = {"G1|Part6|9": {"s": "审批中", "c": "2026-09-01 09:00:00",
                          "n": ["a|2026-09-01 09:00:00|甲",
                                "b|2026-09-01 10:00:00|乙"]}}
    out = svc.diff_docs({}, cur)
    results.append(_check("新单据只计 1 张单、不计动作",
                          out["summary"]["newDocs"] == 1
                          and out["summary"]["newNodes"] == 0,
                          f"newDocs={out['summary']['newDocs']} "
                          f"newNodes={out['summary']['newNodes']}"))
    results.append(_check("新单据行带 nodeCount（信息不丢）",
                          out["newDocs"][0]["nodeCount"] == 2))

    # 4) 既有单据上的新动作 / 状态变化能被抓出
    prev = {"G1|Part6|9": {"s": "审批中", "c": "2026-09-01 09:00:00",
                           "n": ["a|2026-09-01 09:00:00|甲"]}}
    cur2 = {"G1|Part6|9": {"s": "已结束", "c": "2026-09-01 09:00:00",
                           "n": ["a|2026-09-01 09:00:00|甲",
                                 "b|2026-09-02 10:00:00|乙"]}}
    out2 = svc.diff_docs(prev, cur2)
    results.append(_check("既有单据的新动作被抓出",
                          out2["summary"]["newNodes"] == 1
                          and out2["newNodes"][0]["action"] == "b"))
    results.append(_check("状态变化被抓出",
                          out2["summary"]["statusChanges"] == 1
                          and out2["statusChanges"][0]["to"] == "已结束"))

    # 5) 空 diff（同数据重复导入）必须为零
    out3 = svc.diff_docs(prev, {"G1|Part6|9": dict(prev["G1|Part6|9"])})
    results.append(_check("同数据重算 = 零增量（重导入不会造假增量）",
                          sum(v for kk, v in out3["summary"].items()
                              if isinstance(v, int)
                              and kk in ("newDocs", "newNodes", "statusChanges",
                                         "newSkus", "closedSkus")) == 0))
    return results


def data_checks(cats):
    """真实数据下的口径一致性（无数据则整体 skip）。"""
    _hr("真实数据一致性")
    from app.routers.review import get_review_data

    results, any_data = [], False
    for cat in cats:
        try:
            rows = get_review_data(cat)["rows"]
            docs, open_skus = svc.build_docs(cat)
        except Exception as e:                        # noqa: BLE001
            print(f"  SKIP  {cat}：取数失败 {type(e).__name__}: {e}", flush=True)
            continue
        any_data = True
        expect = {r["sku"] for r in rows if not r["valid"]}
        results.append(_check(
            f"{cat}：未闭环 SKU 集合与复盘一致（valid=False）",
            open_skus == expect, f"{len(open_skus)} 个"))
        got = {svc.split_key(k)[0] for k in docs}
        results.append(_check(f"{cat}：单据表只含未闭环 SKU（无已闭环混入）",
                              got <= expect, f"{len(docs)} 张单"))
        bad = [k for k in docs if k.count("|") < 2]
        results.append(_check(f"{cat}：单据键格式正确（SKU|Part|编号）",
                              not bad, f"异常 {len(bad)} 条"))
        no_no = [k for k in docs if svc.split_key(k)[2] == ""]
        results.append(_check(f"{cat}：单据键第三段非空（编号缺失须带 c: 兜底）",
                              not no_no, f"空尾键 {len(no_no)} 条"))
        print(f"        {cat}：{len(open_skus)} 个未闭环 SKU / {len(docs)} 张单据 / "
              f"{sum(len(v['n']) for v in docs.values())} 个节点", flush=True)
    if not any_data:
        print("  SKIP  两个品类都没有可读数据（DB 或批次目录未就绪）", flush=True)
    return results, any_data


def print_report(cats, days, sku):
    _hr(f"增量报告（近 {days} 天{('，SKU=' + sku) if sku else ''}）")
    for cat in cats:
        d = svc.doc_delta(cat, days=days, sku=sku)
        st = d.get("state")
        if not d.get("exists"):
            print(f"  {cat}：尚未建立基线（跑 --record，或让自动管线/页面先访问一次）",
                  flush=True)
            continue
        print(f"  {cat}  基线 {st['at']}  {st['skuCount']} 个未闭环 SKU / "
              f"{st['docCount']} 张单据", flush=True)
        s = d["window"]["summary"]
        print(f"        近 {days} 天：新增单据 {s['newDocs']} / 新增动作 {s['newNodes']} / "
              f"状态变化 {s['statusChanges']} / 新进入 {s['newSkus']} / "
              f"已闭环离开 {s['closedSkus']}（{s['snapshots']} 次快照）", flush=True)
        last = d.get("last")
        if last:
            mark = []
            if last.get("baseline"):
                mark.append("基线（不计增量）")
            if last.get("suspect"):
                mark.append("⚠ 疑似基线重算")
            print(f"        最近一次 {last['at']}：新增单据 {len(last['newDocs'])} / "
                  f"新动作 {len(last['newNodes'])}"
                  + (f"  [{'；'.join(mark)}]" if mark else ""), flush=True)
            for r in last["newDocs"][:8]:
                print(f"          + 单 {r['sku']:<10} {r['part']:<6} "
                      f"{r.get('approvalNo') or '—'}  {r.get('status') or ''}", flush=True)
            for r in last["newNodes"][:8]:
                print(f"          · 动 {r['sku']:<10} {r['part']:<6} "
                      f"{r.get('action') or ''}  {r.get('at') or ''} "
                      f"{r.get('operator') or ''}", flush=True)
            more = len(last["newDocs"]) + len(last["newNodes"]) - 16
            if more > 0:
                print(f"          …另有 {more} 条，详见页面或 --export", flush=True)
        ranks = [r for r in d["window"]["bySku"] if r["newDocs"] or r["newNodes"]][:8]
        if ranks:
            print("        变化最多的 SKU：", flush=True)
            for r in ranks:
                print(f"          {r['sku']:<10} 新增单据 {r['newDocs']:<3} "
                      f"新增动作 {r['newNodes']:<3} 当前单据 {r['docCount']:<4} "
                      f"{r['model'] or ''}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="单据增量模型 自检/报告/导出")
    ap.add_argument("--category", default=None, help="只处理某品类（缺省全部）")
    ap.add_argument("--days", type=int, default=svc.DEFAULT_WINDOW_DAYS)
    ap.add_argument("--sku", default=None)
    ap.add_argument("--record", action="store_true",
                    help="先拍一张快照（指纹变化才落；人工查看用）")
    ap.add_argument("--export", default=None, help="导出 xlsx 到指定路径")
    args = ap.parse_args()

    cats = [args.category] if args.category else list(DATA_ROOTS)
    ok = True

    ok &= all(contract_checks())

    if args.record:
        _hr("二、拍快照")
        for cat in cats:
            try:
                r = svc.record_doc_snapshot(cat)
            except Exception as e:                    # noqa: BLE001
                print(f"  FAIL  {cat} 拍快照失败：{type(e).__name__}: {e}", flush=True)
                ok = False
                continue
            print(f"  {'OK  ' if r['recorded'] else 'SKIP'}  {cat}：{r['reason']}", flush=True)
        print(flush=True)
        data_ok, _ = data_checks(cats)
        ok &= all(data_ok)
    else:
        data_ok, _ = data_checks(cats)
        ok &= all(data_ok)

    print()
    print_report(cats, args.days, args.sku)

    if args.export:
        from app.reporting import doc_delta_report
        out = Path(args.export)
        for i, cat in enumerate(cats):
            delta = svc.doc_delta(cat, days=args.days, sku=args.sku)
            rows = svc.window_rows(cat, days=args.days, sku=args.sku)
            from app.category_rules import rules_for
            rs = rules_for(cat)
            blob = doc_delta_report(delta, rows, category=cat,
                                    provisional=rs.provisional,
                                    targets_source=rs.targets_source)
            target = out if len(cats) == 1 else \
                out.with_name(f"{out.stem}_{cat}{out.suffix or '.xlsx'}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
            print(f"\n  导出 {cat} → {target}（{len(blob)} B）", flush=True)

    print()
    print("=" * 68, flush=True)
    print("单据增量模型自检通过" if ok else "单据增量模型自检未通过（见上面 FAIL）", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
