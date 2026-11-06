# -*- coding: utf-8 -*-
"""
review.py — 大流程复盘计算引擎（GET /api/review/*）
====================================================
口径：实际动作映射（移植自 流程复盘/route_check_sys.py 系统口径）：
  - **按品类分发规则**（category_rules.rules_for）：手机膜 = seg_rules.py（起点=取数）；
    手机壳 = seg_rules_shell.py（**起点=提案**，该品类无「取数」节点）。
    所有接口均可带 `?category=` 参数，缺省 = 手机膜（行为与改造前一致）。
  - 节点时间优先级：系统业务时间列（支持**候选列链**，首个非空生效）> audit
    (审批记录备注含 audit) > 审批动作时间；
  - 段级绩效规则（ruleset.segs）：每段 = 起点动作→终点动作 × 目标天，
    目标天/是否考核可在规则配置中心(seg_targets)调整；
  - 跨批次重复单据只保留最新（同 Part 同审批编号，按 update_time）；
  - 运输段：Part5 发货 × Part8 到货按货件号配对，目标按渠道
    (空运15/海运35/卡航35)，空/海汇总 + 英欧日美单独行；
  - 有效复盘 = 三部门终节点全完成（产品=素材交付(=Part6 素材加工)、运营=max(上架,广告)、
    后勤=最晚渠道仓到货），全量口径不限时间窗口。

接口：
  GET /api/review/overview   全量总览（KPI + 分布 + 卡点TOP + 环节统计 + 人员 + SKU行）
  GET /api/review/sku/{code} 单个 SKU 复盘档案（段级明细、部门终节点）
  GET /api/review/sku/{code}/report  单 SKU 复盘报告 markdown

缓存：进程内缓存结果，以 batch 表 (COUNT, MAX(imported_at)) 为指纹，ETL 后自动失效。
"""

import threading
from datetime import datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, or_, select

from ..assessment import compute as _assess_one
from ..assessment import summarize as _assess_summary
from ..causal import locate as _locate_root_cause
from ..category_rules import DEFAULT_CATEGORY, category_of_text, rules_for
from ..db import SessionLocal
from ..models import ApprovalNode, Batch, PartDoc, ProductCatalog
from ..product_rules import range_of
from ..reporting import assessment_report as _assessment_xlsx
from ..reporting import route_limits
from ..reporting import sku_check_report as _skucard_xlsx
from ..review_rules import DEPARTMENTS, PART_NAMES
from ..rule_service import get_rules, seg_target
# 样机流转（2026-11-06）：计算/合格线收口在 app/sample_cycle.py，复盘与日报共用同一实现。
# 这里保留 `_sample_cycle` 旧名做别名，调用点与既有测试无需改动，口径只有一个。
from ..sample_cycle import compute as _sample_cycle
# 手机膜口径仅作为 ruleset=None 时的兜底默认值（品类规则统一由 category_rules 分发）
from ..seg_rules import AIR_TARGET, FOUR_COUNTRIES, SEA_TARGET
from ..sku_alias import load_aliases
from ..sku_hold import load_holds

router = APIRouter(prefix="/api/review", tags=["review"])

# 流程起点节点名（两个品类的规则集都以它作为依赖图根与首段起点）
ORIGIN_NODE = "提案"

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


# _sample_cycle = app.sample_cycle.compute（2026-11-06 收口，见文件顶部导入）。
# 曾经在这里各写一份：复盘算间隔、日报另算一遍，结果 15 天线改了只生效一处。
# 现在两处共用同一函数，且只认系统业务日期 —— 寄送/素材常年只有 audit 补录，
# 拿它算会把 G600 的「寄送→拍摄交付」算成 -14 天。


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
def _cat_cond(category=None):
    """批次品类过滤条件。

    ⚠️ 旧库（2026-09-22 加列之前导入的批次）`batch.category` 为 NULL。
    这类批次按既有语义**只属于默认品类**，因此默认品类要带上 NULL，
    否则升级后会看到"批次凭空消失"。非默认品类只认精确匹配 —— 绝不能把
    NULL 批次也吞进手机壳，那会让两个品类的数据互相污染。
    """
    cat = rules_for(category).key
    if cat == DEFAULT_CATEGORY:
        return or_(Batch.category == cat, Batch.category.is_(None))
    return Batch.category == cat


def _live_batch_ids(category=None):
    """目录仍存在的批次 id 集合。

    用户新模式是「钉钉每天导出最新全量」：旧批次目录会被移走/清掉，
    但 DB 记录不会自动消失。目录已不在的批次视为退役 —— 绝不能继续
    混进复盘/指纹，否则每天的新数据永远背着退役批次的旧账。
    """
    import os
    with SessionLocal() as session:
        rows = session.execute(
            select(Batch.id, Batch.path).where(_cat_cond(category))).all()
    return {bid for bid, p in rows if p and os.path.isdir(p)}


