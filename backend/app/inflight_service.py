# -*- coding: utf-8 -*-
"""
inflight_service.py — 事中引擎（P0-4 数据面 / P0-7 清单 / P1-1 进度追踪 / P1-2 段级预警）
========================================================================================
定位：复盘（review）回答"已经发生了什么"；本模块回答**"现在卡在哪、谁该动"**。

与 review 的关系：
  · 复用的部分 —— 三部门已完结或单据齐全的 SKU 直接取 `get_review_data()` 的
    skuDetails（段级已算好，零重复计算）；
  · 补算的部分 —— **无任何已结束单据的 SKU 不进复盘**（compute_review 会跳过），
    但它们恰恰是最需要事中视角的（单据建了、活没人干），这里用同一套
    `review_sku_segments` 现算段级；
  · 新增的部分 —— 段状态机（§3.1：未开始/进行中/预警/超期/已完成）、
    每日节点快照与 diff（§2.3/P1-1）、当日异常上报清单（P0-7）、日报落盘（P0-4）。

存储：
  · 每日节点快照 `backend/state/inflight_snapshots.json`（照 kpi_snapshots 模式）：
        [{date, at, fingerprint, category, nodes: {sku: {节点名: iso}}}])
    每品类每日一张；同日指纹变化 → 替换当日张（保持每日一张最新）；保留 365 天。
  · 日报索引 `backend/state/daily_reports_index.json`：
        [{date, category, path, skuCount, overdueCount, warnCount, createdAt}]

口径注意：
  · 上报清单只针对**进行中**的超期段（终点没时间、elapsed > target）——
    已完成但 ✗ 的段属"事后未达标"，复盘页负责，上报了也没人能改历史；
  · 快照只存**在研 SKU** 的节点：已完结 SKU 不再有新完成节点，diff 永远为空；
  · 「不再追踪」豁免（2026-10-06 与加急预警/单据增量合并口径）：
    人工登记 > 换号 > 末端终止（作废单）> 正常追踪，判定单一真源 = app/exemption.py。
    旧实现只有「全部单据都终止」才排除，导致人工登记（G495/G524~G528）、
    已换号旧号、末端终止（前面 Part 已结束、末尾 Part 全终止）的 SKU
    全部混进「在研 SKU 状态总表」——终止的单据也在被追踪。
    豁免 SKU 移出追踪后不吞数据：items 之外单列 exempted 备查（日报出「豁免备查」sheet）。
"""

import json
import os
import threading
from datetime import date, datetime, timedelta

from . import exemption
from .category_rules import rules_for
from .config import STATE_DIR, list_batches
from .transport_closure import (classify_transport, is_transport_segment,
                               transport_exempt_reason)

_SNAP_FILE = STATE_DIR / "inflight_snapshots.json"
_REPORT_INDEX = STATE_DIR / "daily_reports_index.json"
_SNAP_RETENTION_DAYS = 365

# 日报落盘名前缀。原为「复盘报告_」，与单 SKU 的「复盘报告 md」撞名，而内容其实
# 是**事中日报**（5 sheets）——2026-09-24 改名，语义对齐。
_REPORT_PREFIX = "事中日报_"
# 改名前的旧前缀：同批次目录里同一天的旧名文件会在生成时随迁删除，
# 否则索引条目一换，旧文件就永远没人再引用（既不会被保留期清理，也不会被下载）。
_LEGACY_REPORT_PREFIX = "复盘报告_"
# RLock：maybe_snapshot（接口）与 auto_pipeline（定时）可能并发拍快照；
# generate_daily_report 内部也会再取 inflight 数据（可重入）。
_lock = threading.RLock()

# ---------------------------------------------------------------------------
# 段状态机（§3.1 · P1-2）
# ---------------------------------------------------------------------------
# 状态取值：未开始 / 进行中 / 预警 / 超期 / 已完成 / 留空
ST_PENDING = "未开始"
ST_RUNNING = "进行中"
ST_WARN = "预警"
ST_OVER = "超期"
ST_DONE = "已完成"
ST_GAP = "留空"


def seg_state(row, today=None, ratio=0.85):
    """对一段行（review_sku_segments 产出）做事中状态判定。

    today 可注入（测试用）；缺省 = 本地今天。
    返回 dict：{state, elapsed, target, warnLine, over}；
      elapsed = 已进行天数（today − t0，仅未完成段有）；
      over    = 超期天数（elapsed − target，仅超期段 > 0）。
    不误报已完成段：终点已有时间的段判 ✓/✗（judge 已有），不进预警区。
    noShip 行（渠道未发货异常）终点恒为"至今"，按进行中口径累计。
    """
    if row.get("gap"):
        return {"state": ST_GAP, "elapsed": None, "target": None,
                "warnLine": None, "over": None}
    target = row.get("target")
    if today is None:
        today = date.today()
    # 已完成：终点有时间（noShip 行的 t1 是"今天"占位，视作未完成）
    if row.get("t1") is not None and not row.get("noShip"):
        return {"state": ST_DONE, "elapsed": None, "target": target,
                "warnLine": None, "over": None}
    t0 = row.get("t0")
    if t0 is None:
        return {"state": ST_PENDING, "elapsed": None, "target": target,
                "warnLine": None, "over": None}
    elapsed = max((today - t0.date()).days, 0)
    if target is None or not row.get("assessed", True):
        # 未配置目标/未考核段：只报进度，不做阈值判定
        return {"state": ST_RUNNING, "elapsed": elapsed, "target": None,
                "warnLine": None, "over": None}
    warn_line = round(float(target) * float(ratio), 2)
    if elapsed > float(target):
        return {"state": ST_OVER, "elapsed": elapsed, "target": target,
                "warnLine": warn_line,
                "over": round(elapsed - float(target), 2)}
    if elapsed > warn_line:
        return {"state": ST_WARN, "elapsed": elapsed, "target": target,
                "warnLine": warn_line, "over": None}
    return {"state": ST_RUNNING, "elapsed": elapsed, "target": target,
            "warnLine": warn_line, "over": None}


