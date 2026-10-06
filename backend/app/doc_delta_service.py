# -*- coding: utf-8 -*-
"""
doc_delta_service.py — 未闭环 SKU 单据增量追踪模型（T10）
=====================================================================
问题（用户 2026-09-28）：
    「最新一批 4027 个单据，对比上周我得到的感知较弱；而且批次对比页以后只剩
      一个批次了。我需要定义一个模型，追踪未闭环 SKU 新增单据。」

为什么现有的「批次对比」结构性地失效：
    本系统的新模式是「钉钉每天导出最新全量、批次目录名固定」——批次名不再随
    日期变化（`data\\手机膜\\手机膜\\Part*_xx.xlsx` 反复覆盖），于是批次表里
    永远只有那一个批次在长。批次对比（`/api/compare`）比的是批次之间的 KPI/
    partBar/statusPie，**没有第二个批次可对比**，只能看总量 4027 这样的大数，
    推不出「哪些未闭环 SKU 又动了、动了哪张单」。

模型（本模块）：
    把「未闭环 SKU 的单据集合」当成一个**随时间演化的状态**，每次数据指纹
    变化时拍一次，并只保留**最新基线 + 增量事件流**：

        state[品类]  = {at, fingerprint, skuCount, docCount, docs:{单据键: 指纹}}
        events[]     = 每次指纹变化产生的一条增量事件（新增单据 / 新动作 /
                       状态变化 / 进入或离开未闭环集合）

    · 单据键 = `SKU|Part|审批编号`（审批编号为空时退化为 `SKU|Part|c:创建时间`）。
      ⚠️ 不能用 `part_doc.id`：重导入会整批删掉重建，自增 id 每次都变，
      拿它做基线会让「每张单都是新的」。
    · 单据指纹 = 审批状态 + 节点动作列表（`动作|时间|操作人`），所以既有单据上
      新推进一步也会被抓出来 —— 这正是用户要的「单据内新动作也算」。
    · 只跟踪**未闭环 SKU**（复盘口径 valid=False）：已闭环的 SKU 不会再推进，
      留在基线里只会让文件无谓变大（实测未闭环单据只占全部单据的 1/8）。

为什么不是「每天存一张全量快照」：
    全量快照实测 ~100KB/品类/次，一天两次 + 留一年 ≈ 100MB。改成
    「基线 + 事件流」后稳态只有 ~200KB，而历史趋势一点没丢（事件本身就是历史，
    按日聚合即可画走势），还能直接回答「近 7 天新增了多少张单」。

口径与红线：
    · 「未闭环」= `review.get_review_data(cat)["rows"]` 里 `valid=False`，
      与复盘总览/预警页同源，不另立标准。
    · 豁免（2026-10-06 与事中日报/加急预警合并口径）：人工登记 / 换号 /
      末端终止（作废单）的 SKU 从未闭环集合里**剔除**——它们和已闭环一样
      不会再推进，留在追踪集合里只会把终止单据当成增量噪声。
      判定单一真源 = app/exemption.py。
    · 单据集合 = `review._load_sku_docs` + `review.dedupe_docs`，即全系统
      统一的那份（含已结束/终止单据；多批次重复单据只留最新一条）。
    · 节点时间优先级沿用复盘：系统业务时间列 > audit 上报 > 审批动作（本模块
      只消费 `_load_sku_docs` 解析好的节点，不碰优先级）。
    · 锁纪律（本项目有死锁前科，务必遵守）：`review` 的重算**绝不在锁内**调用。
      本模块的写法是「锁内只做 load/diff/save」，重算（含 get_review_data）
      一律在锁外，且用双重检查避免并发重复提交。

对外：
    record_doc_snapshot / maybe_doc_snapshot  → 拍快照（auto_pipeline 与接口都调）
    doc_delta(category, days, sku)           → 页面/导出消费的完整响应体
"""

import json
import os
import threading
from datetime import datetime, timedelta

from . import exemption
from .category_rules import rules_for
from .config import STATE_DIR

#: 未闭环判定的口径说明（页面与导出都直接展示这一句，改口径只改这里）
SCOPE_DESC = ("未闭环 SKU（复盘口径 valid=False；已剔除人工登记 / 换号 / "
              "末端终止豁免，与事中日报·加急预警同口径）")