def _load_sku_docs(category=None):
    """加载该品类全部有 SKU 的单据 + 审批节点（含未结束，货件配对需要）。

    ⚠️ 品类隔离发生在这里：不同品类的批次绝不混算（规则集不同，混算会互相污染）。
    ⚠️ 只算「活批次」（目录仍存在）——退役批次目录移走后，其 DB 记录不再参与。
    """
    cond = _cat_cond(category)
    live = _live_batch_ids(category)
    if not live:
        return []
    with SessionLocal() as session:
        docs = session.execute(
            select(PartDoc.id, PartDoc.batch_id, PartDoc.part, PartDoc.sku,
                   PartDoc.model, PartDoc.approval_no, PartDoc.status,
                   PartDoc.create_time, PartDoc.finish_time, PartDoc.update_time,
                   PartDoc.raw, Batch.name.label("batch_name"),
                   Batch.category.label("batch_category"))
            .join(Batch, PartDoc.batch_id == Batch.id)
            .where(PartDoc.sku.isnot(None), cond,
                   PartDoc.batch_id.in_(live))
        ).all()
        nodes = session.execute(
            select(ApprovalNode.doc_id, ApprovalNode.seq, ApprovalNode.operator,
                   ApprovalNode.action, ApprovalNode.action_time,
                   ApprovalNode.remark)
            .join(PartDoc, ApprovalNode.doc_id == PartDoc.id)
            .join(Batch, PartDoc.batch_id == Batch.id)
            .where(PartDoc.sku.isnot(None), cond,
                   PartDoc.batch_id.in_(live))
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
         create_time, finish_time, update_time, raw, batch_name,
         batch_category) in docs:
        out.append({
            "doc_id": doc_id, "batch": batch_name,
            "batch_category": batch_category or DEFAULT_CATEGORY,
            "part": part, "sku": sku,
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
def _docs_oldest_first(docs):
    """按创建时间升序（最早一轮在前）。

    ⚠️ 取轮口径（2026-09-24 起，对齐 流程复盘/route_check_sys.py）：
    同一 Part 多张单据（多轮提交）时**统一取『最早一轮』** —— 即逐列按此顺序
    扫描、取第一个非空值，与 route_check_sys._rows_sorted(rows, oldest_first=True)
    同义。改前是「最新一轮优先」，会把『提案』取成补报轮的时间。
    """
    return sorted(docs, key=lambda d: d.get("create_time") or datetime.min)


def extract_node_times(parts_docs, restrict=None, ruleset=None):
    """parts_docs: {part: [去重后单据]} → {节点名: 节点时间字典}。

    ⚠️ 取轮口径（2026-09-24 起）＝ **最早一轮优先**，见 _docs_oldest_first。
    于是『提案』= 最早那张 Part1 的创建时间，『取数』= 最早一张填了取数日期的
    Part1 单据（两者同轮，或取数更早，**不再出现跨轮错配的反向段**）。
    改前口径 =「基准轮＝取数日期最贴近本单据创建时间的那一轮」+ restrict 限定
    单张单据，会把『提案』取成最新一轮（G506 的 2026-04-30），与最早轮的取数
    （2025-10-30）错配成 -183 天反向段。

    注意「最早一轮」是**逐列**判定的，不是整单绑定：若最早一轮没填取数、次轮
    才填，取数取次轮那张（跨轮＝真实等待），提案仍取最早轮 —— 这正是
    route_check_sys 的口径。

    restrict: {part: [允许参与取值的单据]} —— **已废弃**（原 P0-3 的轮次限定手段）。
              默认 None = 按最早一轮取；显式传入时仍只扫传入单据，仅为兼容旧调用
              保留，业务路径（compute_review / inflight_service）不再传它。

    ruleset: 品类规则集（category_rules.RuleSet）；None = 手机膜（行为与改造前一致）。
             手机壳的节点表不同（无「取数」、签样落 Part1），且系统列支持**候选链**
             （按序取第一个非空列），因此这里按 spec["sys"] 逐列尝试。
    """
    rs = ruleset or rules_for(DEFAULT_CATEGORY)
    nodes = {}
    for name, spec in rs.nodes.items():
        part, cands, act = spec["part"], spec["sys"], spec["act"]
        if restrict is not None and part in restrict:
            docs = _docs_oldest_first(list(restrict[part]))
        else:
            docs = _docs_oldest_first(parts_docs.get(part) or [])
        # 候选列链：按列优先级逐列扫单据，第一个非空即采用
        sys_raw, sys_dt, sys_field = "", None, None
        for fld in cands:
            for d in docs:
                v = _clean(d["raw"].get(fld))
                if v:
                    sys_raw, sys_dt, sys_field = v, _parse_dt(v), fld
                    break
            if sys_raw:
                break
        audit_dt = audit_op = None
        act_dt = act_op = None
        for d in docs:
            for n in d["nodes"]:
                if not act or n["action"] != act or n["action_time"] is None:
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
            "sys_field": sys_field,          # 实际命中的列（候选链时便于排查）
            "sys_date_only": bool(sys_raw) and len(sys_raw) <= 10,
            "audit": audit_dt, "audit_op": audit_op,
            "action": act_dt, "action_op": act_op,
            "adopted": adopted, "src": src,
        }
    return nodes


def resolve_part1_rounds(docs, ruleset=None):
    """Part1 多轮提交的轮次统计与异常标注（P0-3）。

    ⚠️ 取轮口径（2026-09-24 起，对齐 流程复盘/route_check_sys.py）：
    **同一 Part 多张单据统一取『最早一轮』** —— 取值本身由 extract_node_times
    逐列按最早一轮完成（见 _docs_oldest_first），本函数**不再挑「基准轮」去限定
    取值**，只负责两件事：

      · rounds：逐轮明细（详情页/核对报告看「提交过几轮、各是哪天」）；
      · flag / gapDays / note：多轮提交异常标注（PRD P0-3④「不可静默合并」）。
        gapDays = **最晚轮创建日期 − 最早轮创建日期**（日历天差），与
        route_check_sys.multi_round_note 的 max_gap 同式 —— 两个系统对同一件事
        报同一个数（sys「口径说明」第 15 条）。

    ⚠️ 真实数据形态（G506 实测，别想当然）：同 SKU 有两张 Part1 单据——
      R1 2025-10-30 创建、当天完成取数；
      R2 2026-04-30 创建（补报），但它的「手机数据取得日期」**抄了 R1 的 2025-10-30**。
    最早一轮口径下：提案 = 2025-10-30、取数 = 2025-10-30 → 提案→取数 = 0 天
    （不再有 -183 天反向段），同时 gapDays = 182 天标「疑似漏报」，
    异常不会被静默合并掉。

    Part1 配对节点/列由 ruleset 给出 = **规则集里第一个「Part1 且非提案」的节点**
    （膜＝「取数」/「手机数据取得日期」；壳＝「签样」/「三方确样时间」候选链），
    仅用于 rounds[].hasData 的展示。

    返回 (base_doc, rounds, multi_round)；base_doc = 最早一轮单据（供调用方参考，
    取值行为不再依赖它）。
    """
    rs = ruleset or rules_for(DEFAULT_CATEGORY)
    pair_fields = ()
    for _name, spec in rs.nodes.items():
        if _name != "提案" and spec["part"] == "Part1":
            pair_fields = spec["sys"]
            break

    ordered = sorted(docs or [],
                     key=lambda d: d.get("create_time") or datetime.min)
    empty = {"flag": False, "count": 0, "gapDays": None, "note": ""}
    if not ordered:
        return None, [], empty

    def _has_data(d):
        for fld in pair_fields:
            if _clean((d.get("raw") or {}).get(fld)):
                return True
        return False

    rounds = [{
        "round": i + 1,
        "approvalNo": d.get("approval_no") or "",
        "createTime": _fmt(d.get("create_time")),
        "hasData": _has_data(d),
        "status": d.get("status") or "",
    } for i, d in enumerate(ordered)]

    info = dict(empty, count=len(ordered))
    base = ordered[0]

    if len(ordered) >= 2:
        t_first = ordered[0].get("create_time")
        t_last = ordered[-1].get("create_time")
        if t_first and t_last:
            gap_days = (t_last.date() - t_first.date()).days
            if gap_days > 0:
                info.update(flag=True, gapDays=float(gap_days),
                            note=f"多轮提交·间隔 {gap_days} 天·疑似漏报")
            else:
                info["note"] = f"多轮提交 {len(ordered)} 次·同日提交"
        else:
            info["note"] = f"多轮提交 {len(ordered)} 次"
    return base, rounds, info


def node_operator(nd, default=""):
    """节点实际执行人：audit 上报人 > 审批动作执行人 > 配置默认。"""
    return nd.get("audit_op") or nd.get("action_op") or default


# ---------------------------------------------------------------------------
# 段执行人：审批动作 > 配置责任人 > 审批记录（当前负责人/历史审批人）
# ---------------------------------------------------------------------------
# 2026-10-29 用户口径：「配置表有可能会变，但是（审批记录）这个不会」。
# 实测手机膜 G599/G600/G601 的「采购→本地仓到货」段：配置里该段责任人本就为空，
# 本地仓到货还没做（节点无时间、无审批动作），旧逻辑直接渲染「⚠未配置」，
# 但这三张 Part3 单据的「当前负责人」明明白白写着夏康富 —— 信息在库里，
# 只是没被取用；结果「该催谁」丢了，绩效视角还把它们归进「(未配置)」桶。
_PERSON_SEP = ("，", "、", ";", "；", "/", "→")


def _split_persons(v):
    """责任人串 → 人名列表（兼容中英文逗号/顿号/分号/斜杠/箭头）。"""
    s = _clean(v)
    if not s:
        return []
    for sep in _PERSON_SEP:
        s = s.replace(sep, ",")
    return [p.strip() for p in s.split(",") if p.strip()]


def _pick_person(v):
    """多值责任人取「最近一位在岗的人」：从右往左，跳过已离职。

    实测形态（手机膜 Part2/Part3 原始行）：
      '张智博,李杰华,夏康富'   → 夏康富（最右 = 审批链上最后一位）
      '夏康富,谢雪娜(已离职)'   → 夏康富（离职的人不该再被催）
    """
    for name in reversed(_split_persons(v)):
        if "离职" not in name:
            return name
    return ""


# 「审批记录」原文里不算责任人的动作：提交申请常是外部公司（如「巨川电子」），
# 通知/抄送/评论是群发或留言节点 —— 都不代表「这一段该谁负责」。
_SKIP_ACTS = ("提交申请", "通知", "抄送", "评论")


def _last_action_person(record_text):
    """从「审批记录」原文取最后一个实质动作的操作人。

    原文形态（Part2/Part3 raw）：
      '巨川电子|提交申请|2026-09-29 16:43:10|提交申请;\n\n张智博|站点需求收集|…|audit|'
    字段顺序：操作人 | 动作 | 时间 | 类型。
    """
    s = _clean(record_text)
    if not s:
        return ""
    for chunk in reversed([c.strip() for c in s.split(";") if c.strip()]):
        cells = [c.strip() for c in chunk.split("|")]
        if len(cells) < 2:
            continue
        name, act = cells[0], cells[1]
        if not name or "离职" in name:
            continue
        if any(k in act for k in _SKIP_ACTS):
            continue
        return name
    return ""


def _approval_owner(parts_docs, part):
    """从该 Part 的**审批记录**里捞责任人，返回 (姓名, 来源说明)。

    回退顺序（越靠前越贴近「此刻该催谁」）：
      1. 「当前负责人」——单据此刻停在谁手上（审批中的单据最准）
      2. 「历史审批人」——审批链上走过的最后一位在岗的人
      3. 「审批记录」原文——最后一个实质动作的操作人
    选单据：审批中的优先（该段还没走完，停在谁手上就是谁），否则取最新一张。
    """
    docs = list((parts_docs or {}).get(part) or [])
    if not docs:
        return "", ""

    def _key(d):
        pending = "审批中" in str(d.get("status") or "")
        t = d.get("create_time") or datetime.min
        return (pending, t)

    for d in sorted(docs, key=_key, reverse=True):
        raw = d.get("raw") or {}
        name = _pick_person(raw.get("当前负责人"))
        if name:
            return name, "审批记录·当前负责人"
        name = _pick_person(raw.get("历史审批人"))
        if name:
            return name, "审批记录·历史审批人"
        name = _last_action_person(raw.get("审批记录"))
        if name:
            return name, "审批记录·审批动作"
    return "", ""


def _resolve_executor(nd, cfg_owner, parts_docs):
    """段执行人 → (姓名, 来源)。来源写进 seg_rows["execSrc"] 便于追溯。

    顺序：审批动作 > 配置责任人 > 审批记录 > 空（渲染时才显示「⚠未配置」）。
    ⚠️ 审批记录排在配置**之后**：配置是管理上指定的责任人，优先尊重；
    只有配置为空（如手机膜「采购→本地仓到货」）才用审批记录兜底 ——
    这样既有结果一个都不变，纯粹把原本丢掉的「未配置」补上。
    """
    op = node_operator(nd)                     # 不带配置默认，为了分辨来源
    if op:
        return op, "审批动作"
    if cfg_owner:
        return cfg_owner, "配置"
    name, src = _approval_owner(parts_docs, nd.get("part") or "")
    if name:
        return name, src
    return "", ""


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


def _seg_judge(d, target, seg_name="", backfill_ok=False):
    """段判定。backfill_ok=True 表示起点节点属"完成后补录"（手机壳「提案」），
    此时首段负值是**记录约定**而非流程异常 → 标「—补录(N天)」而不报 ⚠反向。"""
    if d is None:
        return "—缺时间"
    if d < 0:
        if backfill_ok:
            return f"—补录({_fmt_days(d)}天·先线下后补录)"
        return f"⚠反向({_fmt_days(d)}天)"
    if target is None:
        # 规则页把该段目标天清空（target=null）→「该段不设目标天」→ 不判超时。
        # 放在 d<0 之后：⚠反向 / —补录 属数据质量判定，不因「没设目标」而消失。
        # （不考核另有 available 开关，与本语义不冲突。）
        return "—未设目标"
    if seg_name == "取数→签样" and d < 9:
        return f"⚠过短({_fmt_days(d)}<9天)"
    return "✓" if d <= target else f"✗超{_fmt_days(d - target)}天"


# ---------------------------------------------------------------------------
# 货件配对与运输段
# ---------------------------------------------------------------------------
def build_shipments(parts_docs, ruleset=None):
    """Part5 发货 × Part8 到货按货件号配对（同货件号保留有业务时间的最新记录）。
    另：Part5 中只有发往渠道仓库、无货件号的记录 → 渠道未发货占位条目
    （货件号/发货/到货均留空，渠道仓保留，供货件明细/甘特/异常判定展示）。

    ruleset：品类规则集（运输方式判定/目标天）；None = 手机膜默认。
    """
    rs = ruleset or rules_for(DEFAULT_CATEGORY)
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
        mode, tgt = rs.transport_mode(hub)
        items.append({"trk": trk, "hub": hub, "ship": sd, "arr": arr,
                      "days": _dday(sd, arr), "mode": mode, "target": tgt,
                      "cls": rs.ship_class(mode), "noShip": False})
    for hub in no_trk_hubs:
        if hub in hubs_with_trk:
            continue                       # 该渠道已有正式货件，不重复列
        mode, tgt = rs.transport_mode(hub)
        items.append({"trk": "", "hub": hub, "ship": None, "arr": None,
                      "days": None, "mode": mode, "target": tgt,
                      "cls": rs.ship_class(mode), "noShip": True})
    items.sort(key=lambda x: (x["ship"] or datetime.max,
                              x["trk"] or "￿", x["hub"]))
    return items


def _short_hub(hub):
    """渠道仓名去掉渠道家族前缀，只留站点：'亚马逊美国'→'美国'、'emag罗马尼亚'→'罗马尼亚'。

    只用于甘特图「在途明细」列的展示（列宽有限），不影响任何取数键。
    """
    h = _clean(hub)
    for p in ("亚马逊物流", "亚马逊", "东南亚", "美客多", "Shopee", "shopee",
              "emag", "Emag", "EMAG"):
        if h.startswith(p) and len(h) > len(p):
            return h[len(p):]
    return h


def transport_rows(items, ruleset=None, ship_op="", today=None):
    """运输汇总行（空/海：最早发货→最晚到货）+ 4 国单独行 + 在途汇总行。

    ruleset：品类规则集（目标天/4国清单）；None = 手机膜默认。
    ship_op：Part5「发货」动作的实际操作人 —— 运输段没有审批节点可归，
             旧逻辑留空 → 日报里 4 个在运 SKU 的责任人全是「⚠未配置」。
             发货是谁做的，这段就该催谁（与后勤④「采购→发货末批」同一口径）。
    today：在途行的"至今"基准（测试可注入）。

    ⚠️ 口径（2026-11-01 用户提问后补）：上面的空/海/4国行**只汇总已到货的票**
    （过滤条件 `it["arr"] and it["ship"]`），在途票根本不进这些行 —— 段一闭合，
    甘特图看起来就像"运输全到齐了"。实测 G594：15 票到 10 票、5 票在路上，
    甘特图只画了已到那部分，括号里的票数也是**已到货票数**（越堵数字越小，反而
    显得正常）。所以这里额外出一行「运输·在途(K票)」：起点=最早在途发货，
    终点留空（甘特图延伸至今日、终点列写「进行中」），明细列给
    「已发N·到M·未到K：站点…」。
    """
    air_tgt = ruleset.air_target if ruleset is not None else AIR_TARGET
    sea_tgt = ruleset.sea_target if ruleset is not None else SEA_TARGET
    countries = (ruleset.four_countries if ruleset is not None
                 else FOUR_COUNTRIES)
    rows = []
    for cls, tgt, label in (("空运", air_tgt, "运输·空运"),
                            ("海运", sea_tgt, "运输·海运")):
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
            "executor": ship_op, "execSrc": "审批动作·发货" if ship_op else "",
            "note": "最早发货→最晚到货", "gap": False,
            "assessed": False,
        })
    n_transport = len(rows)          # 在途行插在这两行之后、4 国之前
    for cname, kw in countries:
        sub = [it for it in items
               if kw in it["hub"] and it["arr"] and it["ship"]]
        if not sub:
            continue
        s0, s1 = min(it["ship"] for it in sub), max(it["arr"] for it in sub)
        d = float((s1 - s0).days)
        tgt = sub[0]["target"] or (air_tgt if sub[0]["cls"] == "空运"
                                   else sea_tgt)
        rows.append({
            "dept": "4国", "seg": f"{cname}·运输",
            "from": "发货(最早)", "to": "到货(最晚)",
            "t0": s0, "t1": s1, "days": _fmt_days(d), "target": tgt,
            "judge": "✓" if d <= tgt else f"✗超{_fmt_days(d - tgt)}天",
            "executor": ship_op, "execSrc": "审批动作·发货" if ship_op else "",
            "note": f"{len(sub)}票·{sub[0]['mode']}", "gap": False,
            "assessed": False,
        })

    # 在途汇总行：已到货的票画完了，但**还有货在路上**这件事必须看得见
    pend = [it for it in items if it["ship"] and not it["arr"]]
    if pend:
        shipped = [it for it in items if it["ship"]]
        arrived = [it for it in items if it["arr"]]
        hubs = []
        for it in pend:
            h = _short_hub(it["hub"])
            if h and h not in hubs:
                hubs.append(h)
        s0 = min(it["ship"] for it in pend)
        rows.insert(n_transport, {
            "dept": "运输", "seg": f"运输·在途({len(pend)}票)",
            "from": "发货(最早)", "to": "未到货(截至今日)",
            "t0": s0, "t1": None,          # 未完成：甘特图延伸至今日、终点写「进行中」
            "days": None,                  # 不参与耗时/卡点统计（见 displayOnly）
            "target": None,
            "judge": f"⏳在途{len(pend)}票",
            "executor": ship_op, "execSrc": "审批动作·发货" if ship_op else "",
            "note": f"已发{len(shipped)}票·到货{len(arrived)}票·未到{len(pend)}票",
            "noteText": (f"已发{len(shipped)}·到{len(arrived)}·未到{len(pend)}："
                         + "、".join(hubs)),
            "pendingCount": len(pend),
            # displayOnly：只画在甘特图/详情，不进状态机、不产生「超期/预警/卡点」，
            # 有真实卡点时也绝不抢「当前停留」—— 否则 G594 的当前停留会从
            # 「采购→发货末批」被改成「运输·在途」，责任人、距超时、绩效统计跟着全变。
            # ✅ 唯一例外（2026-11-05）：流程段**全部走完**且本行存在时，它由
            #    inflight_service._prefer_in_transit() 兜底成为「当前停留」——
            #    否则会退化成"取最晚完成的段"，把停留晒成一个早已到货的运输段。
            "displayOnly": True,
            "gap": False, "assessed": False,
        })
    return rows