def _seg_public(row, st):
    """段行 → 对外结构（详情页/日报共用）。

    t0/t1 以 ISO 字符串输出（datetime 过不了 JSON）；甘特图与前端时间轴都靠它。
    days 为复盘侧算好的耗时展示串（_fmt_days 产物）。
    """
    t0, t1 = row.get("t0"), row.get("t1")
    return {
        "dept": row.get("dept"), "seg": row.get("seg"),
        "from": row.get("from"), "to": row.get("to"),
        "state": st["state"],
        "start": row.get("start"), "end": row.get("end"),
        "days": row.get("days"),
        "t0": (t0.strftime("%Y-%m-%d %H:%M:%S") if t0 else None),
        "t1": (t1.strftime("%Y-%m-%d %H:%M:%S") if t1 else None),
        "elapsed": st["elapsed"], "target": st["target"],
        "warnLine": st["warnLine"], "over": st["over"],
        "judge": row.get("judge"),
        "owner": row.get("owner"), "executor": row.get("executor"),
        "gap": bool(row.get("gap")), "noShip": bool(row.get("noShip")),
    }


def _iso_date(v):
    """datetime/date → 'YYYY-MM-DD'；字符串原样；None/'' → None。

    进 JSON 前统一转字符串，**绝不**让 datetime 泄漏进响应。
    """
    if v is None or v == "":
        return None
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m-%d")
    return str(v)


def _iso_ticket(t):
    """运输在途票 → 可 JSON 化结构（日期转字符串，其余原值）。"""
    return {
        "hub": t.get("hub"),
        "trk": t.get("trk"),
        "ship": _iso_date(t.get("ship")),
        "arr": _iso_date(t.get("arr")),
        "state": t.get("state"),
    }


def _build_detail(sku, info, *, detail, segs_out, nodes_iso, part_stat,
                  multi_round, root_cause, status, closure, closure_info,
                  pending_countries, transport_exempt, over_list, warn_list,
                  ratio):
    """构造单 SKU 的事中详情（**在研与免追踪共用同一形状**，供「事中档案」回查）。

    ⚠️ over_list / warn_list 由调用方在「剔除运输域卡点」之后再传入 ——
    运输域不参与卡点判定时，运输域段不得出现在超期/预警里。
    ⚠️ transportExempt 只表示「运输域不计卡点」，**闭环态一律看 shipClosure**。
    """
    return {
        "sku": sku, "model": info["model"],
        "firstProposalAt": detail.get("firstProposalAt") if detail else None,
        "status": status, "multiRound": multi_round,
        "rootCause": root_cause,
        "segments": segs_out, "nodes": nodes_iso,
        "partStat": part_stat,
        "shipClosure": closure,
        "pendingCountries": pending_countries,
        "transportExempt": transport_exempt,
        "transportReason": transport_exempt_reason(
            closure, closure_info["pendingHubs"],
            closure_info["notShippedHubs"]),
        "warnings": [{"dept": w[1].get("dept"), "seg": w[1].get("seg"),
                      "elapsed": w[2]["elapsed"], "target": w[2]["target"],
                      "ratio": ratio, "owner": w[1].get("executor")
                      or w[1].get("owner") or ""}
                     for w in warn_list],
        "overdues": [{"dept": o[1].get("dept"), "seg": o[1].get("seg"),
                      "elapsed": o[2]["elapsed"], "target": o[2]["target"],
                      "overDays": o[2]["over"], "owner": o[1].get("executor")
                      or o[1].get("owner") or ""}
                     for o in over_list],
    }


def _pick_stay(state_rows):
    """当前停留环节判定（2026-10-06：总表「最差环节」改判「当前停留」）。

    state_rows：[(段行, seg_state 结果)]。
    定义：**未完成段（进行中/预警/超期）里最近启动（t0 最新）的那一段**——
    流程是顺序推进的，最近启动而没走完的段就是当前卡住的地方；
      · 全部未开始 → 停在第一段（已停留 0 天，距超时 = 目标天）；
      · 全部完成但仍未闭环（缺终点节点等复盘口径）→ 取最晚完成的段，
        距超时 = None（没有"进行中"的时钟可算）。
    返回 (段行|None, st|None, days_left|None)；days_left = 目标 − 已停留，
    **负数 = 已超期 N 天**。
    """
    started = [(r.get("t0"), r, st) for r, st in state_rows
               if st["state"] in (ST_RUNNING, ST_WARN, ST_OVER) and r.get("t0")]
    if started:
        _, row, st = max(started, key=lambda x: x[0])
    else:
        pend = [(r, st) for r, st in state_rows if st["state"] == ST_PENDING]
        if pend:
            row, st = pend[0]
        else:
            done = [(r.get("t1"), r, st) for r, st in state_rows
                    if st["state"] == ST_DONE and r.get("t1")]
            if done:
                _, row, st = max(done, key=lambda x: x[0])
            else:
                row = st = None
    days_left = None
    if st is not None and st.get("target") is not None:
        if st["state"] == ST_PENDING:
            days_left = round(float(st["target"]), 1)
        elif st["state"] in (ST_RUNNING, ST_WARN, ST_OVER):
            days_left = round(float(st["target"]) - float(st["elapsed"] or 0), 1)
    return row, st, days_left