#: 增量事件保留期（天）与条数上限：事件很小，留足一年多的排障纵深
EVENT_RETENTION_DAYS = 400
EVENT_MAX = 2000

#: 默认统计窗口（天）
DEFAULT_WINDOW_DAYS = 30

#: 「疑似基线重算」阈值：既有单据被替换超过这个比例时打标记（数据源换号段等）
SUSPECT_RATIO = 0.6
SUSPECT_MIN_DOCS = 50

STATE_FILE = STATE_DIR / "doc_delta_state.json"

# RLock：auto_pipeline（定时）与接口（uvicorn 线程）会并发拍快照
_lock = threading.RLock()


# ---------------------------------------------------------------------------
# 键与指纹
# ---------------------------------------------------------------------------
def _iso(t):
    """datetime → 'YYYY-MM-DD HH:MM:SS'；已是字符串/None 原样返回。"""
    if t is None:
        return None
    if isinstance(t, datetime):
        return t.strftime("%Y-%m-%d %H:%M:%S")
    return str(t)


def doc_key(sku, part, approval_no, create_time=None):
    """单据的稳定键：`SKU|Part|审批编号`。

    审批编号缺失（空）时退化为 `SKU|Part|c:创建时间` —— 仍比自增 id 稳，
    因为源文件里同一张单的创建时间不会变。
    """
    no = (approval_no or "").strip()
    if not no:
        no = "c:" + (_iso(create_time) or "?")
    return f"{sku or ''}|{part or ''}|{no}"


def split_key(key):
    """单据键 → (sku, part, 审批编号)。sku 里不会含 '|'，只切第一段。"""
    sku, _, rest = str(key).partition("|")
    part, _, no = rest.partition("|")
    return sku, part, no


def node_sig(node):
    """节点 → 指纹串 `动作|时间|操作人`（时间缺失时留空占位，保持三段）。

    三段式是为了 diff 后还能把「谁在什么时候推进了哪一步」复原出来；
    不存 remark（体积大且对增量判断无用）。
    """
    a = (node.get("action") or "").strip()
    t = _iso(node.get("action_time")) or ""
    op = (node.get("operator") or "").strip()
    return f"{a}|{t}|{op}"


def split_sig(sig):
    """指纹串 → (动作, 时间, 操作人)。"""
    a, _, rest = str(sig).partition("|")
    t, _, op = rest.partition("|")
    return a, t, op


def _empty_diff():
    return {"newDocs": [], "newNodes": [], "statusChanges": [],
            "closedSkus": [], "newSkus": [], "summary": {
                "newDocs": 0, "newNodes": 0, "statusChanges": 0,
                "closedSkus": 0, "newSkus": 0, "docCount": 0, "skuCount": 0}}


# ---------------------------------------------------------------------------
# 基线构建（全部在锁外调用）
# ---------------------------------------------------------------------------
def build_docs(category=None, review_data=None):
    """→ (docs, open_skus)：当前**未闭环 SKU** 的全部单据指纹表。

    docs      : {单据键: {"s": 审批状态, "c": 创建时间, "n": [节点指纹]}}
    open_skus : {SKU}（复盘判定的未闭环集合，已剔除豁免 SKU）

    ⚠️ 本函数会触发 review 重算（可能几秒），**调用方必须在锁外调用它**。
    """
    from .routers.review import (_load_sku_docs, dedupe_docs,
                                 get_review_data)

    cat = rules_for(category).key
    data = review_data if review_data is not None else get_review_data(cat)
    open_skus = {r["sku"] for r in (data.get("rows") or [])
                 if not r.get("valid")}

    # 豁免剔除（2026-10-06 口径合并）：人工登记 / 换号 / 末端终止（作废单）的
    # SKU 与已闭环一样不会再推进。⚠️ 判定用**去重前**的全量单据（与 alerts
    # 的 _load_all_docs 同口径——dedupe 留最新会掩盖「终止新单 + 已结束旧单」
    # 这类混合形态，导致误判作废）。
    raw_docs = _load_sku_docs(cat)
    parts_by_sku = {}
    for d in raw_docs:
        sku = d.get("sku")
        if sku:
            parts_by_sku.setdefault(sku, {}).setdefault(
                d.get("part"), []).append(d)
    exempt_skus = {sku for sku, parts in parts_by_sku.items()
                   if exemption.classify(sku, parts)[0] != "active"}
    open_skus -= exempt_skus

    docs, _ = dedupe_docs(raw_docs)
    out = {}
    for d in docs:
        sku = d.get("sku")
        if not sku or sku not in open_skus:
            continue
        key = doc_key(sku, d.get("part"), d.get("approval_no"),
                      d.get("create_time"))
        out[key] = {
            "s": (d.get("status") or "").strip(),
            "c": _iso(d.get("create_time")),
            "n": [node_sig(x) for x in (d.get("nodes") or [])],
        }
    return out, open_skus