# ---------------------------------------------------------------------------
# 单 SKU 段级复盘
# ---------------------------------------------------------------------------
def review_sku_segments(sku, parts_docs, rules, restrict=None, ruleset=None):
    """按实际动作映射计算一个 SKU 的段级明细。返回 (seg_rows, nodes, items)。

    ruleset：品类规则集（category_rules.RuleSet）；None = 手机膜默认（行为不变）。
    """
    rs = ruleset or rules_for(DEFAULT_CATEGORY)
    nodes = extract_node_times(parts_docs, restrict=restrict, ruleset=rs)
    items = build_shipments(parts_docs, rs)
    seg_targets = rules.get("seg_targets") or {}

    # Part5「发货」实际操作人：后勤④ 与 运输段共用（逐条覆盖即取最后一条发货操作人）
    ship_op = ""
    for d5 in (parts_docs.get("Part5") or []):
        for n in d5["nodes"]:
            if n["action"] == "发货" and n["operator"]:
                ship_op = n["operator"]

    seg_rows = []
    for dept, name, n0, n1, def_target, owner, note, is_gap in rs.segs:
        cfg = seg_targets.get(name) or {}
        # 段目标天：规则页 seg_targets 优先，缺失回退代码默认（与 analysis.py 同一真源）
        target = seg_target(rules, name, def_target)
        available = bool(cfg.get("available", True))
        nd0, nd1 = nodes[n0], nodes[n1]
        t0, src0 = nd0["adopted"], nd0["src"]
        t1, src1 = nd1["adopted"], nd1["src"]
        d = _seg_days(nd0, t0, src0, nd1, t1, src1)
        if is_gap:
            judge = "—留空"
            executor, exec_src = "", ""
        else:
            # 首段 = 从流程起点「提案」出发的那一段（膜的 提案→取数 / 壳的 提案→签样）；
            # 手机壳的提案是"完成后补录"，负值属记录约定 → 宽容为中性「—补录」
            judge = _seg_judge(d, target, name,
                               backfill_ok=(n0 == ORIGIN_NODE
                                            and rs.proposal_backfilled))
            # 寄送→拍摄交付 归属起点节点执行人，其余段取终点节点执行人
            op_node = n0 if name == "寄送→拍摄交付" else n1
            # 审批动作 > 配置责任人 > 审批记录（当前负责人/历史审批人）
            executor, exec_src = _resolve_executor(nodes[op_node], owner,
                                                   parts_docs)
            # 首段终点早于本单建单日 → 先线下办理、后补录审批（实测 60+ 个 SKU 如此），
            # 小负值是业务现实而非配对错误，加注说明避免被误读为数据异常
            if n0 == ORIGIN_NODE and d is not None and d < 0:
                note = f"{note}；{n1}早于本单建单日 → 先线下办理、后补录审批"
        seg_rows.append({
            "dept": dept, "seg": name, "from": n0, "to": n1,
            "start": _disp(t0, src0), "end": _disp(t1, src1),
            "t0": t0, "t1": t1, "src0": src0, "src1": src1,
            "days": _fmt_days(d), "target": None if is_gap else target,
            "judge": judge, "owner": owner, "executor": executor,
            "execSrc": exec_src,
            "note": note, "gap": is_gap, "assessed": available and not is_gap,
        })

    # 后勤④ 采购→发货末批（多货件取末批发货日期）
    dept, name, n0, def_target, owner, note = rs.l4
    cfg = seg_targets.get(name) or {}
    # 段目标天：规则页 seg_targets 优先，缺失回退代码默认（与 analysis.py 同一真源）
    target = seg_target(rules, name, def_target)
    nd_purchase = nodes[n0]
    ship_days = [it["ship"] for it in items if it["ship"] is not None]
    last_ship = max(ship_days) if ship_days else None
    d_l4 = _seg_days(nd_purchase, nd_purchase["adopted"], nd_purchase["src"],
                     {"sys_date_only": True}, last_ship, "系统")
    seg_rows.append({
        "dept": dept, "seg": name, "from": n0, "to": "发货末批(Part5)",
        "start": _disp(nd_purchase["adopted"], nd_purchase["src"]),
        "end": _fmt(last_ship), "t0": nd_purchase["adopted"], "t1": last_ship,
        "src0": nd_purchase["src"], "src1": "系统",
        "days": _fmt_days(d_l4), "target": target,
        "judge": _seg_judge(d_l4, target), "owner": owner,
        "executor": ship_op or owner,
        "execSrc": "审批动作·发货" if ship_op else ("配置" if owner else ""),
        "note": note, "gap": False,
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
        over = (target is not None and stall_days is not None
                and stall_days > target)
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
            "execSrc": "配置" if owner else "",
            "note": "Part5渠道已立项但无货件号·超时未发货异常(独立于采购→发货末批)",
            "gap": False, "assessed": False, "noShip": True,
        })

    # 运输汇总 + 4国 + 在途（执行人 = Part5 发货操作人，见 transport_rows 文档）
    seg_rows.extend(transport_rows(items, rs, ship_op=ship_op, today=today))

    # 甘特图行按部门归块排序：产品 → 留空 → 运营 → 后勤 → 运输 → 4国
    seg_rows.sort(key=lambda r: rs.g_order.get(r["dept"], 9))
    return seg_rows, nodes, items