# ---------------------------------------------------------------------------
# 全量在研计算
# ---------------------------------------------------------------------------
def _dedupe_parts(info):
    """按 SKU 组织 parts_docs（与 compute_review 同规则：同 Part 去重留最新）。"""
    from .routers.review import dedupe_docs
    parts_docs = {}
    for part, ds in info["parts"].items():
        kept, _removed = dedupe_docs(ds)
        kept.sort(key=lambda x: x.get("create_time") or datetime.min,
                  reverse=True)
        parts_docs[part] = kept
    return parts_docs


def _load_all(category):
    """全量单据按 SKU 组织 —— **不跳过**无已结束单据的 SKU（与 compute_review 的差别）。"""
    from .routers.review import _load_sku_docs
    docs = _load_sku_docs(category)
    by_sku = {}
    for d in docs:
        rec = by_sku.setdefault(
            d["sku"], {"model": "", "parts": {}, "has_finished": False,
                       "all_terminated": True})
        if not rec["model"] and d["model"]:
            rec["model"] = d["model"]
        rec["parts"].setdefault(d["part"], []).append(d)
        if d["status"] == "已结束":
            rec["has_finished"] = True
        if d["status"] != "终止":
            rec["all_terminated"] = False
    return by_sku


def compute_inflight(category=None):
    """事中全量计算。category 缺省 = 默认品类（手机膜）。

    返回 {computedAt, **describe, inResearchCount, overdueSkuCount, warnCount,
          multiRoundCount, todayNewNodes, items, skuDetails, exemptCount, exempted}
    items 项：{sku, model, staySeg, staySegDept, stayElapsed, stayTarget,
               stayDaysLeft, warnSegs, status, multiRound, rootCause, owner}
      stayDaysLeft = 距超时天数（目标 − 已停留；负 = 已超期 N 天）。
      2026-10-06 起总表主判定从「最差环节」（历史超得最狠的段）改为
      「当前停留环节」（最近启动没走完的段）——催办要的是"现在卡在哪"。
    exempted 项：{sku, model, kind, reason} —— 被豁免移出追踪的 SKU（备查不吞数据）。
    结果按品类缓存（指纹 = 复盘指纹，规则热调/导入自动失效）。
    """
    from .routers.review import (build_shipments, fingerprint_for,
                         get_review_data, resolve_part1_rounds,
                         review_sku_segments)
    from .causal import locate as _locate_root_cause
    from .rule_service import get_rules

    rs = rules_for(category)
    rules, _rule_ver = get_rules()
    ratio = float((rules.get("inflight_warn_ratio") or 0.85))
    review_data = get_review_data(rs.key)          # 复用复盘缓存（会触发 kpi 补拍）
    details = review_data["skuDetails"]
    by_sku = _load_all(rs.key)
    today = date.today()
    holds, aliases = exemption.load_registries()

    items = []
    inflight_details = {}          # 在研 SKU 的事中详情（sku/{code} 用）
    snap_nodes = {}                # 快照用：{sku: {node: iso}}
    exempted = []                  # 豁免备查（不进追踪，也不吞数据）
    transport_exempted = []        # 运输闭环免追踪备查（不吞数据）
    transport_in_transit = []      # 运输在途·逐票（前端④：有未到货票 / 有未发货渠道的 SKU）

    for sku, info in sorted(by_sku.items()):
        if info["all_terminated"]:
            continue                # 全部单据终止 = 流程作废（exemption.void 的
                                    # 超集短路，保留只为省一次 classify）
        # ---- 豁免判定（单一真源 exemption.classify，与加急预警同口径）----
        # 判定次序（2026-10）：人工登记 > 换号 > 末端终止 > 运输闭环 > 正常催办。
        # 前三者在此短路；运输闭环在段状态算完后的下游判定（见下方「运输闭环」段）。
        kind, ex_info = exemption.classify(sku, info["parts"], holds=holds,
                                           aliases=aliases)
        if kind != "active":
            exempted.append({"sku": sku, "model": info["model"],
                             "kind": kind,
                             "reason": exemption.exempt_reason(kind, ex_info)})
            continue

        # 去重后的全量单据（新算段级与货件配对共用一份，避免重复去重）
        parts_docs = _dedupe_parts(info)
        detail = details.get(sku)
        if detail is not None:
            seg_rows = detail["segments"]
            nodes_iso = {n: (nd.get("iso") if isinstance(nd, dict) else None)
                         for n, nd in (detail.get("nodes") or {}).items()}
            multi_round = detail.get("multiRound") or {}
            root_cause = detail.get("rootCause")
            part_stat = detail.get("partStat") or {}
        else:
            # 无任何已结束单据：现算段级（这正是事中视角的主战场）
            if parts_docs:
                _p1_base, _p1_rounds, multi_round = resolve_part1_rounds(
                    parts_docs.get("Part1") or [], ruleset=rs)
                seg_rows, nodes, _items = review_sku_segments(
                    sku, parts_docs, rules, ruleset=rs)
                root_cause = _locate_root_cause(seg_rows, nodes, parts_docs,
                                                multi_round=multi_round,
                                                ruleset=rs)
                nodes_iso = {n: (nd["adopted"].strftime("%Y-%m-%d %H:%M:%S")
                                 if nd.get("adopted") else None)
                             for n, nd in nodes.items()}
                part_stat = {}
                for part, ds in parts_docs.items():
                    part_stat[part] = {
                        "total": len(ds),
                        "finished": sum(1 for d in ds if d["status"] == "已结束"),
                        "running": sum(1 for d in ds if d["status"] == "审批中"),
                        "terminated": sum(1 for d in ds if d["status"] == "终止"),
                        "done": any(d["status"] == "已结束" for d in ds)}
            else:
                seg_rows, nodes_iso = [], {}
                multi_round, root_cause, part_stat = {}, None, {}

        # ---- 段状态机 + 汇总 ----
        segs_out, warn_list, over_list, state_rows = [], [], [], []
        for r in seg_rows:
            st = seg_state(r, today=today, ratio=ratio)
            state_rows.append((r, st))
            segs_out.append(_seg_public(r, st))
            if st["state"] == ST_OVER:
                over_list.append((st["over"], r, st))
            elif st["state"] == ST_WARN:
                warn_list.append((st["elapsed"] - (st["warnLine"] or 0), r, st))

        over_list.sort(key=lambda x: -x[0])
        warn_list.sort(key=lambda x: -x[0])
        any_running = any((p or {}).get("running") for p in part_stat.values())
        in_research = (detail is None) or (not detail.get("valid")) or any_running
        if not in_research:
            continue                # 已完结（三部门全完成且无审批中单据）→ 事后域

        # ---- 运输闭环（判定在豁免三通道之外的下游）----
        # 判定次序：人工登记 > 换号 > 末端终止（上面已短路）> 运输闭环 > 催办。
        # 业务口径（2026-10 用户逐字确认）：**在售品** —— 所有段都已推进到位（无任何在研段，
        # 运输域也算）+ 运输无未到货（closure != "pending"）→ 物流到货不是可催办动作，
        # 移出 items/在研计数/超期预警计数（单列备查）。
        # ⚠️ 反向边界（必须守住）：只要还有任一段处在「未开始 / 进行中 / 预警 / 超期」——
        # 尤其是运输域「·未发货」占位段（Part5 已立项渠道但压根没发货）——就**不是**在售品，
        # 必须保留追踪（G597 那种"发货期限没过、都还没发货"的，绝不能丢开在研）。
        shipments = build_shipments(parts_docs, rs)
        closure_info = classify_transport(shipments)
        closure = closure_info["closure"]
        l4_name = rs.l4[1]

        _active_states = (ST_RUNNING, ST_WARN, ST_OVER, ST_PENDING)
        # 非运输域的在研段（state ∈ 未开始/进行中/预警/超期 且不属运输域）
        non_transport_active = any(
            st["state"] in _active_states
            and not is_transport_segment(r.get("dept"), r.get("seg"), l4_name)
            for r, st in state_rows)
        # 运输域的在研段（含「·未发货」占位段：它"在跑"就说明还没发货）
        transport_active = any(
            st["state"] in _active_states
            and is_transport_segment(r.get("dept"), r.get("seg"), l4_name)
            for r, st in state_rows)
        any_active = non_transport_active or transport_active

        pending_countries = []
        transport_exempt = False

        # 运输在途·逐票（前端④）：有未到货票 / 有未发货渠道的 SKU，供催物流
        if closure == "pending" or closure_info["notShippedHubs"]:
            transport_in_transit.append({
                "sku": sku, "model": info["model"], "closure": closure,
                "tickets": [_iso_ticket(t) for t in closure_info["tickets"]],
                "unshippedHubs": list(closure_info["notShippedHubs"]),
            })

        if closure == "pending":
            # 有已发货票未到货 → 保留追踪，并标注未到货国家（必须展示）
            pending_countries = list(closure_info["pendingHubs"])
        elif state_rows and not any_active:
            # 在售品：所有段已完成 + 运输无未到货 → 免追踪（移出 items/在研计数/
            # 超期预警计数，单列备查）。⚠️ detail **仍存进 skuDetails**，
            # 保证「事中档案」GET /api/inflight/sku/{code} 可随时回查（用户明确要求）。
            # 不进 _snap_nodes（快照只针对在研 SKU）。
            # ⚠️ state_rows 非空守卫：拿不到任何段（段模板缺失）时无法判定"全段完成"，
            # 保守起见**不免** —— 绝不把"没有段信息"当成"在售品"而静默丢弃。
            det = _build_detail(
                sku, info, detail=detail, segs_out=segs_out, nodes_iso=nodes_iso,
                part_stat=part_stat, multi_round=multi_round,
                root_cause=root_cause, status=ST_DONE, closure=closure,
                closure_info=closure_info, pending_countries=pending_countries,
                transport_exempt=True, over_list=over_list, warn_list=warn_list,
                ratio=ratio)
            det["exemptKind"] = "运输闭环"
            inflight_details[sku] = det
            transport_exempted.append({
                "sku": sku, "model": info["model"], "kind": "运输闭环",
                "closure": closure,
                "shippedCount": closure_info["shippedCount"],
                "arrivedCount": closure_info["arrivedCount"],
                "notShippedHubs": list(closure_info["notShippedHubs"]),
                "reason": transport_exempt_reason(
                    closure, closure_info["pendingHubs"],
                    closure_info["notShippedHubs"]),
            })
            continue
        elif non_transport_active:
            # 运输已闭环但仍有非运输在研段（素材/上架等仍在跑）→ 仍在研；
            # 运输域段不再是卡点：从超期/预警中剔除后重算 status（行打标 transportExempt）。
            over_list = [o for o in over_list
                         if not is_transport_segment(o[1].get("dept"),
                                                     o[1].get("seg"), l4_name)]
            warn_list = [w for w in warn_list
                         if not is_transport_segment(w[1].get("dept"),
                                                     w[1].get("seg"), l4_name)]
            transport_exempt = True
        # else：transport_active 且无其它在研段 → 运输域是卡点（如 G597 未发货）→
        # 原样保留追踪，transport_exempt 保持 False。

        # ---- 当前停留环节（总表主判定，2026-10-06 起替代「最差环节」）----
        # 最差环节 = 历史上超得最狠的段，回答"哪一段烂过"；
        # 当前停留 = 最近启动没走完的段，回答"现在卡在哪、还有几天到期"。
        # 催办要的是后者。超期/预警计数仍用 over_list/warn_list（状态列与预警段数）。
        # 运输已闭环时运输域段不再是卡点 → 停留也从非运输段里选。
        stay_src = state_rows
        if transport_exempt:
            stay_src = [(r, st) for r, st in state_rows
                        if not is_transport_segment(r.get("dept"),
                                                    r.get("seg"), l4_name)]
        stay_row, stay_st, stay_left = _pick_stay(stay_src or state_rows)
        stay_elapsed = None
        if stay_st is not None:
            stay_elapsed = stay_st.get("elapsed")
            if stay_elapsed is None and stay_st["state"] == ST_PENDING:
                stay_elapsed = 0            # 停在还没开始的段：已停留 0 天

        status = ST_OVER if over_list else (ST_WARN if warn_list else ST_RUNNING)
        owner = ((stay_row or {}).get("executor")
                 or (stay_row or {}).get("owner") or "")
        items.append({
            "sku": sku, "model": info["model"],
            "staySeg": (stay_row or {}).get("seg"),
            "staySegDept": (stay_row or {}).get("dept"),
            "stayElapsed": stay_elapsed,
            "stayTarget": (stay_st or {}).get("target"),
            "stayDaysLeft": stay_left,      # 距超时天数（负 = 已超期 N 天）
            "warnSegs": [w[1].get("seg") for w in warn_list],
            "status": status, "multiRound": bool(multi_round.get("flag")),
            "rootCause": root_cause, "owner": owner,
            "shipClosure": closure,                   # 运输闭环态（closed/pending/none）
            "pendingCountries": pending_countries,    # 运输未闭环：未到货国家清单
            "notShippedHubs": list(closure_info["notShippedHubs"]),  # 未发货渠道
            "shipCounts": {                           # 已发/到货/在途票数
                "shipped": closure_info["shippedCount"],
                "arrived": closure_info["arrivedCount"],
                "pending": closure_info["pendingCount"],
            },
            "transportExempt": transport_exempt,      # 运输已闭环但仍盯其它段
        })
        inflight_details[sku] = _build_detail(
            sku, info, detail=detail, segs_out=segs_out, nodes_iso=nodes_iso,
            part_stat=part_stat, multi_round=multi_round, root_cause=root_cause,
            status=status, closure=closure, closure_info=closure_info,
            pending_countries=pending_countries, transport_exempt=transport_exempt,
            over_list=over_list, warn_list=warn_list, ratio=ratio)
        snap_nodes[sku] = {k: v for k, v in nodes_iso.items() if v}

    # 最紧急的排前：距超时升序（负=已超期最先），无目标的沉底；同分比预警段数
    items.sort(key=lambda x: (x["stayDaysLeft"] is None,
                              x["stayDaysLeft"] if x["stayDaysLeft"] is not None else 0,
                              -len(x["warnSegs"]), x["sku"]))
    return {
        "computedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        **rs.describe(),
        "warnRatio": ratio,
        "inResearchCount": len(items),
        "overdueSkuCount": sum(1 for x in items if x["status"] == ST_OVER),
        "warnCount": sum(1 for x in items if x["status"] == ST_WARN),
        "multiRoundCount": sum(1 for x in items if x["multiRound"]),
        "items": items,
        "skuDetails": inflight_details,
        "exemptCount": len(exempted),
        "exempted": exempted,
        # 运输闭环免追踪（在售品：全段完成且运输无未到货；不进 items/在研计数，
        # 单列备查；日报「豁免备查」sheet 同源）
        "transportExemptCount": len(transport_exempted),
        "transportExempted": transport_exempted,
        # 运输在途·逐票（前端④催物流）：有未到货票 / 有未发货渠道的 SKU
        "transportInTransit": transport_in_transit,
        "transportDef": {
            "l4Name": rs.l4[1],
            "note": "在售品（所有段已完成且运输无未到货）→ 运输域不再是卡点，免追踪",
            "source": "app/transport_closure.classify_transport",
        },
        "_snap_nodes": snap_nodes,      # 快照用（respond 时剔除，见 routers/inflight）
    }


