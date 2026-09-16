# -*- coding: utf-8 -*-
"""
review.py — 大流程复盘计算引擎（GET /api/review/*）
====================================================
口径：实际动作映射（移植自 流程复盘/route_check_sys.py 系统口径）：
  - 节点时间优先级：系统业务时间列 > audit(审批记录备注含 audit) > 审批动作时间；
  - 段级绩效规则（seg_rules.SEGS）：每段 = 起点动作→终点动作 × 目标天，
    目标天/是否考核可在规则配置中心(seg_targets)调整；
  - 跨批次重复单据只保留最新（同 Part 同审批编号，按 update_time）；
  - 运输段：Part5 发货 × Part8 到货按货件号配对，目标按渠道
    (空运15/海运35/卡航35)，空/海汇总 + 英欧日美单独行；
  - 有效复盘 = 三部门终节点全完成（产品=本地仓到货、运营=上架+广告、
    后勤=渠道仓到货），全量口径不限时间窗口。

接口：
  GET /api/review/overview   全量总览（KPI + 分布 + 卡点TOP + 环节统计 + 人员 + SKU行）
  GET /api/review/sku/{code} 单个 SKU 复盘档案（段级明细、部门终节点）
  GET /api/review/sku/{code}/report  单 SKU 复盘报告 markdown

缓存：进程内缓存结果，以 batch 表 (COUNT, MAX(imported_at)) 为指纹，ETL 后自动失效。
"""

import threading
from datetime import datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..db import SessionLocal
from ..models import ApprovalNode, Batch, PartDoc
from ..review_rules import DEPARTMENTS, PART_NAMES
from ..rule_service import get_rules
from ..seg_rules import (AIR_TARGET, FOUR_COUNTRIES, G_ORDER, L4, NODES,
                         SEA_TARGET, SEGS, ship_class, transport_mode)

router = APIRouter(prefix="/api/review", tags=["review"])

_TIME_FMTS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
              "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",       # ISO（防 isoformat 复发）
              "%Y/%m/%d %H:%M:%S", "%Y/%m/%d")