def _dept_final(nodes, parts_docs, ruleset=None):
    """三部门终节点 → {部门: (时间|None, 说明)}。
    产品部=素材交付(rs.product_end_node)；运营部=max(上架,广告)；后勤部=最晚渠道仓到货。"""
    result = {}
    rs = ruleset or rules_for(DEFAULT_CATEGORY)
    name = rs.product_end_node                       # 素材交付 := 节点「素材加工」
    t_prod = (nodes.get(name) or {}).get("adopted")
    result["产品部"] = (t_prod, "完成" if t_prod else f"缺节点: 素材交付({name})")

    t_up, t_ad = nodes["上架"]["adopted"], nodes["广告"]["adopted"]
    if t_up is None and t_ad is None:
        result["运营部"] = (None, "缺节点: 上架/广告设置")
    else:
        vals = [v for v in (t_up, t_ad) if v is not None]
        missing = [n for n, v in (("上架", t_up), ("广告设置", t_ad)) if v is None]
        result["运营部"] = (max(vals),
                            "完成" if not missing else f"缺节点: {','.join(missing)}")

    arrs = [it["arr"] for it in build_shipments(parts_docs, ruleset) if it["arr"]]
    if not arrs:                                   # 兜底：Part8 业务时间列
        for d in (parts_docs.get("Part8") or []):
            v = _parse_dt(_clean(d["raw"].get("渠道仓到货时间")))
            if v:
                arrs.append(v)
    result["后勤部"] = (max(arrs) if arrs else None,
                        "完成" if arrs else "缺节点: 渠道仓到货")
    return result