# ---------------------------------------------------------------------------
# 缓存（指纹 = 复盘指纹：导入/规则热调自动失效）
# ---------------------------------------------------------------------------
_cache_by_cat = {}
_cache_lock = threading.Lock()


def get_inflight_data(category=None):
    cat = rules_for(category).key
    from .routers.review import fingerprint_for
    fp = fingerprint_for(cat)
    with _cache_lock:
        slot = _cache_by_cat.get(cat)
        if slot is not None and slot["fp"] == fp:
            return slot["data"]
        data = compute_inflight(cat)
        _cache_by_cat[cat] = {"fp": fp, "data": data}
        return data


def invalidate(category=None):
    """外部强制失效（auto_pipeline 导入后调用；category=None 清全部）。"""
    with _cache_lock:
        if category is None:
            _cache_by_cat.clear()
        else:
            _cache_by_cat.pop(rules_for(category).key, None)


# ---------------------------------------------------------------------------
# 每日节点快照（P1-1 数据底座，§2.3）
# ---------------------------------------------------------------------------
def _load_snaps():
    if not _SNAP_FILE.exists():
        return []
    try:
        with open(_SNAP_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_snaps(snaps):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _SNAP_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snaps, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _SNAP_FILE)


def _prune(snaps):
    """保留最近 365 天。"""
    cutoff = (datetime.now() - timedelta(days=_SNAP_RETENTION_DAYS)).strftime(
        "%Y-%m-%d")
    return [s for s in snaps if str(s.get("date", "")) >= cutoff]


