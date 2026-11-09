# -*- coding: utf-8 -*-
"""
g506_recheck.py — G506 考核口径复核（2026-09-21 用户纠偏后）
============================================================
口径来源：backend/app/seg_rules.py ROUTE_TARGETS + docs/SOP-新品售前流程操作手册.md §四
  - 考核时钟起点 = 取数（提案→取数 75 天仅挂「提案超期」异常，不计入考核）
  - 运营路线 = 取数 → max(上架,广告)   ≤52优 / ≤60底线
  - 空运路线 = 取数 → 最晚空运货件到货  ≤49优 / ≤60底线
  - 海运路线 = 取数 → 最晚海运货件到货  ≤69优（>69 差）
推迟天数 = 实际路线天数 − 底线天数（仅>0 计为推迟）
"""
import sys

sys.path.insert(0, r"D:\Desktop\work-main\Data-Backend-System\backend")

from app.routers.review import get_review_data
from app.seg_rules import ROUTE_TARGETS
from datetime import datetime

SKU = "G506"
data = get_review_data()
d = data["skuDetails"].get(SKU)
if d is None:
    print(f"!! {SKU} 不在复盘结果中")
    sys.exit(1)

print("=" * 72)
print(f"[{SKU}] {d['devType']}  有效复盘={d['valid']}")
print("-" * 72)
print("节点时间（adopted，系统列>audit>动作）:")
for name, nd in d["nodes"].items():
    print(f"  {name:<8} {nd['at'] or '—':<22} src={nd['src']}")

iso = {k: v["iso"] for k, v in d["nodes"].items()}


def pdt(s):
    if not s:
        return None
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


t_qushu = pdt(iso.get("取数"))
t_up, t_ad = pdt(iso.get("上架")), pdt(iso.get("广告"))
print("-" * 72)
print("货件:")
for s in d["shipments"]:
    print(f"  {s['trk'] or '(无货件号)':<22} {s['hub'] or '—':<14} "
          f"发={s['ship'] or '—'} 到={s['arr'] or '—'} cls={s['cls']} "
          f"{s['days']}天/目标{s['target']} {s['judge']}")

print("=" * 72)
if t_qushu is None:
    print("!! 缺取数时间，无法按考核口径计算")
    sys.exit(1)

routes = {}
# 运营路线
if t_up or t_ad:
    end = max(x for x in (t_up, t_ad) if x)
    routes["运营"] = end
# 空运/海运路线：取数 → 该类别最晚到货
for cls in ("空运", "海运"):
    arrs = [pdt(s["arr"]) for s in d["shipments"]
            if s.get("cls") == cls and s.get("arr")]
    if arrs:
        routes[cls] = max(arrs)

print(f"考核起点 取数 = {iso.get('取数')}")
print("-" * 72)
results = {}
for route, end in routes.items():
    tgt = ROUTE_TARGETS[route]
    days = (end - t_qushu).total_seconds() / 86400.0
    grade = ("优" if days <= tgt["good"]
             else ("良" if tgt["limit"] and days <= tgt["limit"] else "差"))
    delay = max(0.0, days - tgt["limit"]) if tgt["limit"] else None
    results[route] = (days, tgt["good"], tgt["limit"], grade, delay)
    lim = f"底线{tgt['limit']}" if tgt["limit"] else "无底线"
    print(f"{route}路线: 取数→{end:%Y-%m-%d} = {days:.1f} 天  "
          f"(优≤{tgt['good']}, {lim}) → {grade}"
          + (f"，推迟 {delay:.1f} 天" if delay else ""))

print("=" * 72)
print("损益推算定稿（2026-09-22 用户确认口径）:")
print("  推迟天数统一取【空运路线】超底线天数（空运到货即可开卖）")
UNIT_PRICE = 12.0266
for route, (days, good, limit, grade, delay) in results.items():
    if route == "空运" and delay:
        lo, hi = 1.38 * delay, 5.5 * delay
        print(f"  空运路线 推迟{delay:.0f}天 → 销量损失 {lo:.1f}~{hi:.0f} 件"
              f" → 亏损 ¥{lo * UNIT_PRICE:,.0f} ~ ¥{hi * UNIT_PRICE:,.0f}"
              f"（XC=1.38~5.5，成本价 ¥{UNIT_PRICE}）")
    elif delay:
        print(f"  （{route}路线 推迟{delay:.0f}天，仅供参考，不计入损益定稿口径）")
    else:
        print(f"  {route}路线 未推迟（{grade}）")

print("-" * 72)
print("汇总口径：")
print("  · 考核起点=取数（2025-10-30）；提案→取数75天仅计异常不进考核")
print("  · 损益定稿：空运路线 172 天 × 1.38~5.5 件/天 = 237~946 件 × ¥12.0266")
print("  · G506 最终结论：晚 172 天（空运口径），亏损 ¥2,855~¥11,377（成本价）")