def load_product_catalog():
    """读商品资料表 → {sku: {grade, dailyMin, dailyMax, price, priceType}}（损益输入）。

    行内区间为空但档位存在时，用 `product_rules.range_of()` 兜底（A1 档位表为准），
    这样即便商品资料只填了评级列也能算出销量区间。
    """
    out = {}
    with SessionLocal() as session:
        rows = session.execute(
            select(ProductCatalog.sku, ProductCatalog.grade,
                   ProductCatalog.daily_min, ProductCatalog.daily_max,
                   ProductCatalog.price, ProductCatalog.price_type)
        ).all()
    for sku, grade, dmin, dmax, price, ptype in rows:
        if grade and (dmin is None or dmax is None):
            lo, hi = range_of(grade)
            dmin = dmin if dmin is not None else lo
            dmax = dmax if dmax is not None else hi
        out[str(sku).strip()] = {
            "grade": grade or "",
            "dailyMin": float(dmin) if dmin is not None else None,
            "dailyMax": float(dmax) if dmax is not None else None,
            "price": float(price) if price is not None else None,
            "priceType": ptype or "",
        }
    return out


def compute_review(category=None):
    """全量计算复盘结果（段级动作映射口径）。

    category: 品类（手机膜/手机壳）→ 决定用哪一套节点/段/路线评级规则；
              None → 默认品类（手机膜），行为与改造前一致。
    """
    rs = rules_for(category)
    rules, _ = get_rules()
    docs = _load_sku_docs(rs.key)          # 只取本品类批次（品类隔离）
    products = load_product_catalog()

    # 换号对照（旧号→新号）：单一真源 app/sku_alias.py（SKU登记表.xlsx · SKU换号 子表 → DB）
    aliases = load_aliases()
    alias_in_map = {}                      # 新号 -> [旧号…]（反向标注用）
    for _o, _v in aliases.items():
        alias_in_map.setdefault(_v.get("new") or "", []).append(_o)
    for _v in alias_in_map.values():
        _v.sort()

    # 人工登记（业务上不再走完 → 免催办）：单一真源 app/sku_hold.py（SKU登记表.xlsx · 人工登记 子表 → DB）
    holds = load_holds()

    # ---- 按 SKU 组织（同 SKU 跨批次合并；同 Part 重复单据只留最新） ----
    by_sku = {}
    dup_total = 0
    for d in docs:
        rec = by_sku.setdefault(
            d["sku"], {"model": "", "parts": {}, "publish_raw": None,
                       "first_at": None})   # first_at = 立项时间（最早单据创建时间）
        if not rec["model"] and d["model"]:
            rec["model"] = d["model"]
        rec["parts"].setdefault(d["part"], []).append(d)
        if d["part"] == "Part1" and rec["publish_raw"] is None:
            pub = d["raw"].get("官方发布时间")
            if pub not in (None, "", "nan"):
                rec["publish_raw"] = pub
        ct = d.get("create_time")
        if ct is not None:
            if isinstance(ct, str):
                ct = _parse_dt(ct)
            if ct is not None and (rec["first_at"] is None or ct < rec["first_at"]):
                rec["first_at"] = ct

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

        # P0-3：Part1 多轮提交 → 轮次统计 + 异常标注。
        #   取值口径＝最早一轮，已在 extract_node_times 内统一落地，
        #   故这里不再构造 restrict（该参数已废弃）。
        _p1_base, p1_rounds, multi_round = resolve_part1_rounds(
            parts_docs.get("Part1") or [], ruleset=rs)
        seg_rows, nodes, items = review_sku_segments(sku, parts_docs, rules,
                                                     ruleset=rs)

        # 开发类型：提前开发 = 官方发布时间 >= 提案(提交申请)时间
        pub = _parse_dt(info["publish_raw"])
        t_sub = nodes["提案"]["adopted"]
        dev = "提前开发" if (pub and t_sub and pub >= t_sub) else "常规开发"

        dept = _dept_final(nodes, parts_docs, rs)

        # 人工登记（免催办）优先于换号：业务已决定「不再走完流程」时，把部门结论就地
        # 改写为「人工豁免·不催办」，下游（复盘页/甘特图/预警）拿到的就是人话，
        # 而不是一串待解释的「缺节点」。
        _hd = holds.get(str(sku).strip().upper()) or {}
        if _hd:
            _mark = "人工豁免·不催办"
            dept = {k: (v[0], (_mark if v[1] != "完成" else v[1]))
                    for k, v in dept.items()}

        # 换号（旧号 → 新号）：旧号的「缺节点」不是卡点，是流程被新号接管了。
        # 这里把部门结论就地改写为「已换号 → 新号（不催办）」，下游（复盘页/甘特图/
        # 预警）拿到的就是人话，而不是一串待解释的「缺节点」。
        al_out = {} if _hd else (aliases.get(str(sku).strip().upper()) or {})
        if al_out:
            _new = al_out.get("new") or ""
            _mark = f"已换号 → {_new}（不催办）"
            dept = {k: (v[0], (_mark if v[1] != "完成" else v[1]))
                    for k, v in dept.items()}

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
            # target=None（规则页清空该段目标天）= 该段不设目标 → 不计贡献、不进段统计
            if (r["gap"] or not r["assessed"] or r["days"] is None
                    or r["target"] is None):
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

        # P1-3 空运口径损益：仅三部门全完成的 SKU 才有确定的"最晚空运到货"
        #   起点节点由品类规则集给出（膜=取数 / 壳=提案）
        assess = (_assess_one(sku=sku, nodes=nodes, items=items,
                              product=products.get(str(sku).strip()),
                              rules=rules,
                              start_node=rs.start_node) if valid else None)
        # P0-5 核心原因因果链：把超期归因到「漏报嫌疑 / 搁置 / 运输」
        root_cause = _locate_root_cause(seg_rows, nodes, parts_docs,
                                        multi_round=multi_round,
                                        ruleset=rs)

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
            # P0-3 多轮提交标记（总览可筛）/ P1-3 推迟天数 / P0-5 根因定性
            "multiRound": bool(multi_round.get("flag")),
            "delayDays": (assess or {}).get("delayDays"),
            "rootCauseType": (root_cause or {}).get("type"),
            # 换号（2026-09-28）：总览行也带上，前端可在 SKU 旁标注
            # 「已换号 → 新号」（旧号）或「承接 <旧号>」（新号）。
            "aliasOut": (al_out.get("new") or "") or None,
            "aliasIn": alias_in_map.get(str(sku).strip().upper(), []),
            # 人工登记（2026-09-28）：holdOut=True 表示该 SKU 业务上已不需走完流程
            "holdOut": bool(_hd),
            "noChase": bool(al_out) or bool(_hd),
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
            # P0-1：立项时间（最早单据创建时间），也是 rows 的排序键
            "firstProposalAt": _fmt(by_sku[sku]["first_at"]),
            "dept": {k: {"time": _fmt(v[0]), "note": v[1]}
                     for k, v in dept.items()},
            "nodes": {name: {"at": _disp(nd["adopted"], nd["src"]),
                             "src": nd["src"],
                             "iso": (nd["adopted"].strftime("%Y-%m-%d %H:%M:%S")
                                     if nd["adopted"] else None),
                             # 2026-11-06 双时间并列（对齐「流程复盘」核对表）：
                             # sys  = 系统业务日期（表单里人填的那个日期，缺失就是缺失）
                             # audit= 钉钉审批里带 audit 备注的上报时间（补录，常晚于实际）
                             # 两者都给出，判 "取数口径对不对" 时不用再翻原始单据。
                             "sys": _fmt(nd["sys"]),
                             "sysField": nd["sys_field"],
                             "audit": _fmt(nd["audit"]),
                             "auditOp": nd["audit_op"],
                             "action": _fmt(nd["action"]),
                             "actionOp": nd["action_op"]}
                      for name, nd in nodes.items()},
            "sampleCycle": _sample_cycle(nodes),
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
            # P0-3 多轮提交 / P0-5 因果链 / P1-3 损益（Review 详情页直接消费）
            "rounds": p1_rounds,
            "multiRound": multi_round,
            "rootCause": root_cause,
            "assessment": assess,
            # 换号（2026-09-28）：旧号→新号 / 反向承接。noChase=True 表示
            # 「缺节点」已由换号解释，不应再当成卡点催办（加急预警据此移出 P0/P1/P2）。
            "aliasOut": ({"new": al_out.get("new") or "",
                          "note": al_out.get("note") or "",
                          "effectiveAt": al_out.get("effectiveAt") or ""}
                         if al_out else None),
            "aliasIn": alias_in_map.get(str(sku).strip().upper(), []),
            # 人工登记（2026-09-28）：业务上已不需走完流程 → 免催办。与换号并列，
            # 但优先级更高（人工决策 > 业务登记）：两者同时存在时只认这条。
            "hold": ({"reason": _hd.get("reason") or "",
                      "registeredAt": _hd.get("registeredAt") or ""}
                     if _hd else None),
            "noChase": bool(al_out) or bool(_hd),
            "chaseReason": ("人工登记：业务上已不需走完流程，不催办" if _hd
                            else ("换号：流程已由新号承接，旧号不催办"
                                  if al_out else "")),
        }

    # ---- 汇总 ----
    # P0-1：SKU 行按立项时间从新到久排序（缺时间排最后，SKU 名兜底）
    rows.sort(key=lambda r: (by_sku[r["sku"]]["first_at"] or datetime.min,
                             r["sku"]), reverse=True)
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
                                  key=lambda kv: (rs.g_order.get(kv[0][1], 9),
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
        # 品类与口径元信息（避免"静默用了哪套规则"）：起点/评级线/是否待校准
        **rs.describe(),
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
        # 换号对照快照（复盘页/甘特图直接用；与预警共用 app/sku_alias.py 同一真源）
        #   count   = 全库登记对数（含其它品类）
        #   outN    = 本品类里命中为「旧号」的 SKU 数（⚠️ 不含人工登记，单独计）
        #   inN     = 本品类里命中为「新号」的 SKU 数
        "aliases": {
            "out": aliases, "in": alias_in_map, "count": len(aliases),
            "outN": sum(1 for r in rows if r.get("aliasOut")),
            "inN": sum(1 for r in rows if r.get("aliasIn")),
        },
        # 人工登记快照（业务上不再走完 → 免催办；与预警共用 app/sku_hold.py 同一真源）
        #   count = 全库登记数（含其它品类）；outN = 本品类命中数
        "holds": {
            "rows": holds, "count": len(holds),
            "outN": sum(1 for r in rows if r.get("holdOut")),
        },
        "skuDetails": sku_details,
    }