def record_inflight_snapshot(category, fingerprint, nodes_map=None,
                             force=False):
    """拍一张当日节点快照（每品类每日一张；同日异指纹 → 替换当日张）。

    nodes_map：{sku: {节点名: iso}}；None → 现算（get_inflight_data）。
    """
    cat = rules_for(category).key
    if nodes_map is None:
        nodes_map = get_inflight_data(cat).get("_snap_nodes") or {}
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    with _lock:
        snaps = _prune(_load_snaps())
        for i, s in enumerate(snaps):
            if s.get("category") == cat and s.get("date") == today:
                if not force and s.get("fingerprint") == fingerprint \
                        and s.get("nodes") == nodes_map:
                    return s
                snaps[i] = {"date": today,
                            "at": now.strftime("%H:%M"),
                            "fingerprint": fingerprint,
                            "category": cat, "nodes": nodes_map}
                _save_snaps(snaps)
                return snaps[i]
        snap = {"date": today, "at": now.strftime("%H:%M"),
                "fingerprint": fingerprint, "category": cat,
                "nodes": nodes_map}
        snaps.append(snap)
        _save_snaps(snaps)
        return snap


def maybe_inflight_snapshot(category=None):
    """当日首次访问 inflight 接口时补拍（auto_pipeline / 手动也可调）。"""
    from .routers.review import fingerprint_for
    cat = rules_for(category).key
    fp = fingerprint_for(cat)
    today = datetime.now().strftime("%Y-%m-%d")
    with _lock:
        for s in reversed(_load_snaps()):
            if s.get("category") == cat and s.get("date") == today:
                if s.get("fingerprint") == fp:
                    return s
                break               # 当日有旧指纹快照 → 落到下面重拍（替换）
        return record_inflight_snapshot(cat, fp)