def _parse_dt(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    for f in _TIME_FMTS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def _fmt(t):
    if t is None:
        return None
    if t.hour == 0 and t.minute == 0:
        return t.strftime("%Y-%m-%d")
    return t.strftime("%Y-%m-%d %H:%M")


def _disp(t, src):
    """时间显示：audit/动作来源带来源标签，便于审计。"""
    if t is None:
        return None
    if src == "audit":
        return f"{t.strftime('%Y-%m-%d %H:%M')}(audit)"
    if src == "动作":
        return f"{t.strftime('%Y-%m-%d %H:%M')}(动作)"
    return _fmt(t)


def _fmt_days(d):
    if d is None:
        return None
    return int(d) if abs(d - round(d)) < 1e-9 else round(d, 1)


def _dday(a, b):
    """带符号天数差（b-a），任一为 None 返回 None。"""
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 86400.0


def _clean(v):
    s = str(v if v is not None else "").strip()
    return "" if s.lower() == "nan" else s


def _clean_operators(raw_op):
    """操作人串清洗：拆 '→' 与 ','，剔除通知/测试群发，得人名集合。"""
    out = set()
    if not raw_op:
        return out
    for seg in str(raw_op).split("→"):
        for name in seg.split(","):
            n = name.strip()
            if n and "通知" not in n and "测试" not in n:
                out.add(n)
    return out


# ---------------------------------------------------------------------------
# 数据装载与去重
# ---------------------------------------------------------------------------
def _load_sku_docs():
    """加载全部有 SKU 的单据 + 审批节点（含未结束，货件配对需要），按 doc 组织。"""
    with SessionLocal() as session:
        docs = session.execute(
            select(PartDoc.id, PartDoc.batch_id, PartDoc.part, PartDoc.sku,
                   PartDoc.model, PartDoc.approval_no, PartDoc.status,
                   PartDoc.create_time, PartDoc.finish_time, PartDoc.update_time,
                   PartDoc.raw, Batch.name.label("batch_name"))
            .join(Batch, PartDoc.batch_id == Batch.id)
            .where(PartDoc.sku.isnot(None))
        ).all()
        nodes = session.execute(
            select(ApprovalNode.doc_id, ApprovalNode.seq, ApprovalNode.operator,
                   ApprovalNode.action, ApprovalNode.action_time,
                   ApprovalNode.remark)
            .join(PartDoc, ApprovalNode.doc_id == PartDoc.id)
            .where(PartDoc.sku.isnot(None))
            .order_by(ApprovalNode.doc_id, ApprovalNode.seq)
        ).all()

    nodes_by_doc = {}
    for doc_id, seq, op, act, t, remark in nodes:
        nodes_by_doc.setdefault(doc_id, []).append({
            "operator": op, "action": act, "action_time": t,
            "remark": remark or "",
        })

    out = []
    for (doc_id, batch_id, part, sku, model, approval_no, status,
         create_time, finish_time, update_time, raw, batch_name) in docs:
        out.append({
            "doc_id": doc_id, "batch": batch_name, "part": part, "sku": sku,
            "model": model, "approval_no": approval_no or "", "status": status,
            "create_time": create_time, "finish_time": finish_time,
            "update_time": update_time, "raw": raw or {},
            "nodes": nodes_by_doc.get(doc_id, []),
        })
    return out


def _doc_time_key(d):
    """去重时的时间排序键：update_time 优先，其次 finish/create。"""
    t = d.get("update_time") or d.get("finish_time") or d.get("create_time")
    if isinstance(t, str):
        t = _parse_dt(t)
    return t or datetime.min


def dedupe_docs(docs):
    """跨批次重复单据只保留最新：同 (Part, 审批编号) 取 update_time 最新一条。
    无审批编号时退化为 (Part, 创建时间) 键。返回 (保留列表, 去掉数量)。"""
    best = {}
    order = []
    for d in docs:
        key = (d["part"], d["approval_no"] or str(d.get("create_time")))
        if key not in best:
            best[key] = d
            order.append(key)
        elif _doc_time_key(d) > _doc_time_key(best[key]):
            best[key] = d
    kept = [best[k] for k in order]
    return kept, len(docs) - len(kept)


# ---------------------------------------------------------------------------
# 节点时间提取（系统列 > audit > 动作）
# ---------------------------------------------------------------------------
def extract_node_times(parts_docs):
    """parts_docs: {part: [去重后单据，创建时间降序]} → {节点名: 节点时间字典}。"""
    nodes = {}
    for name, (part, sys_field, act, _owner) in NODES.items():
        docs = parts_docs.get(part) or []
        sys_raw, sys_dt = "", None
        for d in docs:
            v = _clean(d["raw"].get(sys_field)) if sys_field else ""
            if v:
                sys_raw, sys_dt = v, _parse_dt(v)
                break
        audit_dt = audit_op = None
        act_dt = act_op = None
        for d in docs:
            for n in d["nodes"]:
                if n["action"] != act or n["action_time"] is None:
                    continue
                if audit_dt is None and "audit" in (n["remark"] or ""):
                    audit_dt, audit_op = n["action_time"], n["operator"] or ""
                if (act_dt is None
                        and not any(k in (n["action"] or "")
                                    for k in ("通知", "抄送"))):
                    act_dt, act_op = n["action_time"], n["operator"] or ""
            if audit_dt is not None and act_dt is not None:
                break
        if sys_dt is not None:
            adopted, src = sys_dt, "系统"
        elif audit_dt is not None:
            adopted, src = audit_dt, "audit"
        elif act_dt is not None:
            adopted, src = act_dt, "动作"
        else:
            adopted, src = None, "缺失"
        nodes[name] = {
            "part": part, "sys": sys_dt, "sys_raw": sys_raw,
            "sys_date_only": bool(sys_raw) and len(sys_raw) <= 10,
            "audit": audit_dt, "audit_op": audit_op,
            "action": act_dt, "action_op": act_op,
            "adopted": adopted, "src": src,
        }
    return nodes


def node_operator(nd, default=""):
    """节点实际执行人：audit 上报人 > 审批动作执行人 > 配置默认。"""
    return nd.get("audit_op") or nd.get("action_op") or default


def _seg_days(nd0, t0, src0, nd1, t1, src1):
    """段耗时(天)：任一端是纯日期型系统值时按日期差取整，否则保留时分秒精度。"""
    d = _dday(t0, t1)
    if d is None:
        return None
    do0 = src0 == "系统" and nd0.get("sys_date_only")
    do1 = src1 == "系统" and nd1.get("sys_date_only")
    if do0 or do1:
        return float((t1.date() - t0.date()).days)
    return d


def _seg_judge(d, target, seg_name=""):
    if d is None:
        return "—缺时间"
    if d < 0:
        return f"⚠反向({_fmt_days(d)}天)"
    if seg_name == "取数→签样" and d < 9:
        return f"⚠过短({_fmt_days(d)}<9天)"
    return "✓" if d <= target else f"✗超{_fmt_days(d - target)}天"


# ---------------------------------------------------------------------------
# 货件配对与运输段
# ---------------------------------------------------------------------------
def build_shipments(parts_docs):
    """Part5 发货 × Part8 到货按货件号配对（同货件号保留有业务时间的最新记录）。
    另：Part5 中只有发往渠道仓库、无货件号的记录 → 渠道未发货占位条目
    （货件号/发货/到货均留空，渠道仓保留，供货件明细/甘特/异常判定展示）。"""
    p5, p8 = {}, {}
    no_trk_hubs = []                       # 有渠道仓但无货件号（按首现顺序）
    hubs_with_trk = set()
    for d in (parts_docs.get("Part5") or []):          # 已按创建时间降序
        raw = d["raw"]
        trk = _clean(raw.get("货件号"))
        hub = _clean(raw.get("发往渠道仓库"))
        if not trk:
            if hub and hub not in no_trk_hubs:
                no_trk_hubs.append(hub)
            continue
        hubs_with_trk.add(hub)
        sd = _parse_dt(_clean(raw.get("发货日期")))
        prev = p5.get(trk)
        if prev is None or (sd and not prev[1]):
            p5[trk] = (hub, sd)
    for d in (parts_docs.get("Part8") or []):
        trk = _clean(d["raw"].get("货件号"))
        if not trk:
            continue
        ad = _parse_dt(_clean(d["raw"].get("渠道仓到货时间")))
        prev = p8.get(trk)
        if prev is None or (ad and not prev):
            p8[trk] = ad
    items = []
    for trk, (hub, sd) in p5.items():
        arr = p8.get(trk)
        mode, tgt = transport_mode(hub)
        items.append({"trk": trk, "hub": hub, "ship": sd, "arr": arr,
                      "days": _dday(sd, arr), "mode": mode, "target": tgt,
                      "cls": ship_class(mode), "noShip": False})
    for hub in no_trk_hubs:
        if hub in hubs_with_trk:
            continue                       # 该渠道已有正式货件，不重复列
        mode, tgt = transport_mode(hub)
        items.append({"trk": "", "hub": hub, "ship": None, "arr": None,
                      "days": None, "mode": mode, "target": tgt,
                      "cls": ship_class(mode), "noShip": True})
    items.sort(key=lambda x: (x["ship"] or datetime.max,
                              x["trk"] or "￿", x["hub"]))
    return items


def transport_rows(items):
    """运输汇总行（空/海：最早发货→最晚到货）+ 4 国单独行。"""
    rows = []
    for cls, tgt, label in (("空运", AIR_TARGET, "运输·空运"),
                            ("海运", SEA_TARGET, "运输·海运")):
        sub = [it for it in items
               if it["cls"] == cls and it["arr"] and it["ship"]]
        if not sub:
            continue
        s0, s1 = min(it["ship"] for it in sub), max(it["arr"] for it in sub)
        d = float((s1 - s0).days)
        rows.append({
            "dept": "运输", "seg": f"{label}({len(sub)}票)",
            "from": "发货(最早)", "to": "到货(最晚)",
            "t0": s0, "t1": s1, "days": _fmt_days(d), "target": tgt,
            "judge": "✓" if d <= tgt else f"✗超{_fmt_days(d - tgt)}天",
            "executor": "", "note": "最早发货→最晚到货", "gap": False,
            "assessed": False,
        })
    for cname, kw in FOUR_COUNTRIES:
        sub = [it for it in items
               if kw in it["hub"] and it["arr"] and it["ship"]]
        if not sub:
            continue
        s0, s1 = min(it["ship"] for it in sub), max(it["arr"] for it in sub)
        d = float((s1 - s0).days)
        tgt = sub[0]["target"] or (AIR_TARGET if sub[0]["cls"] == "空运"
                                   else SEA_TARGET)
        rows.append({
            "dept": "4国", "seg": f"{cname}·运输",
            "from": "发货(最早)", "to": "到货(最晚)",
            "t0": s0, "t1": s1, "days": _fmt_days(d), "target": tgt,
            "judge": "✓" if d <= tgt else f"✗超{_fmt_days(d - tgt)}天",
            "executor": "", "note": f"{len(sub)}票·{sub[0]['mode']}", "gap": False,
            "assessed": False,
        })
    return rows


# ---------------------------------------------------------------------------
# 单 SKU 段级复盘
# ---------------------------------------------------------------------------
def review_sku_segments(sku, parts_docs, rules):
    """按实际动作映射计算一个 SKU 的段级明细。返回 (seg_rows, nodes, items)。"""
    nodes = extract_node_times(parts_docs)
    items = build_shipments(parts_docs)
    seg_targets = rules.get("seg_targets") or {}

    seg_rows = []
    for dept, name, n0, n1, def_target, owner, note, is_gap in SEGS:
        cfg = seg_targets.get(name) or {}
        target = cfg.get("target", def_target)
        available = bool(cfg.get("available", True))
        nd0, nd1 = nodes[n0], nodes[n1]
        t0, src0 = nd0["adopted"], nd0["src"]
        t1, src1 = nd1["adopted"], nd1["src"]
        d = _seg_days(nd0, t0, src0, nd1, t1, src1)
        if is_gap:
            judge = "—留空"
            executor = ""
        else:
            judge = _seg_judge(d, target, name)
            # 寄送→拍摄交付 归属起点节点执行人，其余段取终点节点执行人
            op_node = n0 if name == "寄送→拍摄交付" else n1
            executor = node_operator(nodes[op_node], owner)
        seg_rows.append({
            "dept": dept, "seg": name, "from": n0, "to": n1,
            "start": _disp(t0, src0), "end": _disp(t1, src1),
            "t0": t0, "t1": t1, "src0": src0, "src1": src1,
            "days": _fmt_days(d), "target": None if is_gap else target,
            "judge": judge, "owner": owner, "executor": executor,
            "note": note, "gap": is_gap, "assessed": available and not is_gap,
        })

    # 后勤④ 采购→发货末批（多货件取末批发货日期）
    dept, name, n0, def_target, owner, note = L4
    cfg = seg_targets.get(name) or {}
    target = cfg.get("target", def_target)
    nd_purchase = nodes[n0]
    ship_days = [it["ship"] for it in items if it["ship"] is not None]
    last_ship = max(ship_days) if ship_days else None
    d_l4 = _seg_days(nd_purchase, nd_purchase["adopted"], nd_purchase["src"],
                     {"sys_date_only": True}, last_ship, "系统")
    ship_op = ""
    for d5 in (parts_docs.get("Part5") or []):
        for n in d5["nodes"]:
            if n["action"] == "发货" and n["operator"]:
                ship_op = n["operator"]          # 逐条覆盖即取最后一条发货操作人
    seg_rows.append({
        "dept": dept, "seg": name, "from": n0, "to": "发货末批(Part5)",
        "start": _disp(nd_purchase["adopted"], nd_purchase["src"]),
        "end": _fmt(last_ship), "t0": nd_purchase["adopted"], "t1": last_ship,
        "src0": nd_purchase["src"], "src1": "系统",
        "days": _fmt_days(d_l4), "target": target,
        "judge": _seg_judge(d_l4, target), "owner": owner,
        "executor": ship_op or owner, "note": note, "gap": False,
        "assessed": bool(cfg.get("available", True)),
    })

    # 后勤⑤ 渠道未发货异常（Part5 已立项渠道但无货件号/发货记录）
    # 独立判定类型：采购下单 → 至今仍未发货，与「采购→发货末批」异常分开列示
    today = datetime.now()
    t_purchase = nd_purchase["adopted"]
    stall_days = _fmt_days(_dday(t_purchase, today)) if t_purchase else None
    for it in items:
        if not it.get("noShip"):
            continue
        over = stall_days is not None and stall_days > target
        it["stallDays"] = stall_days
        it["judge"] = (f"✗超时未发货(采购后{stall_days:.0f}天)" if over
                       else (f"⏳未发货(采购后{stall_days:.0f}天)"
                             if stall_days is not None else "⏳未发货(缺采购时间)"))
        seg_rows.append({
            "dept": "后勤", "seg": f"{it['hub']}·未发货",
            "from": "采购(Part2)", "to": "未发货(Part5)",
            "start": _disp(t_purchase, nd_purchase["src"]),
            "end": "至今未发货",
            "t0": t_purchase, "t1": today if t_purchase else None,
            "src0": nd_purchase["src"], "src1": "系统",
            "days": _fmt_days(stall_days), "target": target,
            "judge": it["judge"], "owner": owner, "executor": owner,
            "note": "Part5渠道已立项但无货件号·超时未发货异常(独立于采购→发货末批)",
            "gap": False, "assessed": False, "noShip": True,
        })

    # 运输汇总 + 4国
    seg_rows.extend(transport_rows(items))

    # 甘特图行按部门归块排序：产品 → 留空 → 运营 → 后勤 → 运输 → 4国
    seg_rows.sort(key=lambda r: G_ORDER.get(r["dept"], 9))
    return seg_rows, nodes, items


def _dept_final(nodes, parts_docs):
    """三部门终节点 → {部门: (时间|None, 说明)}。
    产品=本地仓到货；运营=max(上架,广告)；后勤=最晚渠道仓到货。"""
    result = {}
    t_local = nodes["本地仓到货"]["adopted"]
    result["产品部"] = (t_local, "完成" if t_local else "缺节点: 本地仓到货检查")

    t_up, t_ad = nodes["上架"]["adopted"], nodes["广告"]["adopted"]
    if t_up is None and t_ad is None:
        result["运营部"] = (None, "缺节点: 上架/广告设置")
    else:
        vals = [v for v in (t_up, t_ad) if v is not None]
        missing = [n for n, v in (("上架", t_up), ("广告设置", t_ad)) if v is None]
        result["运营部"] = (max(vals),
                            "完成" if not missing else f"缺节点: {','.join(missing)}")

    arrs = [it["arr"] for it in build_shipments(parts_docs) if it["arr"]]
    if not arrs:                                   # 兜底：Part8 业务时间列
        for d in (parts_docs.get("Part8") or []):
            v = _parse_dt(_clean(d["raw"].get("渠道仓到货时间")))
            if v:
                arrs.append(v)
    result["后勤部"] = (max(arrs) if arrs else None,
                        "完成" if arrs else "缺节点: 渠道仓到货")
    return result


def compute_review():
    """全量计算复盘结果（段级动作映射口径）。"""
    rules, _ = get_rules()
    docs = _load_sku_docs()

    # ---- 按 SKU 组织（同 SKU 跨批次合并；同 Part 重复单据只留最新） ----
    by_sku = {}
    dup_total = 0
    for d in docs:
        rec = by_sku.setdefault(
            d["sku"], {"model": "", "parts": {}, "publish_raw": None})
        if not rec["model"] and d["model"]:
            rec["model"] = d["model"]
        rec["parts"].setdefault(d["part"], []).append(d)
        if d["part"] == "Part1" and rec["publish_raw"] is None:
            pub = d["raw"].get("官方发布时间")
            if pub not in (None, "", "nan"):
                rec["publish_raw"] = pub

    rows = []
    persons = {}          # (executor, seg) -> {"skus": set, "over": set}
    seg_stats = {}        # (seg, dept) -> {"actuals": [], "target": f, "over": 0}
    sku_details = {}
    over_days_all = []    # 全部考核段超标天数（actual - target），供分布直方图
    month_stats = {}      # 段终点月份 -> {"n": 考核段数, "over": 超时段数}

    for sku, info in sorted(by_sku.items()):
        parts_docs = {}
        has_finished = False
        for part, ds in info["parts"].items():
            kept, removed = dedupe_docs(ds)
            dup_total += removed
            kept.sort(key=lambda x: x.get("create_time") or datetime.min,
                      reverse=True)
            parts_docs[part] = kept
            if any(d["status"] == "已结束" for d in kept):
                has_finished = True
        if not has_finished:
            continue                        # 无任何已结束单据的 SKU 不进复盘

        seg_rows, nodes, items = review_sku_segments(sku, parts_docs, rules)

        # 开发类型：提前开发 = 官方发布时间 >= 提案(提交申请)时间
        pub = _parse_dt(info["publish_raw"])
        t_sub = nodes["提案"]["adopted"]
        dev = "提前开发" if (pub and t_sub and pub >= t_sub) else "常规开发"

        dept = _dept_final(nodes, parts_docs)
        dept_times = {k: v[0] for k, v in dept.items()}
        valid = all(t is not None for t in dept_times.values())
        reasons = [f"{k}{v[1]}" for k, v in dept.items() if v[1] != "完成"]

        # 卡点 = 实际耗时最长的非留空段（渠道未发货占位行不计）
        bottle, best_d = None, 0.0
        for r in seg_rows:
            if r["gap"] or r.get("noShip") or r["days"] is None:
                continue
            dv = float(r["days"])
            if dv > best_d:
                best_d, bottle = dv, r

        # 段贡献与统计（留空段/未考核段不计）
        contrib_total, contrib_n = 0.0, 0
        for r in seg_rows:
            if r["gap"] or not r["assessed"] or r["days"] is None:
                continue
            contrib = round(r["target"] - float(r["days"]), 2)
            contrib_total += contrib
            contrib_n += 1
            r["contribution"] = contrib
            key = (r["seg"], r["dept"])
            st = seg_stats.setdefault(key, {"actuals": [], "target": r["target"],
                                            "over": 0, "n": 0, "overDays": []})
            st["actuals"].append(float(r["days"]))
            st["n"] += 1
            if str(r["judge"]).startswith("✗"):
                st["over"] += 1
                over = round(float(r["days"]) - float(r["target"]), 2)
                st["overDays"].append(over)
                over_days_all.append(over)
            # 月度趋势：按段终点月份分桶（达标率随时间的走向）
            t1 = r.get("t1")
            if t1 is not None:
                ms = month_stats.setdefault(t1.strftime("%Y-%m"),
                                            {"n": 0, "over": 0})
                ms["n"] += 1
                if str(r["judge"]).startswith("✗"):
                    ms["over"] += 1
            # 人员统计（有实际执行人才计）
            if r["executor"]:
                pp = persons.setdefault(r["executor"], {})
                pp = pp.setdefault(r["seg"], {"skus": set(), "over": set(),
                                              "dept": r["dept"]})
                pp["skus"].add(sku)
                if str(r["judge"]).startswith("✗"):
                    pp["over"].add(sku)

        rows.append({
            "sku": sku, "model": info["model"], "devType": dev,
            "prodAt": _fmt(dept_times["产品部"]),
            "opAt": _fmt(dept_times["运营部"]),
            "logiAt": _fmt(dept_times["后勤部"]),
            "valid": valid, "invalidReason": "；".join(reasons),
            "bottleneck": ({"seg": bottle["seg"], "dept": bottle["dept"],
                            "days": bottle["days"]} if bottle else None),
            "contributionTotal": round(contrib_total, 2) if contrib_n else None,
            "overSegs": sum(1 for r in seg_rows
                            if not r["gap"] and not r.get("noShip")
                            and str(r["judge"]).startswith("✗")),
            "coveredSegs": sum(1 for r in seg_rows
                               if not r["gap"] and not r.get("noShip")
                               and r["days"] is not None),
            "publishAt": _fmt(pub),
        })
        # 各 Part 单据状态汇总（场景诊断用：Part4 是否已结束、Part5 是否仍有在途）
        part_stat = {}
        for part, ds in parts_docs.items():
            fin = sum(1 for d in ds if d["status"] == "已结束")
            run = sum(1 for d in ds if d["status"] == "审批中")
            term = sum(1 for d in ds if d["status"] == "终止")
            part_stat[part] = {"total": len(ds), "finished": fin,
                               "running": run, "terminated": term,
                               "done": fin > 0}

        # Part5 未结束单据摘要（发货停滞诊断用，最多 8 条）
        p5_pending = [
            {"approvalNo": d["approval_no"],
             "status": d["status"],
             "createAt": _fmt(d["create_time"]),
             "hub": _clean(d["raw"].get("发往渠道仓库")),
             "trk": _clean(d["raw"].get("货件号"))}
            for d in (parts_docs.get("Part5") or [])
            if d["status"] != "已结束"
        ][:8]

        sku_details[sku] = {
            "devType": dev, "valid": valid,
            "dept": {k: {"time": _fmt(v[0]), "note": v[1]}
                     for k, v in dept.items()},
            "nodes": {name: {"at": _disp(nd["adopted"], nd["src"]),
                             "src": nd["src"],
                             "iso": (nd["adopted"].strftime("%Y-%m-%d %H:%M:%S")
                                     if nd["adopted"] else None)}
                      for name, nd in nodes.items()},
            "partStat": part_stat,
            "part5Pending": p5_pending,
            "segments": seg_rows,
            "shipments": [{"trk": it["trk"], "hub": it["hub"],
                           "ship": _fmt(it["ship"]), "arr": _fmt(it["arr"]),
                           "days": _fmt_days(it["days"]), "mode": it["mode"],
                           "target": it["target"], "cls": it.get("cls", ""),
                           "noShip": it.get("noShip", False),
                           "stallDays": it.get("stallDays"),
                           "judge": (it.get("judge")
                                     or ("✓" if it["days"] is not None
                                         and it["days"] <= it["target"]
                                         else ("✗超" if it["days"] is not None
                                               else "—未到货")))}
                          for it in items],
            "bottleneck": ({"seg": bottle["seg"], "dept": bottle["dept"],
                            "days": bottle["days"]} if bottle else None),
        }

    # ---- 汇总 ----
    valid_n = sum(1 for r in rows if r["valid"])
    dev_dist = {}
    for r in rows:
        d = dev_dist.setdefault(r["devType"], {"total": 0, "valid": 0})
        d["total"] += 1
        d["valid"] += 1 if r["valid"] else 0

    bottle_top = {}
    for r in rows:
        if r["valid"] and r["bottleneck"]:
            k = r["bottleneck"]["seg"]
            bottle_top[k] = bottle_top.get(k, 0) + 1

    seg_stat_list = []
    for (seg, dept), st in sorted(seg_stats.items(),
                                  key=lambda kv: (G_ORDER.get(kv[0][1], 9),
                                                  kv[0][0])):
        actuals = sorted(st["actuals"])
        n = len(actuals)
        avg = round(sum(actuals) / n, 2)
        over_ds = sorted(st.get("overDays") or [])
        seg_stat_list.append({
            "seg": seg, "dept": dept, "sampleCount": n,
            "avgActual": avg,
            "medianActual": round(actuals[n // 2], 2),
            "p90Actual": round(actuals[min(n - 1, int(n * 0.9))], 2),
            "maxActual": actuals[-1],
            "target": st["target"],
            "overCount": st["over"],
            "onTimeRate": round(100 * (n - st["over"]) / n, 1),
            "avgOverDays": round(sum(over_ds) / len(over_ds), 1) if over_ds else 0,
            "maxOverDays": round(over_ds[-1], 1) if over_ds else 0,
        })

    person_list = [{"operator": op, "seg": seg, "dept": info["dept"],
                    "skuCount": len(info["skus"]),
                    "overCount": len(info["over"]),
                    "onTimeRate": round(100 * (len(info["skus"])
                                               - len(info["over"]))
                                        / len(info["skus"]), 1)}
                   for op, segs_map in sorted(persons.items())
                   for seg, info in sorted(segs_map.items())]

    # ---- 超标天数分布（0-1 / 1-3 / 3-7 / 7-14 / 14-30 / 30+ 天） ----
    buckets = [("0~1天", 0, 1), ("1~3天", 1, 3), ("3~7天", 3, 7),
               ("7~14天", 7, 14), ("14~30天", 14, 30), ("30天以上", 30, None)]
    over_days_dist = [
        {"label": lb,
         "count": sum(1 for d in over_days_all
                      if d >= lo and (hi is None or d < hi))}
        for lb, lo, hi in buckets
    ]

    # ---- 月度达标趋势（按段终点月份，全考核段） ----
    monthly = [{"month": m,
                "sampleCount": st["n"],
                "overCount": st["over"],
                "onTimeRate": round(100 * (st["n"] - st["over"]) / st["n"], 1)}
               for m, st in sorted(month_stats.items())]

    return {
        "computedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "skuCount": len(rows),
        "duplicateDocsRemoved": dup_total,
        "rows": rows,
        "devDist": dev_dist,
        "bottleneckTop": sorted(
            [{"seg": k, "count": v} for k, v in bottle_top.items()],
            key=lambda x: -x["count"]),
        "segStats": seg_stat_list,
        "persons": person_list,
        "overDaysDist": over_days_dist,
        "monthlyTrend": monthly,
        "skuDetails": sku_details,
    }


# ---------------------------------------------------------------------------
# 缓存（以 batch 表状态为指纹）
# ---------------------------------------------------------------------------
_cache = {"fp": None, "data": None}
_cache_lock = threading.Lock()


def _db_fingerprint():
    with SessionLocal() as session:
        batches = session.execute(
            select(Batch.imported_at, Batch.doc_count)).all()
    if not batches:
        return "empty"
    latest = max((b[0] or datetime.min) for b in batches)
    return f"{len(batches)}|{latest.strftime('%Y%m%d%H%M%S')}|{sum(b[1] or 0 for b in batches)}"


def get_review_data():
    """取复盘全量数据（带锁缓存：指纹一致直接返回；否则持锁重算后原子提交，
    避免并发请求在重算中途拿到旧数据或重复计算）。"""
    from ..rule_service import get_rules as _gr
    fp = f"{_db_fingerprint()}|{_gr()[1]}"
    with _cache_lock:
        if _cache["fp"] == fp and _cache["data"] is not None:
            return _cache["data"]
        data = compute_review()
        _cache["fp"] = fp
        _cache["data"] = data
    # 快照必须在缓存锁外调用：_build_kpi 内部的运输/预警/场景看板会再次
    # 进入 get_review_data（此时命中缓存），锁内重入非重入锁会死锁。
    try:
        from .history import record_snapshot
        record_snapshot(fp, data)
    except Exception:
        pass
    return data


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@router.get("/overview")
def review_overview():
    """大流程复盘总览：KPI + 分布 + 卡点TOP + 段级统计 + 人员 + 全部 SKU 行。"""
    try:
        data = get_review_data()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"复盘计算失败: {e}")
    return {k: v for k, v in data.items() if k != "skuDetails"}


@router.get("/sku/{code}")
def review_sku(code: str):
    """单个 SKU 的复盘档案（段级动作映射明细、货件运输、三部门终节点）。"""
    try:
        data = get_review_data()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"复盘计算失败: {e}")
    detail = data["skuDetails"].get(code)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"SKU 不存在或无已结束单据: {code}")
    return {"sku": code, **detail}