# ---------------------------------------------------------------------------
# 缓存（以 batch 表状态为指纹）
# ---------------------------------------------------------------------------
_cache = {"fp": None, "data": None}          # 兼容旧引用：镜像"最近一次计算"的槽位
_cache_by_cat = {}                           # 品类 key -> {"fp", "data"}（真正的缓存）
_cache_lock = threading.Lock()


def _db_fingerprint(category=None):
    """批次表状态指纹。按品类过滤 —— 手机壳导批次不该让手机膜缓存失效
    （更重要的是：手机壳数据变化必须能让手机壳自己失效）。"""
    live = _live_batch_ids(category)
    if not live:
        return "empty"
    with SessionLocal() as session:
        batches = session.execute(
            select(Batch.imported_at, Batch.doc_count)
            .where(_cat_cond(category), Batch.id.in_(live))).all()
    if not batches:
        return "empty"
    latest = max((b[0] or datetime.min) for b in batches)
    return f"{len(batches)}|{latest.strftime('%Y%m%d%H%M%S')}|{sum(b[1] or 0 for b in batches)}"


def _product_fingerprint():
    """商品资料表（评级档位+单价）指纹：行数 + 最近更新时间。

    计入复盘缓存键 —— 商品资料重导入后损益自动重算，无需额外失效逻辑。
    """
    with SessionLocal() as session:
        n, latest = session.execute(
            select(func.count(ProductCatalog.sku),
                   func.max(ProductCatalog.updated_at))).one()
    return f"{n or 0}|{latest.strftime('%Y%m%d%H%M%S') if latest else '-'}"