def _snaps_of(category, snaps=None):
    cat = rules_for(category).key
    snaps = _load_snaps() if snaps is None else snaps
    return sorted((s for s in snaps if s.get("category") == cat),
                  key=lambda s: s.get("date", ""))


def progress_for(category=None, sku=None, days=7):
    """最近 N 张快照逐日 diff：newNodes = 当日有值 ∧ 前一日无值（P1-1）。

    sku=None → 全部在研 SKU 的合并视图。
    """
    snaps = _snaps_of(category)[-max(int(days), 1):]
    out = []
    prev_nodes = {}
    for s in snaps:
        cur_nodes = s.get("nodes") or {}
        if sku is not None:
            cur_nodes = {sku: cur_nodes.get(sku) or {}}
            prev_nodes = {sku: prev_nodes.get(sku) or {}}
        new_nodes = []
        for s_key, nd in cur_nodes.items():
            for node, iso in (nd or {}).items():
                if iso and (prev_nodes.get(s_key) or {}).get(node) is None:
                    new_nodes.append({"sku": s_key, "node": node, "at": iso})
        out.append({"date": s.get("date"), "newNodes": new_nodes,
                    "skuCount": len(cur_nodes)})
        prev_nodes = cur_nodes
    return out


def _prev_day_nodes(category, today_str=None):
    """昨天（< 今日的最近一张）快照的节点表；无 → {}。"""
    today_str = today_str or datetime.now().strftime("%Y-%m-%d")
    snaps = _snaps_of(category)
    for s in reversed(snaps):
        if str(s.get("date", "")) < today_str:
            return s.get("nodes") or {}
    return {}


# ---------------------------------------------------------------------------
# 当日异常上报清单（P0-7）—— 只针对**进行中**超期段
# ---------------------------------------------------------------------------
def compute_alerts_today(category=None, data=None):
    """{todayNewOverdue, ongoingOverdue, warnings}

    todayNewOverdue：昨日快照同段未超期（或昨日无记录）而今日超期；
    ongoingOverdue ：昨日已超期，cumDays = elapsed − target；
    warnings       ：85% 预警段（P1-2）。
    """
    cat = rules_for(category).key
    data = data or get_inflight_data(cat)
    prev_nodes = _prev_day_nodes(cat)
    today = date.today()
    ratio = float(data.get("warnRatio") or 0.85)

    new_over, ongoing, warns = [], [], []
    for sku, det in (data.get("skuDetails") or {}).items():
        for o in det.get("overdues") or []:
            row = {"sku": sku, "dept": o["dept"], "seg": o["seg"],
                   "overDays": o["overDays"], "elapsed": o["elapsed"],
                   "target": o["target"], "owner": o["owner"]}
            # 昨日同段是否已超期：用昨日快照里该段起点节点的时间重算 elapsed；
            # 昨日无快照/无该 SKU/无起点时间 → 按"当日新超期"（首次被发现）
            was_over = False
            t0_iso = _seg_t0_iso(det, o["seg"])
            if prev_nodes and t0_iso:
                start_iso = (prev_nodes.get(sku, {})
                             .get(_seg_start_node(det, o["seg"])) or t0_iso)
                t0 = _parse_iso(start_iso)
                if t0 is not None and o["target"]:
                    elapsed_y = max((today - t0.date()).days, 0)
                    was_over = elapsed_y > float(o["target"])
            (ongoing if was_over else new_over).append(row)
        for w in det.get("warnings") or []:
            warns.append({"sku": sku, "dept": w["dept"], "seg": w["seg"],
                          "elapsed": w["elapsed"], "target": w["target"],
                          "ratio": ratio, "owner": w["owner"]})
    new_over.sort(key=lambda r: -r["overDays"])
    ongoing.sort(key=lambda r: -r["overDays"])
    warns.sort(key=lambda r: -(r["elapsed"] - r["target"] * ratio))
    return {"todayNewOverdue": new_over, "ongoingOverdue": ongoing,
            "warnings": warns}


def _seg_start_node(det, seg_name):
    """段行 → 起点节点名（segments 里已带 from）。"""
    for s in det.get("segments") or []:
        if s.get("seg") == seg_name:
            return s.get("from")
    return None


def _seg_t0_iso(det, seg_name):
    """段行 → 起点节点的 iso 时间（今日口径）。"""
    start = _seg_start_node(det, seg_name)
    if not start:
        return None
    return (det.get("nodes") or {}).get(start)