def diff_docs(prev_docs, cur_docs):
    """两个基线之间的增量（纯函数，可单测）。

    三类事件：
      newDocs       —— 本基线有、上一基线没有的单据（含其节点，见下）
      newNodes      —— **既有单据**上新增的节点动作（新单据的动作不在这里重复计）
      statusChanges —— 既有单据的审批状态变化（审批中→已结束 等）
    集合进出：
      newSkus       —— 新进入「未闭环」集合的 SKU（新品/刚有数据）
      closedSkus    —— 离开未闭环集合（已闭环）的 SKU

    ⚠️ 新单据的节点不重复计入 newNodes：否则「一张新单带 5 个节点」会被数成
    1 张单据 + 5 个新动作，把「新增动作」这个指标灌水。页面在新增单据行里
    直接显示该单的节点数，信息不丢。
    """
    prev_docs = prev_docs or {}
    cur_docs = cur_docs or {}

    prev_skus = {split_key(k)[0] for k in prev_docs}
    cur_skus = {split_key(k)[0] for k in cur_docs}

    new_docs, new_nodes, status_changes = [], [], []
    for key in sorted(cur_docs):
        c = cur_docs[key]
        sku, part, no = split_key(key)
        p = prev_docs.get(key)
        if p is None:
            new_docs.append({"sku": sku, "part": part, "approvalNo": no,
                             "status": c.get("s") or "",
                             "createAt": c.get("c"),
                             "nodeCount": len(c.get("n") or [])})
            continue

        if (p.get("s") or "") != (c.get("s") or ""):
            status_changes.append({"sku": sku, "part": part, "approvalNo": no,
                                   "from": p.get("s") or "—",
                                   "to": c.get("s") or "—"})

        old = set(p.get("n") or [])
        for sig in (c.get("n") or []):
            if sig not in old:
                a, t, op = split_sig(sig)
                new_nodes.append({"sku": sku, "part": part, "approvalNo": no,
                                  "action": a, "at": t, "operator": op})

    # 展示序：新增单据按创建时间倒序（缺时间排最后），新动作按时间倒序
    new_docs.sort(key=lambda r: (r.get("createAt") or "", r["sku"]), reverse=True)
    new_nodes.sort(key=lambda r: (r.get("at") or "", r["sku"]), reverse=True)
    status_changes.sort(key=lambda r: (r["sku"], r["part"]))

    return {
        "newDocs": new_docs,
        "newNodes": new_nodes,
        "statusChanges": status_changes,
        "newSkus": sorted(cur_skus - prev_skus),
        "closedSkus": sorted(prev_skus - cur_skus),
        "summary": {
            "newDocs": len(new_docs),
            "newNodes": len(new_nodes),
            "statusChanges": len(status_changes),
            "newSkus": len(cur_skus - prev_skus),
            "closedSkus": len(prev_skus - cur_skus),
            "docCount": len(cur_docs),
            "skuCount": len(cur_skus),
        },
    }


# ---------------------------------------------------------------------------
# 状态文件读写
# ---------------------------------------------------------------------------
def _load():
    if not STATE_FILE.exists():
        return {"version": 1, "state": {}, "events": []}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"version": 1, "state": {}, "events": []}
    if not isinstance(data, dict):
        return {"version": 1, "state": {}, "events": []}
    data.setdefault("version", 1)
    if not isinstance(data.get("state"), dict):
        data["state"] = {}
    if not isinstance(data.get("events"), list):
        data["events"] = []
    return data


def _save(data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, STATE_FILE)


def _prune(events):
    """按保留期 + 条数上限裁剪事件流（保留最新）。"""
    cutoff = (datetime.now() - timedelta(days=EVENT_RETENTION_DAYS)) \
        .strftime("%Y-%m-%d")
    kept = [e for e in events if str(e.get("date") or "") >= cutoff]
    return kept[-EVENT_MAX:]