def fingerprint_for(category=None) -> str:
    """指定品类的复盘指纹（= get_review_data 的缓存键）。

    对外暴露是为了让 /api/history 取指纹时**与数据同品类**：
    读"最近一次计算"的镜像会串味（算过手机壳再取快照，会把手机膜的 KPI
    挂到手机壳的指纹上）。
    """
    from ..rule_service import get_rules as _gr
    cat = rules_for(category).key
    return f"{cat}|{_db_fingerprint(cat)}|{_gr()[1]}|{_product_fingerprint()}"


def get_review_data(category=None):
    """取复盘全量数据（带锁缓存：指纹一致直接返回；否则持锁重算后原子提交，
    避免并发请求在重算中途拿到旧数据或重复计算）。

    缓存指纹含**品类**，且**按品类分槽**：不同品类规则集不同，结果不可互相复用；
    分槽还消除了一类致命死锁 —— 见下方 record_snapshot 处的说明。
    """
    cat = rules_for(category).key
    fp = fingerprint_for(category)
    with _cache_lock:
        slot = _cache_by_cat.get(cat)
        if slot is not None and slot["fp"] == fp:
            data = slot["data"]
        else:
            data = compute_review(cat)
            _cache_by_cat[cat] = {"fp": fp, "data": data}
        # 镜像给"最近一次计算"，history._current_fingerprint() 与旧引用读这里
        _cache["fp"] = fp
        _cache["data"] = data
    # 快照必须在缓存锁外调用 —— 同线程会重入：
    #     get_review_data → record_snapshot → _build_kpi
    #       → compute_transport/_get_alerts_cached/compute_scenarios
    #       → get_review_data（再次进入本函数）
    # 若在 _cache_lock 内调用，上面这条链会自己撞死在自己的非重入锁上。
    # 更隐蔽的是：分支品类（如手机壳）重算时，嵌套的 get_review_data() 走的是
    # 默认品类（手机膜）—— 单槽缓存必然未命中 → 又会调 record_snapshot →
    # history._lock 已被本线程持有 → 整个后端永久卡死。
    # 所以这里有两道防线：缓存**分槽**（消除未命中）＋ history 侧的重入保护。
    try:
        from .history import record_snapshot
        record_snapshot(fp, data, category=cat)
    except Exception:
        pass
    return data


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@router.get("/overview")
def review_overview(category: str = None):
    """大流程复盘总览：KPI + 分布 + 卡点TOP + 段级统计 + 人员 + 全部 SKU 行。

    category: 品类（手机膜/手机壳），缺省 = 默认品类（手机膜）。
    """
    try:
        data = get_review_data(category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"复盘计算失败: {e}")
    return {k: v for k, v in data.items() if k != "skuDetails"}