def _parse_iso(v):
    if not v:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# 日报落盘（P0-4）：生成 xlsx → 最新批次 reports/ → 索引 → 过期清理
# ---------------------------------------------------------------------------
def _latest_batch_dir(category):
    """该品类最新批次目录：DB imported_at 最新优先，回退目录扫描最后一个。"""
    from sqlalchemy import select
    from .db import SessionLocal
    from .models import Batch
    from .routers.review import _cat_cond
    cat = rules_for(category).key
    try:
        with SessionLocal() as session:
            row = session.execute(
                select(Batch.name).where(_cat_cond(cat))
                .order_by(Batch.imported_at.desc()).limit(1)).first()
        if row and row[0]:
            for b in list_batches(cat):
                if b["name"] == row[0]:
                    return b["path"]
    except Exception:
        pass
    batches = list_batches(cat)
    return batches[-1]["path"] if batches else None


def _load_report_index():
    if not _REPORT_INDEX.exists():
        return []
    try:
        with open(_REPORT_INDEX, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_report_index(entries):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _REPORT_INDEX.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _REPORT_INDEX)


def reports_index(category=None):
    """日报元数据列表（前端下载列表）。category=None → 全部品类。"""
    cat = rules_for(category).key if category else None
    entries = _load_report_index()
    if cat:
        entries = [e for e in entries if e.get("category") == cat]
    return sorted(entries, key=lambda e: e.get("date", ""), reverse=True)


def _drop_legacy_report(reports_dir, stamp):
    """删除同一天、旧命名（复盘报告_YYYYMMDD.xlsx）的历史文件。

    改名（2026-09-24）后，同一天的索引条目会指向新名文件；旧文件若不删，
    就既不在索引里（保留期清理看不到它）也不在任何下载入口，变成永久孤儿。
    路径必须落在数据根之下才允许删（与 cleanup_expired_reports 同一条防线）。
    """
    from pathlib import Path

    from .config import DATA_ROOTS
    legacy = Path(reports_dir) / f"{_LEGACY_REPORT_PREFIX}{stamp}.xlsx"
    if not legacy.exists():
        return None
    roots = [str(Path(p)) for p in DATA_ROOTS.values()]
    if not any(str(legacy).startswith(r) for r in roots):
        return None
    try:
        legacy.unlink()
        return str(legacy)
    except OSError:
        return None        # 删不掉（被 Excel 占用）→ 不管，下次生成再试


def cleanup_expired_reports():
    """按 report_retention_days 清理过期日报（文件 + 索引）。

    路径必须在数据根之下才允许删除（防索引被改坏后误删任意文件）。
    """
    from pathlib import Path

    from .config import DATA_ROOTS
    from .rule_service import get_rules
    rules, _ = get_rules()
    keep = int(rules.get("report_retention_days") or 90)
    cutoff = (datetime.now() - timedelta(days=keep)).strftime("%Y-%m-%d")
    roots = [str(Path(p)) for p in DATA_ROOTS.values()]
    entries = _load_report_index()
    kept, removed = [], []
    for e in entries:
        path = str(e.get("path") or "")
        if str(e.get("date", "")) >= cutoff:
            kept.append(e)
            continue
        if any(path.startswith(r) for r in roots) and os.path.exists(path):
            try:
                os.remove(path)
                removed.append(path)
            except OSError:
                kept.append(e)      # 删不掉（被占用）→ 保留索引，下次再试
        # 文件本来就不在 → 直接丢索引
    if removed:
        _save_report_index(kept)
    return {"removed": removed, "kept": len(kept), "retentionDays": keep}


def _parse_iso(v):
    """ISO 时间串 → datetime；解析不了返回 None（甘特画不了条的段跳过）。"""
    if not v:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(v), fmt)
        except (ValueError, TypeError):
            continue
    return None


def _gantt_records(data, today=None):
    """事中日报甘特记录（画法 = 流程复盘 gantt_sheet.py 同款风格）。

    每个在研 SKU 一条：{sku, model, status, staySeg, stayDaysLeft, rows}，
    rows 项 {dept, seg, t0, t1, days, target, judge, owner, neutral}（t0/t1=datetime）。
    与复盘甘特的两点差异（事中口径）：
      · **未完成段（t1 空）条形延伸至今日** —— 当前停留一目了然，
        判定列改写为 ⏳进行中 / ⚠预警 / ✗超期（复盘口径下这些段是"—缺时间"）；
      · 只画在研 SKU（复盘是全部）。
    SKU 区块按「新→旧」排列（最早节点时间降序，同 route_check_sys 甘特）。
    """
    today = today or date.today()
    noon = datetime.combine(today, datetime.min.time()).replace(hour=12)
    recs = []
    for it in (data.get("items") or []):
        det = (data.get("skuDetails") or {}).get(it["sku"]) or {}
        rows = []
        for s in det.get("segments") or []:
            t0, t1 = _parse_iso(s.get("t0")), _parse_iso(s.get("t1"))
            judge = s.get("judge")
            days = s.get("days")
            if t1 is None or s.get("noShip"):
                if t0 is None:
                    continue            # 未开始：起点无时间，无条形可画
                t1 = noon               # 未完成段延伸至今日（事中口径）
                if not s.get("noShip"):
                    # noShip 行 judge 已是"✗超时未发货/⏳未发货"，保留
                    st_name = s.get("state")
                    if st_name == ST_OVER:
                        judge = f"✗超期(已{s.get('elapsed')}天)"
                    elif st_name == ST_WARN:
                        judge = f"⚠预警(线{s.get('warnLine')}天)"
                    else:
                        judge = f"⏳进行中(已{s.get('elapsed')}天)"
                days = s.get("elapsed")
            rows.append({"dept": s.get("dept"), "seg": s.get("seg"),
                         "t0": t0, "t1": t1, "days": days,
                         "target": s.get("target"), "judge": judge,
                         "owner": s.get("executor") or s.get("owner") or "",
                         "neutral": bool(s.get("gap"))
                         or str(judge or "").startswith("—")})
        if not rows:
            continue
        recs.append({"sku": it["sku"], "model": it.get("model"),
                     "status": it.get("status"),
                     "staySeg": it.get("staySeg"),
                     "stayDaysLeft": it.get("stayDaysLeft"),
                     "rows": rows})

    def _start(rec):
        ts = [r["t0"] for r in rec["rows"] if r["t0"]]
        return min(ts) if ts else datetime.min
    recs.sort(key=_start, reverse=True)         # 新 → 旧
    return recs