@router.get("/overview/export")
def export_review_overview():
    """导出复盘总览 SKU 行 xlsx（含开发类型/三部门时间/卡点/贡献）。"""
    import io
    import urllib.parse

    import pandas as pd
    from fastapi.responses import StreamingResponse

    try:
        data = get_review_data()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导出失败: {e}")
    cols = ["SKU", "型号", "开发类型", "有效复盘", "无效原因",
            "产品部·本地仓到货", "运营部·上架/广告", "后勤部·渠道仓到货",
            "头号卡点环节", "卡点部门", "卡点耗时(天)", "超时段数",
            "总贡献(天)", "覆盖段数"]
    out = []
    for r in data["rows"]:
        b = r.get("bottleneck")
        out.append({
            "SKU": r["sku"], "型号": r["model"] or "",
            "开发类型": r["devType"],
            "有效复盘": "有效" if r["valid"] else "未完成",
            "无效原因": r["invalidReason"] or "",
            "产品部·本地仓到货": r["prodAt"] or "",
            "运营部·上架/广告": r["opAt"] or "",
            "后勤部·渠道仓到货": r["logiAt"] or "",
            "头号卡点环节": b["seg"] if b else "",
            "卡点部门": b["dept"] if b else "",
            "卡点耗时(天)": b["days"] if b else "",
            "超时段数": r["overSegs"],
            "总贡献(天)": r["contributionTotal"]
            if r["contributionTotal"] is not None else "",
            "覆盖段数": r["coveredSegs"],
        })
    df = pd.DataFrame(out, columns=cols).fillna("")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="复盘总览", index=False)
    buf.seek(0)
    filename = urllib.parse.quote("大流程复盘总览.xlsx")
    return StreamingResponse(buf, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
    }, media_type="application/vnd.openxmlformats-"
                   "officedocument.spreadsheetml.sheet")