@router.get("/sku/{code}")
def review_sku(code: str, category: str = None):
    """单个 SKU 的复盘档案（段级动作映射明细、货件运输、三部门终节点）。"""
    try:
        data = get_review_data(category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"复盘计算失败: {e}")
    detail = data["skuDetails"].get(code)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"SKU 不存在或无已结束单据: {code}")
    return {"sku": code, **detail}


@router.get("/overview/export")
def export_review_overview(category: str = None):
    """导出复盘总览 SKU 行 xlsx（含开发类型/三部门时间/卡点/贡献）。"""
    import io
    import urllib.parse

    import pandas as pd
    from fastapi.responses import StreamingResponse

    try:
        data = get_review_data(category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导出失败: {e}")
    cols = ["SKU", "型号", "开发类型", "有效复盘", "无效原因",
            "产品部·素材交付", "运营部·上架/广告", "后勤部·渠道仓到货",
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
            "产品部·素材交付": r["prodAt"] or "",
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
    final_nodes = {"产品部": "素材交付(Part6 素材加工)",
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
def sku_report(code: str, category: str = None):
    """单 SKU 复盘报告（markdown 下载）。"""
    try:
        data = get_review_data(category)
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


# ---------------------------------------------------------------------------
# P0-6 · 单 SKU 核查报告 xlsx
# ---------------------------------------------------------------------------
@router.get("/sku/{code}/checkreport")
def sku_checkreport(code: str, category: str = None):
    """单 SKU 核查报告 xlsx：甘特（按部门归块）+ 段级明细 + 末尾「人工核对」空白列。

    设计给"打印出来人工过一遍"用：判定列按超期标红，人工核对列带下拉可选。
    """
    try:
        data = get_review_data(category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"复盘计算失败: {e}")
    detail = data["skuDetails"].get(code)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"SKU 不存在或无已结束单据: {code}")
    try:
        payload = _skucard_xlsx(code, detail, data,
                                root_cause=detail.get("rootCause"),
                                assess=detail.get("assessment"),
                                ruleset=rules_for(category))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"核查报告生成失败: {e}")

    import io
    import urllib.parse

    from fastapi.responses import StreamingResponse
    filename = urllib.parse.quote(
        f"SKU核查报告_{code}_{datetime.now().strftime('%Y%m%d')}.xlsx")
    return StreamingResponse(io.BytesIO(payload), headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
    }, media_type="application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet")


# ---------------------------------------------------------------------------
# P1-4 · 事后评估三指标
# ---------------------------------------------------------------------------
def _assessment_items(data):
    """从 skuDetails 汇总评估项（仅三部门全完成、可算损益的 SKU）。"""
    items = [d["assessment"] for d in data["skuDetails"].values()
             if d.get("assessment")]
    items.sort(key=lambda x: (-(x.get("delayDays") or 0), x["sku"]))
    return items


@router.get("/assessment")
def review_assessment(category: str = None):
    """事后评估：时效（起点→最晚空运到货 vs 底线）+ 销量 + 金额（P1-3/P1-4）。

    口径：时钟起点 = 品类规则集的 start_node（手机膜=取数 / 手机壳=提案）；
    推迟天数 = 起点→最晚空运到货 − 底线(60)；
    亏损 = 推迟天数 × 评级日均区间 × 单价；**增益仅提前完成才算**。
    """
    try:
        data = get_review_data(category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"复盘计算失败: {e}")
    rs = rules_for(category)
    items = _assessment_items(data)
    return {
        "computedAt": data["computedAt"],
        "category": rs.key,
        "startNode": rs.start_node,
        "targetsProvisional": rs.provisional,
        "routeLimits": route_limits(rs),
        "items": items,
        "summary": _assess_summary(items),
    }


@router.get("/assessment/export")
def export_assessment(category: str = None):
    """事后评估三指标 xlsx（时效 / 销量 / 毛利 三 sheet）。"""
    try:
        data = get_review_data(category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"复盘计算失败: {e}")
    items = _assessment_items(data)
    rs = rules_for(category)
    try:
        payload = _assessment_xlsx(items, _assess_summary(items),
                                   computed_at=data["computedAt"],
                                   start_node=rs.start_node,
                                   provisional=rs.provisional,
                                   targets_source=rs.targets_source)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"评估导出失败: {e}")

    import io
    import urllib.parse

    from fastapi.responses import StreamingResponse
    filename = urllib.parse.quote(
        f"事后评估_{datetime.now().strftime('%Y%m%d')}.xlsx")
    return StreamingResponse(io.BytesIO(payload), headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
    }, media_type="application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet")
