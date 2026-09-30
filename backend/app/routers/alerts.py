# -*- coding: utf-8 -*-
"""
alerts.py — SKU 加急预警引擎（GET /api/alerts）
================================================
口径 1:1 移植自 流程复盘/route_check_sys.py 的 write_warn_md：

  只列未闭环（复盘无效）SKU，按优先级排列，停滞越久越靠前：
    P0 = 空运/海运超底线（空>60 / 海>69 判定=差） / 提案→取数超75天 / Part7 卡点停滞>7天
    P1 = 产品段超时（通用>23 / 不通用>38） / 停滞>7天 / 缺到货渠道（有发货未到货）
    P2 = 尚在阶段时限内的推进中 SKU（到期自动升级）

  关键段：
    提案→取数  = Part1 提案确认 → 取得手机数据（>75 天 ⚠，P0）
    产品段     = 起点(膜=取数/壳=提案) → 素材交付（=Part6「产品营销素材加工」，素材通用≤23 / 不通用≤38，✗ 计 P1）
    运营段     = 素材交付 → max(上架, 广告设置)（≤7 天）
    空运/海运  = 取数 → 该方式末批到货（Part5×Part8 按货件号关联；渠道映射：
                 东南亚=海运35 / 欧洲=空运15 / 美国=35 / 加墨美客多=海运35 / 默认空运15）
    到货国数   = Part8 到货渠道仓去重数（≥14 国达标口径）
    缺到货渠道 = Part5 发往渠道仓库 - Part8 已到货渠道

  卡点与停滞：第一个存在非「已结束」单据的 Part；停滞 = 今天 - 该 Part 未结束单据最新动态时间。

  末端终止（2026-09-28 新增）：末尾 Part 单据**全部终止**且全 SKU 无「审批中」单据的，
  判为**作废单** → 不进 P0/P1/P2 催办，改列 `voidRows`（不催办、仅备查），并识别
  「同型号、终止后重开」的**承接单**（501→G501、G448→G481 均属此例）。

  换号（2026-09-28 新增）：SKU 在流程中途**换了编码**（旧号停用、新号承接）的，旧号也不进
  催办，改列 `aliasRows`。数据源 = `SKU登记表.xlsx · SKU换号` 子表（`app/sku_alias.py`）。
  与「末端终止」的区别：作废单是**从数据里推断**的（末尾全终止 + 无在跑单据），换号是
  **业务登记**的（数据里没有可靠信号）—— 所以两者判据独立、互不覆盖，命中换号优先。

缓存：与复盘引擎共用 batch 表指纹，ETL 后自动失效。
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..category_rules import DEFAULT_CATEGORY, rules_for
from ..db import SessionLocal
from ..models import ApprovalNode, Batch, PartDoc
from ..review_rules import PART_ACTIONS, PART_LIST, PART_NAMES
from ..rule_service import get_rules
from .review import _cat_cond, _db_fingerprint, _fmt, _live_batch_ids, _parse_dt

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

OK_STATUS = {"已结束", "结束", "已通过", "已完成"}

# ---------------------------------------------------------------------------
# 「末端终止（作废单）」判定 —— 2026-09-28 加急口径修正
# ---------------------------------------------------------------------------
# 背景：「终止」此前不在 OK_STATUS 里 → 被当成「未结束」，于是卡点落在**那张已终止
# 的单据**上、停滞天数从终止日算起。实测 G448 报「Part2 停滞 447 天」、501 报
# 「Part2 停滞 340 天」，都是误催办。
#
# 证据（真机数据）：终止 = 「撤回 / 填错重开」，不进入后续环节（见流程手册 Part 说明）。
#   · 501（Xiaomi Redmi Note 15 Pro 5G）Part2 终止于 2025-10-23 14:46:00
#     → G501 开单于 14:48:47（间隔 2 分 47 秒），且 G501 已闭环（valid=True）
#   · G448（Xiaomi Poco F7 5G）Part2 终止于 2025-07-08 09:28:12
#     → G481 开单于 09:30:01（间隔 1 分 49 秒），且 G481 已闭环
#
# 判据（②与「承接单」识别在离线 route_check_sys.write_warn_md 有同源实现，改一处必须同步改另一处）：
#   ① 该 SKU 没有任何「审批中」单据 —— 流程是真的停住了，不是在跑
#   ② 最后一个有单据的 Part 里，单据状态**全部**为「终止」
# 命中即判为作废：不进 P0/P1/P2 催办清单，改列 voidRows（不催办、仅备查，不吞数据）。
VOID_TERMINATED = "终止"
VOID_NOTE = "流程已终止，不进入后续环节，不列入催办"

# ---------------------------------------------------------------------------
# 「换号（旧号 → 新号）」判定 —— 2026-09-28 新增
# ---------------------------------------------------------------------------
# 背景：G493/G494 这类 SKU 在流程中途换了编码（Galaxy S26 Pro/Edge → S26/S26+），
# 旧号的钉钉流程永远停在半路 → 复盘报「缺节点」、预警把它当卡点催办 → 多余异常。
#
# ⚠️ 与「末端终止」判据**完全独立**，不能互相覆盖：
#   · 作废单 = 从数据推断（末尾 Part 全终止 + 全 SKU 无在跑单据）；
#   · 换号   = 业务登记（流程单据里没有结构化痕迹，见 app/sku_alias.py 的说明）。
#   实测 G493/G494/G495 各自都还有 2~3 张「审批中」单据 → 永远不会被 `_void_info` 判中，
#   所以必须有这条独立通道。
#
# 真源：`资产文件\SKU登记表.xlsx` 的 `SKU换号` 子表 → MySQL `sku_alias` → app/sku_alias.py。
# 消费方：本模块（预警）、routers/review.py（复盘+甘特图）、离线 route_check_sys.py（预警 md）。
ALIAS_NOTE = "流程中途换了编码（旧号→新号），旧号不列入催办"

# ---------------------------------------------------------------------------
# 「人工登记（免催办）」判定 —— 2026-09-28 新增
# ---------------------------------------------------------------------------
# 背景：有些新品业务上**已决定不再走完流程**（渠道取消 / 并入其它型号 / 末端环节实际不执行），
# 但钉钉单据还挂在半路 → 预警把它当卡点催办 → 多余异常。
# 实测 6 例（商品资料里全部「在售」）：
#   · G495（Samsung Galaxy S26 Ultra 5G）P0 —— 卡 Part8 两张「手机上架确认」
#     （2026-03-25 起悬挂「审批中」，停滞 187 天），且空运 83>60、海运 87>69 超底线；
#   · G524~G528（Realme 16 Pro / Honor Magic8 Lite / Oppo Reno15 三兄弟）P1 ——
#     卡 Part9/Part8，自 2026-02 停滞 187~236 天。
#
# ⚠️ 与「换号」「末端终止」是三件不同的事，判据互相独立：
#   · **人工登记** = 业务登记「不再走完」→ 没有承接号（人工决策，**最先判**）；
#   · **换号**     = 旧号停下、**新号承接**（也是业务登记）；
#   · **末端终止** = **数据推断**（末尾 Part 单据全「终止」且无在跑单据）。
#   三者都单列备查、都移出 P0/P1/P2，但只有前两者认人工维护的登记表。
#
# 真源：`资产文件\SKU登记表.xlsx` 的 `人工登记` 子表 → MySQL `sku_hold` → app/sku_hold.py。
# 消费方：本模块（预警）、routers/review.py（复盘+甘特图）、离线 route_check_sys.py（预警 md）。
HOLD_NOTE = "业务上已不需走完流程，人工登记后不列入催办"

NEXT_BY_PART = {
    "Part1": "催李杰华取得手机数据",
    "Part2": "催李杰华完成签样、周奕奕采购下单",
    "Part3": "跟进本地仓到货检查",
    "Part4": "催郭晋盈完成新品首次上架",
    "Part5": "跟进渠道仓发货动态(林惠欢)",
    "Part6": "催李杰华寄送拍摄/林宇炫素材加工",
    "Part7": "催郭晋盈完成最终上架、梁子锋完成广告设置",
    "Part8": "催李晓影确认末批到货/查物流",
}

# 预警页需要的四个关键时点 → 规则集里的节点名（系统时间列兜底口径）
#   fetch  = 考核时钟起点：手机膜=取数 / 手机壳=提案（壳无取数节点）
#   deliver= 产品部终节点「素材交付」= rs.product_end_node（节点「素材加工」）
#   up = 上架 / ad = 广告
_ALERT_NODES = {"fetch": None, "deliver": None, "up": "上架", "ad": "广告"}

# 手机膜的「提案」在预警页历史上用动作名 **提案确认** 取时间（不是 NODES 里的
# 提交申请 —— 两者时间不同）。为保持改造前行为逐字节一致，这里按品类显式给出。
_PROP_ACTION = {
    "手机膜": "提案确认",
    "手机壳": "提交申请",
}


def _clean(v):
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() == "nan" else s


def transport_mode(hub):
    """按到货渠道仓返回 (运输方式名, 目标天)：2026-09 口径。"""
    h = str(hub or "")
    if "东南亚" in h:
        return "海运", 35
    if "欧洲" in h:
        return "空运/卡航", 15
    if "美国" in h:
        return "空运+海运", 35
    if "加拿大" in h or "墨西哥" in h or "美客多" in h:
        return "海运", 35
    return "空运", 15


def _ship_cls(mode_name):
    return "海运" if "海运" in (mode_name or "") else "空运"


def _days(a, b):
    """日期级差天数（口径同 route_check 系列的 date 差）。"""
    if a is None or b is None:
        return None
    return float((b.date() - a.date()).days)


def _grade(d, good, limit):
    if d is None:
        return "-"
    if d <= good:
        return "优"
    if d <= limit:
        return "良"
    return "差"


def _seg_judge(d, target):
    if d is None:
        return "-"
    return "✓" if d <= target else f"✗ 超 {d - target:.1f} 天"


# ---------------------------------------------------------------------------
# 数据装载：全部单据（不限状态）+ 节点
# ---------------------------------------------------------------------------
def _load_all_docs(category=None):
    """加载该品类全部单据（不限状态）+ 节点。按品类隔离，绝不跨品类混算。

    ⚠️ 只算「活批次」（目录仍存在）——与 review._live_batch_ids 同口径：
    钉钉每天导出最新全量后，旧批次目录被移走即退役，DB 残留记录不再参与预警。
    """
    cond = _cat_cond(category)
    live = _live_batch_ids(category)
    if not live:
        return {}
    with SessionLocal() as session:
        docs = session.execute(
            select(PartDoc.id, PartDoc.part, PartDoc.sku, PartDoc.model,
                   PartDoc.status, PartDoc.create_time, PartDoc.update_time,
                   PartDoc.finish_time, PartDoc.raw)
            .join(Batch, PartDoc.batch_id == Batch.id)
            .where(PartDoc.sku.isnot(None), cond, PartDoc.batch_id.in_(live))
        ).all()
        nodes = session.execute(
            select(ApprovalNode.doc_id, ApprovalNode.action, ApprovalNode.action_time)
            .join(PartDoc, ApprovalNode.doc_id == PartDoc.id)
            .join(Batch, PartDoc.batch_id == Batch.id)
            .where(PartDoc.sku.isnot(None), cond, PartDoc.batch_id.in_(live))
            .order_by(ApprovalNode.doc_id, ApprovalNode.seq)
        ).all()

    times_by_doc = {}
    for doc_id, act, t in nodes:
        if t is not None:
            times_by_doc.setdefault(doc_id, {}).setdefault(act, t)

    by_sku = {}
    for (doc_id, part, sku, model, status, create, update, finish, raw) in docs:
        rec = by_sku.setdefault(sku, {"model": "", "parts": {}})
        if not rec["model"] and model:
            rec["model"] = model
        rec["parts"].setdefault(part, []).append({
            "id": doc_id, "status": (status or "").strip(),
            "create": create, "update": update, "finish": finish,
            "raw": raw or {}, "times": times_by_doc.get(doc_id, {}),
        })
    return by_sku


def _pick_doc(docs):
    """同 Part 多张单据时，取关键动作覆盖最多、其次最新完成的一张。

    ⚠️ 已知遗留：`_part_of(d)` 取 `doc["part"]`，但上游 `_load_all_docs` 分组时
    并没有把这个键写进 doc（分组用外部循环变量）→ `hit` 恒为 0，实际按
    「最新完成」选主单据。此处**故意不修**：修正会改变手机膜既有预警输出，
    属于独立的行为变更，需要单独的验收。
    """
    def score(d):
        acts = PART_ACTIONS.get(_part_of(d), ())
        hit = sum(1 for a in acts if a in d["times"])
        ft = d["finish"] or d["update"] or d["create"] or datetime.min
        return (hit, ft)
    return max(docs, key=score)


def _part_of(doc):
    # docs 分组时键已按 part 组织，这里通过闭包外传入；仅为 _pick_doc 通用性保留
    return doc.get("part", "")


def _alert_specs(rs):
    """按品类给出预警页的节点规格：key -> (Part, 动作名, 系统列候选链)。

    系统列候选链直接取自规则集（`NODES` 的候选元组），因此某品类某列被停填时
    仍能按序回退 —— 与复盘页共用同一套列口径，避免两页算法不一致。
    """
    specs = {}
    for key, node in _ALERT_NODES.items():
        node = node or (rs.start_node if key == "fetch" else rs.product_end_node)
        part = rs.node_part(node)
        if part is None:                 # 该品类没有这个节点（如壳无「取数」）
            continue
        specs[key] = (part, rs.node(node)["act"], rs.sys_candidates(node))
    return specs


def _node_time(sku_data, part, action, sys_cols=None):
    """节点时间：优先该 Part 主单据的审批动作时间，兜底系统时间列（任一单据有值即用最新）。"""
    parts = sku_data["parts"]
    docs = parts.get(part) or []
    t = None
    if docs and action:
        t = _pick_doc(docs)["times"].get(action)
    if t is None and sys_cols:
        for d in docs:
            for col in sys_cols:
                v = _parse_dt(_clean(d["raw"].get(col)))
                if v and (t is None or v > t):
                    t = v
    return t


def _generic_material(sku_data):
    """素材通用判定（route_check.is_generic_material 口径）。"""
    for d in sorted((sku_data["parts"].get("Part6") or []),
                    key=lambda x: x["create"] or datetime.min, reverse=True):
        raw = d["raw"]
        decide = _clean(raw.get("是否决定拍摄"))
        ref_sku = _clean(raw.get("素材参考SKU"))
        ref_model = _clean(raw.get("素材参考型号"))
        if decide == "否" or ref_sku or ref_model:
            basis = []
            if decide == "否":
                basis.append("是否决定拍摄=否")
            if ref_sku or ref_model:
                basis.append(f"素材参考={ref_sku or ref_model}")
            return True, "；".join(basis)
        if decide == "是":
            return False, "是否决定拍摄=是(需真机拍摄)"
    return False, "未标注拍摄/参考(默认按需拍摄)"


def _shipments(sku_data, rs=None):
    """Part5×Part8 按货件号关联的逐货件列表（route_check_sys 兜底路径）。
    含渠道未发货占位条目（无货件号/时间，仅渠道仓，noShip=True）。

    rs：品类规则集。运输方式/目标天与"空运|海运"归类都从规则集取，
        避免两个品类共用一份硬编码口径（缺省仍用本模块的旧函数）。
    """
    mode_fn = rs.transport_mode if rs is not None else transport_mode
    cls_fn = rs.ship_class if rs is not None else _ship_cls
    p5, p8 = {}, {}
    no_trk_hubs, hubs_with_trk = [], set()
    for d in sku_data["parts"].get("Part5") or []:
        raw = d["raw"]
        trk = _clean(raw.get("货件号"))
        hub = _clean(raw.get("发往渠道仓库"))
        if not trk:
            if hub and hub not in no_trk_hubs:
                no_trk_hubs.append(hub)
            continue
        hubs_with_trk.add(hub)
        sd = _parse_dt(_clean(raw.get("发货日期")))
        if trk not in p5 or (sd and not p5[trk][1]):
            p5[trk] = (hub, sd)
    for d in sku_data["parts"].get("Part8") or []:
        raw = d["raw"]
        trk = _clean(raw.get("货件号"))
        ad = _parse_dt(_clean(raw.get("渠道仓到货时间")))
        if trk and (trk not in p8 or (ad and not p8[trk])):
            p8[trk] = ad
    items = []
    for trk, (hub, sd) in p5.items():
        arr = p8.get(trk)
        mode, tgt = mode_fn(hub)
        items.append({"trk": trk, "hub": hub, "ship": sd, "arr": arr,
                      "days": _days(sd, arr), "mode": mode, "target": tgt,
                      "cls": cls_fn(mode), "noShip": False})
    for hub in no_trk_hubs:
        if hub in hubs_with_trk:
            continue
        mode, tgt = mode_fn(hub)
        items.append({"trk": "", "hub": hub, "ship": None, "arr": None,
                      "days": None, "mode": mode, "target": tgt,
                      "cls": cls_fn(mode), "noShip": True})
    return items


def _pending_part(sku_data, today):
    """第一个存在非已结束单据的 Part + 停滞天数（该 Part 未结束单据最新动态时间）。"""
    for pk in PART_LIST:
        docs = sku_data["parts"].get(pk) or []
        non_ok = [d for d in docs if d["status"] not in OK_STATUS]
        if non_ok:
            latest = max(filter(None, (d["update"] or d["finish"] or d["create"]
                                       for d in non_ok)), default=None)
            stuck = max((today - latest).days, 0) if latest else None
            return pk, stuck, _fmt(latest)
    return None, None, None


# ---- 「末端终止（作废单）」实现（判据见文件头注释）----------------------------
def _doc_running(d):
    """该单据是否「还在跑」：非已结束、也非终止。"""
    return d["status"] not in OK_STATUS and d["status"] != VOID_TERMINATED


def _last_part_with_docs(data):
    """最后一个有单据的 Part（Part1→Part9 顺序里最靠后的那个）。"""
    last = None
    for pk in PART_LIST:
        if data["parts"].get(pk):
            last = pk
    return last


def _void_info(data):
    """末端终止 → (True, part, 终止时刻)；否则 (False, None, None)。"""
    if any(_doc_running(d) for docs in data["parts"].values() for d in docs):
        return False, None, None                    # 还有在跑的单据 → 不当作废
    pk = _last_part_with_docs(data)
    if pk is None:
        return False, None, None
    docs = data["parts"][pk]
    if any(d["status"] != VOID_TERMINATED for d in docs):
        return False, None, None                    # 末尾 Part 不是全终止
    at = max((d["finish"] or d["update"] or d["create"] for d in docs), default=None)
    return True, pk, at


def _first_doc_at(data):
    """该 SKU 最早一张单据的创建时间（判断承接单「重开得更晚」用）。"""
    return min((d["create"] for docs in data["parts"].values()
                for d in docs if d["create"]), default=None)


def _find_successor(sku, model, at, by_sku, valid_map):
    """承接单 = 同型号、开单时间晚于终止时刻、不是自己（取最早一张）。"""
    model = (model or "").strip()
    if not model or at is None:
        return None
    cands = []
    for other, od in by_sku.items():
        if other == sku or (od.get("model") or "").strip() != model:
            continue
        first = _first_doc_at(od)
        if first and first > at:
            cands.append({"sku": other, "firstAt": _fmt(first),
                          "valid": bool(valid_map.get(other))})
    cands.sort(key=lambda x: x["firstAt"])
    return cands[0] if cands else None


def _successor_note(successor):
    if not successor:
        return "无同型号承接单，疑似型号取消或重开未导入，请确认"
    return (f"已由 {successor['sku']} 于 {successor['firstAt']} 重开"
            + ("（承接单已闭环）" if successor["valid"] else "（承接单进行中）"))


# ---- 「换号（旧号 → 新号）」实现（数据源见文件头注释）--------------------------
def _latest_dyn(data):
    """该 SKU 全部单据里最新的动态时间（更新 > 完成 > 创建）。"""
    return max((d["update"] or d["finish"] or d["create"]
                for docs in data["parts"].values() for d in docs
                if (d["update"] or d["finish"] or d["create"])), default=None)


def _alias_conclusion(new_sku, new_valid, new_has_flow):
    """结论文案：换号后新号到底是闭环、在跑，还是压根没导进来。"""
    if not new_has_flow:
        return f"已换号 → {new_sku}（新号在本品类暂无流程单据，请确认是否已导入）"
    if new_valid:
        return f"已换号 → {new_sku}（新号已闭环，旧号不再催办）"
    return f"已换号 → {new_sku}（新号流程进行中，旧号不再催办）"


def _alias_row(sku, data, info, by_sku, valid_map, today):
    """换号旧号的一行。新号状态从同一份 by_sku / valid_map 取，不另查库。"""
    new = info.get("new") or ""
    new_data = by_sku.get(new)
    last = _latest_dyn(data)
    return {
        "sku": sku,
        "model": data.get("model") or "",
        "newSku": new,
        "note": info.get("note") or "",
        "effectiveAt": info.get("effectiveAt") or "",
        "lastDynamic": _fmt(last),
        "stuckDays": (max((today - last).days, 0) if last else None),
        "newHasFlow": new_data is not None,
        "newValid": bool(valid_map.get(new)) if new_data is not None else None,
        "conclusion": _alias_conclusion(new, bool(valid_map.get(new)),
                                        new_data is not None),
        "note2": ALIAS_NOTE,
    }


# ---- 「人工登记（免催办）」实现（数据源见文件头注释）--------------------------
def _hold_row(sku, data, info, today):
    """人工登记 SKU 的一行（不催办、仅备查）。"""
    last = _latest_dyn(data)
    pP, stuck, _ = _pending_part(data, today)
    return {
        "sku": sku,
        "model": data.get("model") or "",
        "reason": info.get("reason") or "",
        "registeredAt": info.get("registeredAt") or "",
        "pendingPart": pP,
        "pendingPartName": PART_NAMES.get(pP, pP or "") if pP else "",
        "lastDynamic": _fmt(last),
        "stuckDays": (max((today - last).days, 0) if last else None),
        "conclusion": "业务上已不需走完流程，人工登记后不列入催办",
        "note2": HOLD_NOTE,
    }


# ---------------------------------------------------------------------------
# 计算
# ---------------------------------------------------------------------------
def compute_alerts(category=None):
    """加急预警计算。category 缺省 = 默认品类（手机膜），行为与改造前一致。

    品类隔离发生在两处：`_load_all_docs` 只取本品类批次；`_alert_specs` 给出
    本品类的节点/动作/系统列（手机壳没有「取数」，起点节点即「提案」）。
    """
    today = datetime.now()
    rs = rules_for(category)
    specs = _alert_specs(rs)
    prop_action = _PROP_ACTION.get(rs.key, "提案确认")
    rules, _ = get_rules()
    prod_gen = rules["product_target_generic"]
    prod_non = rules["product_target_non_generic"]
    ops_t = rules["ops_target"]
    prop_days = rules["proposal_stale_days"]
    air_good, air_lim = rules["air_good"], rules["air_limit"]
    sea_good, sea_lim = rules["sea_good"], rules["sea_limit"]
    country_min = rules["country_min"]
    stuck_t = rules["stuck_days"]
    by_sku = _load_all_docs(rs.key)

    def _nt(data, key):
        """按品类规格取节点时间；该品类没有这个节点 → None（绝不跨节点兜底）。"""
        spec = specs.get(key)
        return _node_time(data, spec[0], spec[1], spec[2]) if spec else None

    # 复盘引擎的有效性结果（已结束单据口径）
    from .review import get_review_data
    review = get_review_data(rs.key)
    valid_map = {sku: d["valid"] for sku, d in review["skuDetails"].items()}

    # 换号对照（旧号 → 新号）。真源 = 独立登记表 `SKU登记表.xlsx·SKU换号 子表` → DB。
    # DB/登记表缺失时返回 {} → 行为与改造前完全一致。
    from ..sku_alias import load_aliases
    aliases = load_aliases()

    # 人工登记（业务上不再走完 → 免催办）。真源 = `SKU登记表.xlsx·人工登记 子表` → DB。
    # 同样缺失即空 → 行为与改造前一致。
    from ..sku_hold import load_holds
    holds = load_holds()

    rows = []
    void_rows = []
    alias_rows = []
    hold_rows = []
    for sku, data in by_sku.items():
        if valid_map.get(sku, False):
            continue                       # 已闭环不列入（route_check_sys 口径）

        # 判定次序（2026-09-28）：**人工登记 > 换号 > 末端终止 > 催办**。
        # 三者都命中时按「人工决策 > 业务登记 > 数据推断」取信，且都单列备查、不进催办。
        _hd = holds.get(str(sku).strip().upper())
        if _hd:
            hold_rows.append(_hold_row(sku, data, _hd, today))
            continue

        # 换号（业务登记）优先于末端终止（数据推断）：两者都命中时，
        # 「业务说它换号了」比「数据看着像作废」更可信。
        _al = aliases.get(str(sku).strip().upper())
        if _al:
            alias_rows.append(_alias_row(sku, data, _al, by_sku, valid_map, today))
            continue

        # 末端终止（作废单）：不进催办清单，单列备查 —— 2026-09-28 口径修正
        is_void, void_part, void_at = _void_info(data)
        if is_void:
            succ = _find_successor(sku, data.get("model"), void_at, by_sku, valid_map)
            void_rows.append({
                "sku": sku, "model": data.get("model") or "",
                "part": void_part,
                "partName": PART_NAMES.get(void_part, void_part or ""),
                "terminatedAt": _fmt(void_at),
                "stuckDays": (max((today - void_at).days, 0) if void_at else None),
                "successor": succ,
                "successorNote": _successor_note(succ),
                "note": VOID_NOTE,
            })
            continue

        t_prop = _node_time(data, "Part1", prop_action)
        t_fetch = _nt(data, "fetch")
        # t_prod_end = 素材交付（Part6 产品营销素材加工，audit 优先）
        t_prod_end = _nt(data, "deliver")
        t_up = _nt(data, "up")
        t_ad = _nt(data, "ad")

        generic, g_basis = _generic_material(data)
        prod_target = prod_gen if generic else prod_non
        d_prod = _days(t_fetch, t_prod_end)
        prod_judge = _seg_judge(d_prod, prod_target)
        d_ops = _days(t_prod_end, max([x for x in (t_up, t_ad) if x], default=None))
        ops_judge = _seg_judge(d_ops, ops_t)
        d_prop = _days(t_prop, t_fetch)
        prop_stale = d_prop is not None and d_prop > prop_days

        items = _shipments(data, rs)
        air_last = max([it["arr"] for it in items
                        if it["cls"] == "空运" and it["arr"]], default=None)
        sea_last = max([it["arr"] for it in items
                        if it["cls"] == "海运" and it["arr"]], default=None)
        d_air = _days(t_fetch, air_last)
        d_sea = _days(t_fetch, sea_last)
        air_judge = _grade(d_air, air_good, air_lim)
        sea_judge = _grade(d_sea, sea_good, sea_lim)

        def _ship_detail(cls):
            """逐货件明细：按到货时间倒序，首条即决定该方式总耗时的末批到货。"""
            good, lim = (air_good, air_lim) if cls == "空运" else (sea_good, sea_lim)
            out = []
            for it in items:
                if it["cls"] != cls:
                    continue
                fd = _days(t_fetch, it["arr"])
                out.append({
                    "trk": it["trk"], "hub": it["hub"], "mode": it["mode"],
                    "ship": _fmt(it["ship"]), "arr": _fmt(it["arr"]),
                    "transit": _days(it["ship"], it["arr"]),   # 发货→到货
                    "fetchDays": fd,                            # 取数→到货（与总耗时同口径）
                    "judge": ("未发货" if it.get("noShip")
                              else _grade(fd, good, lim) if it["arr"]
                              else "未到货"),
                    "noShip": bool(it.get("noShip")),
                })
            out.sort(key=lambda x: (x["arr"] or ""), reverse=True)
            return out

        air_detail = _ship_detail("空运")
        sea_detail = _ship_detail("海运")

        hubs8 = sorted({_clean(d["raw"].get("到货渠道仓"))
                        for d in data["parts"].get("Part8") or []
                        if _clean(d["raw"].get("到货渠道仓"))})
        p5_hubs = {_clean(h) for h in (it["hub"] for it in items) if h}
        no_ship_hubs = {it["hub"] for it in items
                        if it.get("noShip") and it["hub"]}
        missing = sorted(p5_hubs - set(hubs8) - no_ship_hubs)

        pP, stuck, last_dyn = _pending_part(data, today)
        part7_stuck = pP == "Part7" and (stuck or 0) > stuck_t

        # ---- 加急原因 ----
        def _worst_loc(detail):
            """卡点定位文案：末批到货（决定总耗时的那条货件）。"""
            if not detail:
                return ""
            w = detail[0]
            if not w["arr"]:
                return ""
            return f" ← {w['hub']}·货件{w['trk']}（{w['arr'][:10]}到货）"

        reason = []
        if air_judge == "差":
            reason.append(f"空运耗时{d_air:.0f}天>{air_lim}天底线{_worst_loc(air_detail)}")
        if sea_judge == "差":
            reason.append(f"海运耗时{d_sea:.0f}天>{sea_lim}天底线{_worst_loc(sea_detail)}")
        if prop_stale:
            reason.append(f"提案→取数超{prop_days}天(间隔{d_prop:.0f}天)")
        if str(prod_judge).startswith("✗"):
            reason.append(f"产品段超时({d_prod:.0f}/{prod_target}天,素材{'通用' if generic else '不通用'})")
        if ops_judge.startswith("✗"):
            reason.append(f"运营段超时({d_ops:.0f}/{ops_t}天)")
        if (stuck or 0) > stuck_t:
            reason.append(f"{pP}停滞{stuck}天")
        if missing:
            reason.append("有发货未到货渠道:" + "、".join(missing[:3]) +
                          ("…" if len(missing) > 3 else ""))
        if no_ship_hubs:
            reason.append("渠道未发货(超时):" + "、".join(sorted(no_ship_hubs)))
        if not reason:
            reason.append("流程未完成，需催办闭环")

        # ---- 下一步 ----
        todo = []
        if pP and pP != "Part7" and pP in NEXT_BY_PART:
            todo.append(NEXT_BY_PART[pP])
        if t_prod_end is not None:          # 运营阶段已到才催上架/广告
            if t_up is None:
                todo.append("催郭晋盈完成最终上架")
            if t_ad is None:
                todo.append("催梁子锋完成广告设置")
        if missing:
            todo.append("查已发货未到货渠道物流")
        if not todo:
            todo.append(NEXT_BY_PART.get(pP, "推进闭环并重导钉钉单据核对"))

        # ---- 优先级（route_check_sys 口径）----
        if air_judge == "差" or sea_judge == "差" or prop_stale or part7_stuck:
            prio = "P0"
        elif (str(prod_judge).startswith("✗") or (stuck or 0) > stuck_t
              or missing or no_ship_hubs):
            prio = "P1"
        else:
            prio = "P2"

        rows.append({
            "priority": prio, "sku": sku, "model": data["model"],
            "pendingPart": pP, "pendingPartName": PART_NAMES.get(pP, "") if pP else "",
            "stuckDays": stuck, "lastDynamic": last_dyn,
            "prodSeg": {"days": d_prod, "target": prod_target,
                        "judge": prod_judge,
                        "seg": f"{rs.start_node}→素材交付",
                        "basis": ("素材通用·" if generic else "素材不通用·") + g_basis},
            "opsSeg": {"days": d_ops, "target": ops_t, "judge": ops_judge,
                       "seg": "素材交付→max(上架,广告设置)"},
            "proposal": {"days": d_prop, "stale": prop_stale,
                         "target": prop_days},
            "air": {"days": d_air, "judge": air_judge,
                    "shipments": len([it for it in items
                                      if it["cls"] == "空运" and not it.get("noShip")]),
                    "detail": air_detail},
            "sea": {"days": d_sea, "judge": sea_judge,
                    "shipments": len([it for it in items
                                      if it["cls"] == "海运" and not it.get("noShip")]),
                    "detail": sea_detail},
            "countries": len(hubs8), "countryMin": country_min,
            "missingHubs": missing,
            "reasons": reason, "nextSteps": todo,
        })

    order = {"P0": 0, "P1": 1, "P2": 2}
    rows.sort(key=lambda r: (order[r["priority"]], -(r["stuckDays"] or 0), r["sku"]))

    # 作废单排序：「无承接单」在前（更可能真需要人确认是不是漏重开），再按 SKU
    void_rows.sort(key=lambda r: (r["successor"] is not None, r["sku"]))

    # 换号排序：新号没流程的在前（更需要人确认），再按 SKU
    alias_rows.sort(key=lambda r: (r["newHasFlow"], r["sku"]))

    # 盯数清单：按卡点环节归组
    watch = {}
    for r in rows:
        watch.setdefault(r["pendingPart"] or "未知", []).append(r["sku"])
    watch_list = [{"part": k, "partName": PART_NAMES.get(k, "未知环节"),
                   "skuCount": len(v), "skus": sorted(v)}
                  for k, v in sorted(watch.items())]

    # 数据新鲜度：全库最新动态距今天数
    latest_dyn = None
    for sku, data in by_sku.items():
        for docs in data["parts"].values():
            for d in docs:
                t = d["update"] or d["finish"] or d["create"]
                if t and (latest_dyn is None or t > latest_dyn):
                    latest_dyn = t

    return {
        "computedAt": today.strftime("%Y-%m-%d %H:%M"),
        "segDef": {"productEnd": rs.product_end_node,
                   "productEndLabel": "素材交付",
                   "prodSeg": f"{rs.start_node}→素材交付",
                   "opsSeg": "素材交付→max(上架,广告设置)"},
        "totalSkus": len(by_sku),
        "alertCount": len(rows),
        "p0": sum(1 for r in rows if r["priority"] == "P0"),
        "p1": sum(1 for r in rows if r["priority"] == "P1"),
        "p2": sum(1 for r in rows if r["priority"] == "P2"),
        "voidCount": len(void_rows),
        "voidRows": void_rows,
        "voidDef": {"part": "末端终止", "note": VOID_NOTE},
        "aliasCount": len(alias_rows),
        "aliasRows": alias_rows,
        "aliasDef": {
            "note": ALIAS_NOTE,
            "source": "SKU登记表.xlsx · SKU换号 子表",
            "registered": len(aliases),
            "hitInCategory": len(alias_rows),
        },
        "holdCount": len(hold_rows),
        "holdRows": hold_rows,
        "holdDef": {
            "note": HOLD_NOTE,
            "source": "SKU登记表.xlsx · 人工登记 子表",
            "registered": len(holds),
            "hitInCategory": len(hold_rows),
        },
        "dataLagDays": max((today - latest_dyn).days, 0) if latest_dyn else None,
        "latestDynamic": _fmt(latest_dyn),
        "rows": rows,
        "watchList": watch_list,
    }


# ---------------------------------------------------------------------------
# 缓存 + 路由
# ---------------------------------------------------------------------------
import threading

_cache = {"fp": None, "data": None}          # 兼容旧引用：镜像"最近一次计算"
_cache_by_cat_alerts = {}                    # 品类 key -> {"fp", "data"}（真正的缓存）
_cache_lock = threading.Lock()


@router.get("")
@router.get("/")
def alerts(category: str = None):
    """加急预警总览：P0/P1/P2 明细 + 盯数清单 + 数据新鲜度。"""
    try:
        return _get_alerts_cached(category)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"预警计算失败: {e}")


def _get_alerts_cached(category=None):
    """带锁缓存：指纹一致直接返回；否则重算后原子提交（同 review 口径）。

    缓存**按品类分槽**（同 review）：不同品类规则集与批次都不同，共用一个槽位
    会让交替请求永远不命中，还会把另一品类的数据算进去。

    ⚠️ 计算必须在**锁外**（双检锁）：compute_alerts → get_review_data →
    record_snapshot → _build_kpi → _get_alerts_cached 会在同一个线程绕回来，
    若把计算放在锁内，这把非重入锁会被本线程自己撞死（整个后端挂死）。
    并发下最坏只是多算一次，可以接受。
    """
    cat = rules_for(category).key
    fp = f"{cat}|{_db_fingerprint(cat)}|{get_rules()[1]}"
    with _cache_lock:
        slot = _cache_by_cat_alerts.get(cat)
        if slot is not None and slot["fp"] == fp:
            return slot["data"]
    data = compute_alerts(cat)
    with _cache_lock:
        _cache_by_cat_alerts[cat] = {"fp": fp, "data": data}
        _cache["fp"], _cache["data"] = fp, data    # 兼容旧引用的镜像槽
    return data


@router.get("/export")
def export_alerts(priority: str = None, category: str = None):
    """导出加急预警清单 xlsx（可按优先级过滤），下一步建议合并为一列。

    四个 sheet：「加急预警」（P0/P1/P2 催办清单）+「已终止作废」（末端终止的作废单）
    +「已换号」（换号旧号）+「已豁免」（人工登记免催办）—— 后三个都是
    **不催办、仅备查**，仅在未按优先级过滤时附上。
    """
    import io
    import urllib.parse

    import pandas as pd
    from fastapi.responses import StreamingResponse

    try:
        data = _get_alerts_cached(category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导出失败: {e}")
    rows = data["rows"]
    if priority:
        rows = [r for r in rows if r["priority"] == priority]
    cols = ["优先级", "SKU", "型号", "卡点环节", "停滞(天)", "最近动态",
            "产品段(天)", "产品段目标", "产品段判定", "运营段(天)", "运营段目标",
            "运营段判定", "空运(天)", "空运判定", "空运卡点货件/渠道",
            "海运(天)", "海运判定", "海运卡点货件/渠道",
            "提案间隔(天)", "到货国数", "加急原因", "下一步"]

    def _worst_cell(detail):
        if not detail or not detail[0]["arr"]:
            return ""
        w = detail[0]
        d = "" if w["fetchDays"] is None else f"{w['fetchDays']:.0f}天"
        return f"{w['hub']}·{w['trk']}（{w['arr'][:10]}到货，取数起{d}）"

    out = []
    for r in rows:
        out.append({
            "优先级": r["priority"], "SKU": r["sku"], "型号": r["model"] or "",
            "卡点环节": r["pendingPartName"] or r["pendingPart"] or "—",
            "停滞(天)": r["stuckDays"] if r["stuckDays"] is not None else "",
            "最近动态": r["lastDynamic"] or "",
            "产品段(天)": "" if r["prodSeg"]["days"] is None else round(r["prodSeg"]["days"], 1),
            "产品段目标": r["prodSeg"]["target"],
            "产品段判定": r["prodSeg"]["judge"],
            "运营段(天)": "" if r["opsSeg"]["days"] is None else round(r["opsSeg"]["days"], 1),
            "运营段目标": r["opsSeg"]["target"],
            "运营段判定": r["opsSeg"]["judge"],
            "空运(天)": "" if r["air"]["days"] is None else round(r["air"]["days"], 1),
            "空运判定": r["air"]["judge"],
            "空运卡点货件/渠道": _worst_cell(r["air"].get("detail")),
            "海运(天)": "" if r["sea"]["days"] is None else round(r["sea"]["days"], 1),
            "海运判定": r["sea"]["judge"],
            "海运卡点货件/渠道": _worst_cell(r["sea"].get("detail")),
            "提案间隔(天)": "" if r["proposal"]["days"] is None else round(r["proposal"]["days"], 1),
            "到货国数": r["countries"],
            "加急原因": "；".join(r["reasons"]),
            "下一步": "；".join(r["nextSteps"]),
        })
    df = pd.DataFrame(out, columns=cols).fillna("")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="加急预警", index=False)
        # 已终止/作废 sheet：仅在未按优先级过滤时附上（别把「只要 P0」的导出塞成两页）
        if not priority:
            vcols = ["SKU", "型号", "终止环节", "终止时间", "停滞(天)",
                     "承接单", "承接单开单时间", "承接单已闭环", "结论"]
            vout = [{
                "SKU": r["sku"], "型号": r["model"],
                "终止环节": f"{r['part']}·{r['partName']}" if r["part"] else "—",
                "终止时间": r["terminatedAt"] or "",
                "停滞(天)": r["stuckDays"] if r["stuckDays"] is not None else "",
                "承接单": (r["successor"] or {}).get("sku", ""),
                "承接单开单时间": (r["successor"] or {}).get("firstAt", ""),
                "承接单已闭环": ("" if not r["successor"]
                                else ("是" if r["successor"]["valid"] else "否")),
                "结论": r["successorNote"],
            } for r in (data.get("voidRows") or [])]
            pd.DataFrame(vout, columns=vcols).fillna("").to_excel(
                writer, sheet_name="已终止作废", index=False)

            # 已换号 sheet（同样只在未过滤时附上）
            acols = ["旧SKU", "型号", "新SKU", "换号说明", "生效日期",
                     "旧号停滞(天)", "旧号最近动态", "新号有流程", "新号已闭环", "结论"]
            aout = [{
                "旧SKU": r["sku"], "型号": r["model"], "新SKU": r["newSku"],
                "换号说明": r["note"], "生效日期": r["effectiveAt"],
                "旧号停滞(天)": r["stuckDays"] if r["stuckDays"] is not None else "",
                "旧号最近动态": r["lastDynamic"] or "",
                "新号有流程": "是" if r["newHasFlow"] else "否",
                "新号已闭环": "" if r["newValid"] is None else ("是" if r["newValid"] else "否"),
                "结论": r["conclusion"],
            } for r in (data.get("aliasRows") or [])]
            pd.DataFrame(aout, columns=acols).fillna("").to_excel(
                writer, sheet_name="已换号", index=False)

            # 已豁免 sheet（人工登记免催办；同样只在未过滤时附上）
            hcols = ["SKU", "型号", "卡点环节", "停滞(天)", "最近动态",
                     "登记日期", "豁免说明", "结论"]
            hout = [{
                "SKU": r["sku"], "型号": r["model"],
                "卡点环节": (f"{r['pendingPart']}·{r['pendingPartName']}"
                             if r["pendingPart"] else "—"),
                "停滞(天)": r["stuckDays"] if r["stuckDays"] is not None else "",
                "最近动态": r["lastDynamic"] or "",
                "登记日期": r["registeredAt"] or "",
                "豁免说明": r["reason"] or "",
                "结论": r["conclusion"],
            } for r in (data.get("holdRows") or [])]
            pd.DataFrame(hout, columns=hcols).fillna("").to_excel(
                writer, sheet_name="已豁免", index=False)
    buf.seek(0)
    suffix = f"_{priority}" if priority else ""
    filename = urllib.parse.quote(f"加急预警清单{suffix}.xlsx")
    return StreamingResponse(buf, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
    }, media_type="application/vnd.openxmlformats-"
                   "officedocument.spreadsheetml.sheet")