def _fingerprint(category):
    from .routers.review import fingerprint_for
    return fingerprint_for(category)


def _brief(st):
    if not st:
        return None
    return {"at": st.get("at"), "fingerprint": st.get("fingerprint"),
            "skuCount": st.get("skuCount"), "docCount": st.get("docCount")}


def _events_of(data, cat):
    return [e for e in (data.get("events") or []) if e.get("category") == cat]


# ---------------------------------------------------------------------------
# 拍快照
# ---------------------------------------------------------------------------
def record_doc_snapshot(category=None, fingerprint=None, force=False,
                        review_data=None):
    """拍一张增量基线快照。返回 {recorded, reason, category, event, state}。

    幂等：**指纹没变就直接返回**（自动管线一天跑两次、页面访问也会补拍，
    没变就不该产生新事件）。首次运行只建基线、**不产生事件** —— 否则一上来
    就会把当时全部未闭环单据报成「新增」（实测 504 条），那是噪声不是增量。
    """
    cat = rules_for(category).key
    fp = fingerprint or _fingerprint(cat)

    # ---- 第一道检查（锁内，只读，避免无谓重算）----
    with _lock:
        data = _load()
        st = (data.get("state") or {}).get(cat)
        if st and st.get("fingerprint") == fp and not force:
            return {"recorded": False, "reason": "指纹未变化（无新数据导入）",
                    "category": cat, "event": (_events_of(data, cat) or [None])[-1],
                    "state": _brief(st)}

    # ---- 批次目录都不在了就别拍：否则会把基线冲成空表、下一轮全报成新增 ----
    from .routers.review import _live_batch_ids
    if not _live_batch_ids(cat):
        with _lock:
            data = _load()
            st = (data.get("state") or {}).get(cat)
        return {"recorded": False, "reason": "无活批次（批次目录缺失），跳过拍快照",
                "category": cat,
                "event": (_events_of(data, cat) or [None])[-1],
                "state": _brief(st)}

    # ---- 重算（锁外：会调 review，锁内重入是本项目的死锁雷区）----
    docs, open_skus = build_docs(cat, review_data=review_data)
    now = datetime.now()

    # ---- 第二道检查 + 提交 ----
    with _lock:
        data = _load()
        st = (data.get("state") or {}).get(cat)
        if st and st.get("fingerprint") == fp and not force:
            return {"recorded": False, "reason": "指纹未变化（无新数据导入）",
                    "category": cat,
                    "event": (_events_of(data, cat) or [None])[-1],
                    "state": _brief(st)}

        prev_docs = (st or {}).get("docs") or {}
        # 基线只看「该品类有没有建过基线」（state 条目存在与否），不看 docs 是否为空：
        # 全部未闭环 SKU 都闭环时 docs 会正常变空，之后再有新 SKU 进来，那批单据
        # 就是真新增，不能被当成「首次建基线」吞掉。
        baseline = st is None
        diff = _empty_diff() if baseline else diff_docs(prev_docs, docs)

        event = {
            "date": now.strftime("%Y-%m-%d"),
            "at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "category": cat,
            "fingerprint": fp,
            "baseline": baseline,
            # 疑似基线重算：源单据号段换了、或基线文件被清过。**只标记不吞数据**
            # —— 宁可让业务看到「这轮 480 张单都算新增」并怀疑，也不要静默丢增量。
            "suspect": bool(
                len(docs) >= SUSPECT_MIN_DOCS
                and len(diff["newDocs"]) > SUSPECT_RATIO * len(docs)),
            **diff,
        }
        if not baseline:
            data.setdefault("events", []).append(event)
            data["events"] = _prune(data["events"])

        data.setdefault("state", {})[cat] = {
            "at": event["at"], "fingerprint": fp,
            "skuCount": len(open_skus), "docCount": len(docs), "docs": docs,
        }
        _save(data)

        return {"recorded": True,
                "reason": "首次建立基线（不计增量）" if baseline else "已记录增量",
                "category": cat, "event": event,
                "state": _brief(data["state"][cat])}


def maybe_doc_snapshot(category=None):
    """指纹变化时才拍（auto_pipeline 与「单据增量」页首次访问都调它）。"""
    return record_doc_snapshot(category, fingerprint=None, force=False)


