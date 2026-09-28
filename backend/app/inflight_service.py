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
  · 全部单据都「终止」的 SKU 视作流程作废，不算在研。
"""

import json
import os
import threading
from datetime import date, datetime, timedelta

from .category_rules import rules_for
from .config import STATE_DIR, list_batches

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
    """段行 → 对外结构（详情页/日报共用）。"""
    return {
        "dept": row.get("dept"), "seg": row.get("seg"),
        "from": row.get("from"), "to": row.get("to"),
        "state": st["state"],
        "start": row.get("start"), "end": row.get("end"),
        "elapsed": st["elapsed"], "target": st["target"],
        "warnLine": st["warnLine"], "over": st["over"],
        "judge": row.get("judge"),
        "owner": row.get("owner"), "executor": row.get("executor"),
        "gap": bool(row.get("gap")), "noShip": bool(row.get("noShip")),
    }


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
          multiRoundCount, todayNewNodes, items, skuDetails}
    items 项：{sku, model, worstSeg, worstSegDept, worstOverDays, warnSegs,
               status, multiRound, rootCause, owner}
    结果按品类缓存（指纹 = 复盘指纹，规则热调/导入自动失效）。
    """
    from .routers.review import (fingerprint_for, get_review_data,
                         resolve_part1_rounds, review_sku_segments)
    from .causal import locate as _locate_root_cause
    from .rule_service import get_rules

    rs = rules_for(category)
    rules, _rule_ver = get_rules()
    ratio = float((rules.get("inflight_warn_ratio") or 0.85))
    review_data = get_review_data(rs.key)          # 复用复盘缓存（会触发 kpi 补拍）
    details = review_data["skuDetails"]
    by_sku = _load_all(rs.key)
    today = date.today()

    items = []
    inflight_details = {}          # 在研 SKU 的事中详情（sku/{code} 用）
    snap_nodes = {}                # 快照用：{sku: {node: iso}}

    for sku, info in sorted(by_sku.items()):
        if info["all_terminated"]:
            continue                # 全部单据终止 = 流程作废，不算在研

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
            parts_docs = _dedupe_parts(info)
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
        segs_out, warn_list, over_list = [], [], []
        for r in seg_rows:
            st = seg_state(r, today=today, ratio=ratio)
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

        worst = None
        worst_over = None
        if over_list:
            worst_over, r, st = over_list[0]
            worst = {"dept": r.get("dept"), "seg": r.get("seg"),
                     "elapsed": st["elapsed"], "target": st["target"],
                     "over": st["over"], "owner": r.get("executor")
                     or r.get("owner") or ""}
        elif warn_list:
            _dv, r, st = warn_list[0]
            worst = {"dept": r.get("dept"), "seg": r.get("seg"),
                     "elapsed": st["elapsed"], "target": st["target"],
                     "over": None, "owner": r.get("executor")
                     or r.get("owner") or ""}

        status = ST_OVER if over_list else (ST_WARN if warn_list else ST_RUNNING)
        owner = (worst or {}).get("owner") or ""
        items.append({
            "sku": sku, "model": info["model"],
            "worstSeg": (worst or {}).get("seg"),
            "worstSegDept": (worst or {}).get("dept"),
            "worstElapsed": (worst or {}).get("elapsed"),
            "worstTarget": (worst or {}).get("target"),
            "worstOverDays": worst_over,
            "warnSegs": [w[1].get("seg") for w in warn_list],
            "status": status, "multiRound": bool(multi_round.get("flag")),
            "rootCause": root_cause, "owner": owner,
        })
        inflight_details[sku] = {
            "sku": sku, "model": info["model"],
            "firstProposalAt": detail.get("firstProposalAt") if detail else None,
            "status": status, "multiRound": multi_round,
            "rootCause": root_cause,
            "segments": segs_out, "nodes": nodes_iso,
            "partStat": part_stat,
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
        snap_nodes[sku] = {k: v for k, v in nodes_iso.items() if v}

    # 在研最重的排前：超期天数 desc → 预警段数 desc → SKU
    items.sort(key=lambda x: (-(x["worstOverDays"] or 0),
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


def generate_daily_report(category=None, date_str=None):
    """生成当日日报 xlsx（幂等覆盖同名文件），返回 {path, sheets, ...}。

    落盘名 = `事中日报_YYYYMMDD.xlsx`（2026-09-24 起；旧名「复盘报告_」同日文件随迁删除）。
    """
    from .reporting import daily_report as _daily_xlsx
    from pathlib import Path

    rs = rules_for(category)
    cat = rs.key
    data = get_inflight_data(cat)
    alerts = compute_alerts_today(cat, data=data)
    progress = progress_for(cat, days=1)
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

    content = _daily_xlsx(
        category=cat, computed_at=data["computedAt"], date_str=today,
        items=data["items"], alerts=alerts, progress=progress,
        provisional=rs.provisional, targets_source=rs.targets_source)
    with open(path, "wb") as f:
        f.write(content)

    entry = {
        "date": today, "category": cat, "path": str(path),
        "skuCount": data["inResearchCount"],
        # 上报清单总数 = 当日新超期 + 持续超期
        "overdueCount": (len(alerts["todayNewOverdue"])
                         + len(alerts["ongoingOverdue"])),
        "warnCount": len(alerts["warnings"]),
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    with _lock:
        entries = [e for e in _load_report_index()
                   if not (e.get("category") == cat and e.get("date") == today)]
        entries.append(entry)
        _save_report_index(entries)
    cleanup_expired_reports()
    return {"path": str(path), "sheets": 5, "date": today,
            "skuCount": entry["skuCount"], "overdueCount": entry["overdueCount"],
            "warnCount": entry["warnCount"], "category": cat,
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