# ---------------------------------------------------------------------------
# 单 SKU 复盘报告（markdown，一键下载）
# ---------------------------------------------------------------------------
def _sku_report_md(code: str, data: dict) -> str:
    """按实际动作映射口径，为一个 SKU 生成 markdown 复盘报告。"""
    detail = data["skuDetails"][code]
    row = next((r for r in data["rows"] if r["sku"] == code), {})
    segs, dept, dev = detail["segments"], detail["dept"], detail["devType"]
    valid = detail["valid"]
    miss = "；".join(f"{k}{v['note']}" for k, v in dept.items() if v["note"] != "完成")

    L = []
    L.append(f"# {code} 复盘报告")
    L.append("")
    L.append(f"- **型号**：{row.get('model') or '—'}")
    L.append(f"- **开发类型**：{dev}"
             + (f"（官方发布 {row.get('publishAt') or '—'}）" if row.get("publishAt") else ""))
    L.append(f"- **复盘结论**：{'✅ 有效复盘' if valid else '❌ 未闭环'}"
             + ("" if valid else f"（{miss}）"))
    L.append(f"- **生成时间**：{data['computedAt']}（实际动作映射口径：系统列 > audit > 审批动作）")
    L.append("")

    # 三部门终节点
    L.append("## 一、三部门终节点")
    L.append("")
    L.append("| 部门 | 最终工作节点 | 完成时间 | 状态 |")
    L.append("|---|---|---|---|")
    final_nodes = {"产品部": "本地仓到货检查(Part3)",
                   "运营部": "最终上架与广告设置完成(Part7)",
                   "后勤部": "渠道仓到货(Part5×Part8)"}
    for name in ("产品部", "运营部", "后勤部"):
        t, note = dept[name]["time"], dept[name]["note"]
        L.append(f"| {name} | {final_nodes[name]} | {t or '—'} |"
                 f"{'✅ 完成' if note == '完成' else '❌ ' + note} |")
    L.append("")

    # 段级明细
    L.append("## 二、段级明细（实际动作映射 × 目标）")
    L.append("")
    L.append("| 部门 | 环节 | 起点 | 终点 | 耗时(天) | 目标(天) | 判定 | 执行人 |")
    L.append("|---|---|---|---|---|---|---|---|")
    contrib_sum, contrib_n = 0.0, 0
    for s in segs:
        if s.get("gap"):
            L.append(f"| 留空 | {s['seg']} | {s.get('start') or '—'} |"
                     f" {s.get('end') or '—'} | — | — | —留空 | — |")
            continue
        d = s.get("days")
        tgt = s.get("target")
        contrib = s.get("contribution")
        if contrib is not None:
            contrib_sum += contrib
            contrib_n += 1
        j = s.get("judge", "")
        j_disp = f" {j}" if j else ""
        L.append(
            f"| {s['dept']} | {s['seg']} | {s.get('start') or '—'} |"
            f" {s.get('end') or '—'} | {d if d is not None else '—'} |"
            f" {tgt if tgt is not None else '—'} |"
            f" {j_disp} | {s.get('executor') or '—'} |")
    L.append("")
    if contrib_n:
        L.append(f"**总贡献：{contrib_sum:+.2f} 天**"
                 f"（{'超前于目标' if contrib_sum >= 0 else '落后于目标'}，共 {contrib_n} 个考核段计入）")
        L.append("")

    # 卡点
    bottle = detail.get("bottleneck")
    if bottle:
        L.append("## 三、头号卡点")
        L.append("")
        L.append(f"- **{bottle['dept']} {bottle['seg']}** — **{bottle['days']} 天**")
        L.append("")

    # 慢段明细（超 3 天标出）
    L.append("## 四、慢段明细（仅列出 ≥3 天的段）")
    L.append("")
    hot = [s for s in segs
           if not s.get("gap") and s.get("days") is not None
           and float(s["days"]) >= 3]
    if hot:
        L.append("| 部门 | 环节 | 起点 | 终点 | 耗时(天) | 判定 |")
        L.append("|---|---|---|---|---|---|")
        for s in sorted(hot, key=lambda x: -float(x["days"])):
            L.append(f"| {s['dept']} | {s['seg']} | {s.get('start') or '—'} |"
                     f" {s.get('end') or '—'} | **{s['days']}** | {s.get('judge', '')} |")
    else:
        L.append("无 ≥3 天的段，流程衔接良好。")
    L.append("")

    # 货件运输
    ships = detail.get("shipments") or []
    bad_ships = [s for s in ships if str(s.get("judge", "")).startswith("✗")]
    if ships:
        L.append("## 五、货件运输")
        L.append("")
        L.append(f"共 {len(ships)} 票，超时 {len(bad_ships)} 票。")
        if bad_ships:
            L.append("")
            L.append("| 货件号 | 渠道仓 | 发货 | 到货 | 运输(天) | 目标(天) | 判定 |")
            L.append("|---|---|---|---|---|---|---|")
            for s in bad_ships:
                L.append(f"| {s['trk']} | {s['hub'] or '—'} | {s['ship'] or '—'} |"
                         f" {s['arr'] or '—'} | {s['days'] if s['days'] is not None else '—'} |"
                         f" {s['target']} | ✗ |")
        L.append("")

    # 改进建议（超时段）
    over_segs = [s for s in segs
                 if not s.get("gap") and str(s.get("judge", "")).startswith("✗")]
    if over_segs:
        L.append("## 六、改进建议")
        L.append("")
        for s in sorted(over_segs,
                        key=lambda x: -(float(x["days"] or 0) - (x["target"] or 0))):
            L.append(f"1. **{s['dept']} {s['seg']}**：实际 {s['days']} 天 /"
                     f" 目标 {s['target']} 天。"
                     f"（{s.get('note') or '—'}；执行人：{s.get('executor') or '—'}）")
        L.append("")

    L.append("---")
    L.append("*本报告由数据后台系统自动生成，口径：实际动作映射（seg_rules），"
             "节点时间=系统业务列 > audit上报 > 审批动作。*")
    return "\n".join(L)


@router.get("/sku/{code}/report")
def sku_report(code: str):
    """单 SKU 复盘报告（markdown 下载）。"""
    try:
        data = get_review_data()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"复盘计算失败: {e}")
    if code not in data["skuDetails"]:
        raise HTTPException(status_code=404, detail=f"SKU 不存在或无已结束单据: {code}")
    md = _sku_report_md(code, data)
    import io
    import urllib.parse

    from fastapi.responses import StreamingResponse
    buf = io.BytesIO(md.encode("utf-8"))
    filename = urllib.parse.quote(f"{code}_复盘报告.md")
    return StreamingResponse(buf, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
    }, media_type="text/markdown; charset=utf-8")