# ---------------------------------------------------------------------------
# 查询（页面 / 导出消费）
# ---------------------------------------------------------------------------
def _by_day(events):
    """事件按日聚合（画走势用，日期升序）。"""
    agg = {}
    for e in events:
        d = str(e.get("date") or "")
        if not d:
            continue
        s = e.get("summary") or {}
        row = agg.setdefault(d, {"date": d, "newDocs": 0, "newNodes": 0,
                                 "statusChanges": 0, "newSkus": 0,
                                 "closedSkus": 0, "snapshots": 0})
        row["newDocs"] += int(s.get("newDocs") or 0)
        row["newNodes"] += int(s.get("newNodes") or 0)
        row["statusChanges"] += int(s.get("statusChanges") or 0)
        row["newSkus"] += int(s.get("newSkus") or 0)
        row["closedSkus"] += int(s.get("closedSkus") or 0)
        row["snapshots"] += 1
    return [agg[k] for k in sorted(agg)]


def _by_sku(events, sku_filter=None):
    """事件按 SKU 聚合（谁在动、动了多少）。"""
    agg = {}
    for e in events:
        at = e.get("at") or ""
        for r in e.get("newDocs") or []:
            if sku_filter and r.get("sku") != sku_filter:
                continue
            row = agg.setdefault(r["sku"], _sku_row(r["sku"]))
            row["newDocs"] += 1
            row["lastChangeAt"] = max(row["lastChangeAt"] or "", at)
        for r in e.get("newNodes") or []:
            if sku_filter and r.get("sku") != sku_filter:
                continue
            row = agg.setdefault(r["sku"], _sku_row(r["sku"]))
            row["newNodes"] += 1
            row["lastChangeAt"] = max(row["lastChangeAt"] or "", at)
        for r in e.get("statusChanges") or []:
            if sku_filter and r.get("sku") != sku_filter:
                continue
            row = agg.setdefault(r["sku"], _sku_row(r["sku"]))
            row["statusChanges"] += 1
            row["lastChangeAt"] = max(row["lastChangeAt"] or "", at)
    return agg


def _sku_row(sku):
    return {"sku": sku, "model": "", "devType": "", "reason": "",
            "docCount": 0, "newDocs": 0, "newNodes": 0, "statusChanges": 0,
            "lastChangeAt": ""}


def window_rows(category=None, days=DEFAULT_WINDOW_DAYS, sku=None):
    """窗口内**明细行**汇总（导出与钻取用；页面只吃 doc_delta 的聚合）。

    返回 {since, days, newDocs, newNodes, statusChanges, closedSkus, newSkus}，
    每行自带 `at`（事件时刻）以便追溯是哪一次同步产生的。
    """
    cat = rules_for(category).key
    days = max(int(days or DEFAULT_WINDOW_DAYS), 1)
    data = _load()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    events = [e for e in _events_of(data, cat)
              if str(e.get("date") or "") >= cutoff]

    def _filt(r):
        return (not sku) or r.get("sku") == sku

    out = {"since": cutoff, "days": days,
           "newDocs": [], "newNodes": [], "statusChanges": [],
           "closedSkus": [], "newSkus": []}
    for e in events:
        at = e.get("at") or ""
        for r in e.get("newDocs") or []:
            if _filt(r):
                out["newDocs"].append({**r, "at": at})
        for r in e.get("newNodes") or []:
            if _filt(r):
                # ⚠️ 节点行自带的 "at" 是**动作时间**，会被事件时刻覆盖 ——
                # 先把它挪到 actionAt，避免两种时间在同一字段上打架。
                out["newNodes"].append({**r, "actionAt": r.get("at"), "at": at})
        for r in e.get("statusChanges") or []:
            if _filt(r):
                out["statusChanges"].append({**r, "at": at})
        for s in e.get("closedSkus") or []:
            if not sku or s == sku:
                out["closedSkus"].append({"sku": s, "at": at})
        for s in e.get("newSkus") or []:
            if not sku or s == sku:
                out["newSkus"].append({"sku": s, "at": at})
    out["newDocs"].sort(key=lambda r: (r.get("createAt") or "", r.get("at") or ""),
                        reverse=True)
    out["newNodes"].sort(key=lambda r: (r.get("actionAt") or "", r.get("sku") or ""),
                         reverse=True)
    out["statusChanges"].sort(key=lambda r: (r.get("at") or "", r.get("sku") or ""),
                              reverse=True)
    return out