def generate_daily_report(category=None, date_str=None):
    """生成当日日报 xlsx（幂等覆盖同名文件），返回 {path, sheets, ...}。

    落盘名 = `事中日报_YYYYMMDD.xlsx`（2026-09-24 起；旧名「复盘报告_」同日文件随迁删除）。
    2026-10-06 起两处改版：
      · 总表「最差环节」→「当前停留环节」+ 距超时天数（见 _pick_stay）；
      · 「今日进度变化」→「在研SKU甘特图」（每在研 SKU 一块，未完成段延伸至今日）。
    豁免 SKU（人工登记/换号/末端终止）移出在研追踪，明细单列「豁免备查」sheet
    （无豁免时仍是 5 sheets，不产生空页）。
    人工登记备注（2026-10-15）：读 `资产文件\事中日报人工登记.xlsx` → {SKU: 备注}，
    填进总表末列，随每日生成自动延续（真源与口径见 sku_note.py）。读不到=全空，不阻断。
    """
    from .reporting import daily_report as _daily_xlsx
    from .sku_note import read_notes
    from pathlib import Path

    rs = rules_for(category)
    cat = rs.key
    data = get_inflight_data(cat)
    alerts = compute_alerts_today(cat, data=data)
    today = (date_str or datetime.now().strftime("%Y-%m-%d"))

    batch_dir = _latest_batch_dir(cat)
    if not batch_dir:
        raise FileNotFoundError(f"品类「{cat}」下没有可用批次目录，无法落日报")
    target = Path(batch_dir) / "reports"
    target.mkdir(parents=True, exist_ok=True)
    stamp = today.replace("-", "")
    fname = f"{_REPORT_PREFIX}{stamp}.xlsx"
    path = target / fname
    dropped = _drop_legacy_report(target, stamp)

    # 豁免备查 = 三通道豁免（人工登记/换号/末端终止）+ 运输闭环免追踪（kind="运输闭环"）。
    # 无任何豁免时保持 5 sheets，不产生空页。
    exempts = list(data.get("exempted") or [])
    exempts.extend(data.get("transportExempted") or [])
    exempts = exempts or None
    gantts = _gantt_records(data)
    notes, note_warns = read_notes()   # 登记表异常只随返回值告警，不阻断日报
    # 落值条数 = 登记表里**本批次在研**的 SKU 命中的备注数（不是登记表总条数：
    # 备注表不按品类分，手机壳的日报不该把膜的 SKU 备注计进去）
    note_applied = sum(1 for it in (data["items"] or [])
                       if notes.get(it.get("sku")))
    content = _daily_xlsx(
        category=cat, computed_at=data["computedAt"], date_str=today,
        items=data["items"], alerts=alerts, gantts=gantts,
        provisional=rs.provisional, targets_source=rs.targets_source,
        exempts=exempts, notes=notes)
    try:
        with open(path, "wb") as f:
            f.write(content)
    except PermissionError as e:
        # 日报是幂等覆盖，但 Excel/WPS 开着同名文件时会锁住写入
        # （~$ 锁文件）。给一句人话，别甩裸 PermissionError 给定时任务日志。
        raise RuntimeError(
            f"日报文件被占用，写不进去：{path}\n"
            "多半是 Excel/WPS 正开着它 —— 关闭后重跑即可（生成是幂等覆盖）。") from e

    entry = {
        "date": today, "category": cat, "path": str(path),
        "skuCount": data["inResearchCount"],
        # 上报清单总数 = 当日新超期 + 持续超期
        "overdueCount": (len(alerts["todayNewOverdue"])
                         + len(alerts["ongoingOverdue"])),
        "warnCount": len(alerts["warnings"]),
        "exemptCount": data.get("exemptCount", 0),
        "transportExemptCount": data.get("transportExemptCount", 0),
        "noteCount": note_applied,
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    with _lock:
        entries = [e for e in _load_report_index()
                   if not (e.get("category") == cat and e.get("date") == today)]
        entries.append(entry)
        _save_report_index(entries)
    cleanup_expired_reports()
    return {"path": str(path), "sheets": 6 if exempts else 5, "date": today,
            "skuCount": entry["skuCount"], "overdueCount": entry["overdueCount"],
            "warnCount": entry["warnCount"],
            "exemptCount": entry["exemptCount"],
            "transportExemptCount": entry.get("transportExemptCount", 0),
            "noteCount": entry["noteCount"], "noteWarns": note_warns,
            "category": cat,
            "targetsProvisional": rs.provisional,
            "legacyRemoved": dropped}


def read_report_file(date_str, category=None):
    """按日期取日报文件 bytes（下载接口用）；找不到抛 FileNotFoundError。"""
    cat = rules_for(category).key if category else None
    for e in reports_index(cat):
        if e.get("date") == date_str and os.path.exists(e.get("path") or ""):
            with open(e["path"], "rb") as f:
                return f.read(), e["path"]
    raise FileNotFoundError(f"{date_str} 无日报（category={cat or '全部'}）")