def doc_delta(category=None, days=DEFAULT_WINDOW_DAYS, sku=None):
    """完整响应体：基线信息 + 最近一次增量 + 窗口聚合 + 未闭环 SKU 明细。

    days: 窗口天数（走势与聚合的范围）；sku: 只看某一个 SKU（可选）。
    """
    from .routers.review import get_review_data

    cat = rules_for(category).key
    days = max(int(days or DEFAULT_WINDOW_DAYS), 1)
    data = _load()
    st = (data.get("state") or {}).get(cat) or {}
    events = _events_of(data, cat)

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    win = [e for e in events if str(e.get("date") or "") >= cutoff]

    # 未闭环 SKU 的展示信息（型号/开发类型/未闭环原因）——与复盘页同源
    meta = {}
    try:
        rdata = get_review_data(cat)
        for r in rdata.get("rows") or []:
            meta[r["sku"]] = {"model": r.get("model") or "",
                              "devType": r.get("devType") or "",
                              "reason": r.get("invalidReason") or ""}
    except Exception:                                    # noqa: BLE001
        pass

    # 当前未闭环 SKU 的单据数（来自基线）
    doc_count = {}
    for key in (st.get("docs") or {}):
        s = split_key(key)[0]
        doc_count[s] = doc_count.get(s, 0) + 1

    # _by_sku 返回 {sku: 行}（聚合中途要按 SKU 去重累加），这里转成列表
    agg = _by_sku(win, sku_filter=sku)
    by_sku = list(agg.values())
    seen = {r["sku"] for r in by_sku}
    for s, n in doc_count.items():
        if s in seen or (sku and s != sku):
            continue
        row = _sku_row(s)
        row["docCount"] = n
        by_sku.append(row)
    if sku:
        by_sku = [r for r in by_sku if r["sku"] == sku]
    for row in by_sku:
        m = meta.get(row["sku"]) or {}
        row["model"] = m.get("model") or ""
        row["devType"] = m.get("devType") or ""
        row["reason"] = m.get("reason") or ""
        row["docCount"] = doc_count.get(row["sku"], row["docCount"])
    by_sku.sort(key=lambda r: (-(r["newDocs"] + r["newNodes"]), r["sku"]))

    def _filt(r):
        return (not sku) or r.get("sku") == sku

    last = (events[-1] if events else None)
    last_out = None
    if last:
        last_out = {
            "date": last.get("date"), "at": last.get("at"),
            "baseline": bool(last.get("baseline")),
            "suspect": bool(last.get("suspect")),
            "summary": last.get("summary") or {},
            "newDocs": [r for r in (last.get("newDocs") or []) if _filt(r)],
            "newNodes": [r for r in (last.get("newNodes") or []) if _filt(r)],
            "statusChanges": [r for r in (last.get("statusChanges") or [])
                              if _filt(r)],
            "newSkus": last.get("newSkus") or [],
            "closedSkus": last.get("closedSkus") or [],
        }

    summary = {"newDocs": 0, "newNodes": 0, "statusChanges": 0,
               "newSkus": 0, "closedSkus": 0, "snapshots": len(win)}
    for e in win:
        s = e.get("summary") or {}
        for k in ("newDocs", "newNodes", "statusChanges", "newSkus",
                  "closedSkus"):
            summary[k] += int(s.get(k) or 0)

    return {
        "computedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "category": cat,
        "scope": SCOPE_DESC,
        "sku": sku or "",
        "state": _brief(st),
        "last": last_out,
        "exists": bool(st),
        "eventCount": len(events),
        "window": {"days": days, "since": cutoff, "summary": summary,
                   "byDay": _by_day(win), "bySku": by_sku},
        # 快照流水（新→旧），页面上直接看「哪几次同步真的动了东西」
        "events": [
            {"date": e.get("date"), "at": e.get("at"),
             "baseline": bool(e.get("baseline")),
             "suspect": bool(e.get("suspect")),
             "summary": e.get("summary") or {}}
            for e in reversed(events[-30:])
        ],
        "settings": {"retentionDays": EVENT_RETENTION_DAYS,
                     "eventMax": EVENT_MAX,
                     "suspectRatio": SUSPECT_RATIO},
    }
