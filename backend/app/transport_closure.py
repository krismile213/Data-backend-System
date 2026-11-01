# -*- coding: utf-8 -*-
"""
transport_closure.py — 运输闭环判定单一真源（纯函数 · 零业务依赖）
================================================================================
业务口径（2026-10 用户逐字确认，**在售品免追踪**）：
  「所有段都已推进到位（无任何在研段，运输域也算）+ 运输无未到货」的 SKU 属**在售品**，
  物流到货与否**不是人可催办的动作**（等船/等航班），故免追踪（移出在研清单，单列备查）。
  ⚠️ 反向边界（必须守住）：只要还有任一段处在「未开始 / 进行中 / 预警 / 超期」——
  尤其是运输域里「·未发货」占位段（Part5 已立项渠道但压根没发货）——就**不是**在售品，
  必须保留追踪。判定由消费方（inflight_service）用 `is_transport_segment` + 段状态完成，
  本模块只负责「一票货件闭环没有」这一件事。

本模块把「一票货件到底闭环没有」抽成**纯函数**，供两个消费方共用（同源）：
  · `inflight_service.compute_inflight`（事中日报「在研清单 / 免追踪备查」）；
  · `routers/alerts.compute_alerts`（加急预警「已闭环·免追踪」）。

⚠️ 本模块**不得** import `review` / `exemption` / `inflight_service` —— 它只认「货件列表」
（`review.build_shipments()` 的产物，或任何含等价字段的 dict 列表），保持纯函数可单测。

货件字段（**多候选键名容错**，取不到视为缺失）：
  · 货件号    trk / 货件号 / tracking / trackingNo
  · 渠道仓(国家) hub / 渠道仓 / 渠道仓(国家) / 发往渠道仓库 / 到货渠道仓 / 国家 / country
  · 发货时间  ship / 发货时间 / 发货日期 / 发货 / shippedAt
  · 到货时间  arr / 到货时间 / 渠道仓到货时间 / 到货日期 / 到货 / arrivedAt
  · 未发货占位标记 noShip / 未发货（Part5 已立项渠道但无货件号的占位条目）

判定口径（**票级**判定、**仓级**展示）：
  · 一票「已发货」= 有货件号 **且** 有发货时间；「已到货」= 该票有到货时间；
  · 只要**任一已发货票未到货** → 未闭环（`pending`）；
  · `pendingHubs` 按**渠道仓/国家聚合去重**：只列「该仓下存在已发货但未到货的票」的国家名；
  · `notShippedHubs`（无货件号占位渠道）**只进 notShippedHubs，绝不参与 closure 判定**。

2026-10-15 起本模块还持有两条**运输追踪口径**的单一真源（事中日报「货件运输」sheet
与后台「货件运输」看板共用，避免两处各自实现 5 天窗口）：
  · `TRACK_ARRIVED_DAYS` —— 到货留观天数：到货 ≤5 天标「留观N/5天」，超出标
    「已归档(到货N天)」。**只决定状态文案，两处都不删行**（2026-11-01 起）；
  · `MUTE_NOSHIP_HUB_KEYWORD` —— 免标记的未发货渠道关键词：emag「有发货才标记」
    （Part5 审批中的 emag 记录无货件号，不生成「·未发货」占位段、不进在研卡点；
      该渠道真发货后按正常货件追踪）。仅 emag，其它渠道未发货仍是卡点。
"""

from datetime import date, datetime

# 运输域部门（段级时间轴里属于"运输"的部门）
TRANSPORT_DEPTS = ("运输", "4国")

# 货件字段候选键（按优先级）—— 容错不同来源的键名
_TRK_KEYS = ("trk", "货件号", "tracking", "trackingNo")
_HUB_KEYS = ("hub", "渠道仓", "渠道仓(国家)", "发往渠道仓库",
             "到货渠道仓", "国家", "country")
_SHIP_KEYS = ("ship", "发货时间", "发货日期", "发货", "shippedAt")
_ARR_KEYS = ("arr", "到货时间", "渠道仓到货时间", "到货日期", "到货",
             "arrivedAt")
_NOSHIP_KEYS = ("noShip", "未发货")


def _pick(d, keys):
    """从 dict 里按候选键取第一个「有值」的字段；取不到返回 None。

    有值 = 键存在、且值不是 None、空串（datetime 与 "" 比较恒 False，安全）。
    """
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d:
            v = d[k]
            if v is not None and v != "":
                return v
    return None


def _has(v):
    return v is not None and v != ""


def classify_transport(shipments):
    """货件列表 → 运输闭环判定。

    返回 dict（至少含）：
      closure       : "none"（无任何票已发货）/ "closed"（已发货票全部到货）
                      / "pending"（有已发货票未到货）；
      shippedCount  : 已发货票数（有货件号且有发货时间）；
      arrivedCount  : 已到货票数（已发货票里有到货时间）；
      shippedHubs   : 已发货票涉及的渠道仓/国家（去重，按首现顺序）；
      pendingHubs   : 存在已发货未到货票的渠道仓/国家（去重，按首现顺序）；
      notShippedHubs: Part5 已立项但无货件号（noShip 占位）的渠道仓（去重）；
      pendingCount  : 未到货票数（= shippedCount − arrivedCount）；
      tickets       : 已发货票明细 [{hub, trk, ship, arr, state}]（state ∈ 已到货/未到货；
                      取 `_pick` 到的**原值**，datetime 也照传 —— 供前端「运输在途·逐票」展示）；
      unshippedHubs : = notShippedHubs 的别名（供前端②④统一取用）。
    """
    shipped = arrived = 0
    shipped_hubs, pending_hubs, not_shipped_hubs = [], [], []
    tickets = []
    for it in shipments or []:
        trk = _pick(it, _TRK_KEYS)
        hub = _pick(it, _HUB_KEYS)
        ship = _pick(it, _SHIP_KEYS)
        arr = _pick(it, _ARR_KEYS)
        no_ship = bool(_pick(it, _NOSHIP_KEYS))
        is_shipped = _has(trk) and _has(ship)
        if not is_shipped:
            # 无货件号 / 无发货时间：Part5 已立项但未发货占位 → 只进 notShippedHubs，
            # **绝不参与 closure 判定**（未发货不是"闭环/未闭环"的票）
            if _has(hub) and hub not in not_shipped_hubs:
                not_shipped_hubs.append(hub)
            # 保留 no_ship 语义（占位条目一律按未发货处理，即便误带了货件号）
            if no_ship and _has(hub) and hub not in not_shipped_hubs:
                not_shipped_hubs.append(hub)
            continue
        shipped += 1
        if _has(hub) and hub not in shipped_hubs:
            shipped_hubs.append(hub)
        if _has(arr):
            arrived += 1
            tickets.append({"hub": hub, "trk": trk, "ship": ship, "arr": arr,
                            "state": "已到货"})
        else:
            if _has(hub) and hub not in pending_hubs:
                pending_hubs.append(hub)
            tickets.append({"hub": hub, "trk": trk, "ship": ship, "arr": arr,
                            "state": "未到货"})

    if shipped == 0:
        closure = "none"
    elif arrived == shipped and not pending_hubs:
        closure = "closed"
    else:
        closure = "pending"

    return {
        "closure": closure,
        "shippedCount": shipped,
        "arrivedCount": arrived,
        "shippedHubs": shipped_hubs,
        "pendingHubs": pending_hubs,
        "notShippedHubs": not_shipped_hubs,
        "pendingCount": max(shipped - arrived, 0),
        "tickets": tickets,
        "unshippedHubs": list(not_shipped_hubs),
    }


def is_transport_segment(dept, seg, l4_name):
    """该段是否属于「运输域」（判定运输闭环的意义上）。

    命中任一即 True：
      · dept 属于运输域（运输 / 4国）；
      · seg == l4_name（「采购→发货末批」= 后勤并行·多批取末批）；
      · seg 以「·未发货」结尾（Part5 已立项渠道但无货件号的占位段）。
    """
    dept = str(dept or "")
    seg = str(seg or "")
    l4 = str(l4_name or "")
    if dept in TRANSPORT_DEPTS:
        return True
    if l4 and seg == l4:
        return True
    if seg.endswith("·未发货"):
        return True
    return False


def transport_exempt_reason(closure, pending_hubs, not_shipped_hubs):
    """免追踪 / 未闭环的一句话原因（事中日报备查、预警备查、接口展示共用）。"""
    pend = "、".join(str(h) for h in (pending_hubs or []) if h)
    nots = "、".join(str(h) for h in (not_shipped_hubs or []) if h)
    tail = f"；渠道未发货：{nots}" if nots else ""
    if closure == "pending":
        return f"运输未闭环：已发货未到货渠道 {pend or '（渠道缺失）'}（保留追踪）"
    if closure == "closed":
        return f"运输已闭环：已发货货件全部到货{tail}（免追踪）"
    return f"无已发货货件，运输未形成卡点{tail}（免追踪）"


# ---------------------------------------------------------------------------
# 运输追踪口径（2026-10-15 用户确认）：到货留观 / 已运输天数 / emag 免标记
# ---------------------------------------------------------------------------
# 「·未发货」占位段后缀（与 is_transport_segment 同一约定：seg = f"{hub}·未发货"）
NOSHIP_SEG_SUFFIX = "·未发货"

# 到货留观天数：只决定「追踪状态」文案（≤5 天 留观N/5天，>5 天 已归档(到货N天)）。
# 2026-11-01 起**不再截断**：事中日报「货件运输」sheet 与后台「货件运输」看板
# 同为全量保留（SKU 在研期间每一票都在），本常量只影响文案与 archived 标记。
TRACK_ARRIVED_DAYS = 5

# 免标记的未发货渠道关键词：emag「有发货才标记」。
# 实况：emag罗马尼亚 的 Part5 记录长期「审批中/无货件号」——审批未完结前「未发货」
# 是既定状态而非卡点，天天挂「超时未发货」是误报；该渠道真发货（有货件号）后
# 按正常货件进运输段/货件运输明细。仅 emag —— 其它渠道（亚马逊等）未发货仍是卡点。
MUTE_NOSHIP_HUB_KEYWORD = "emag"


def _as_date(v):
    """datetime / date / 'YYYY-MM-DD[ HH:MM[:SS]]' → date；取不到返回 None。

    两个消费方的货件时间形态不同（事中侧是 datetime、看板侧是 _fmt 格式化串），
    这里统一容错，调用方一律拿到 date 再算天数。
    """
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def arrival_ago_days(arr, today=None):
    """到货至今几天；未到货 / 缺时间 → None（未来到货钳为 0）。

    入参 today 可注入（测试用）；缺省 = 本地今天。
    """
    a = _as_date(arr)
    if a is None:
        return None
    if today is None:
        today = date.today()
    return max((today - a).days, 0)


def elapsed_days(ship, arr, today=None):
    """已运输天数：已到货 = 发货→到货；在途 = 发货→至今；未发货/无发货日 → None。"""
    s = _as_date(ship)
    if s is None:
        return None
    end = _as_date(arr)
    if end is None:
        if today is None:
            today = date.today()
        end = today
    return max((end - s).days, 0)


def shipment_keep(ship, arr, today=None):
    """该票是否仍在追踪：在途 / 未发货 → True；到货 ≤ TRACK_ARRIVED_DAYS → True；
    到货超过 TRACK_ARRIVED_DAYS 天 → False（不追踪）。"""
    ago = arrival_ago_days(arr, today)
    return ago is None or ago <= TRACK_ARRIVED_DAYS


def is_muted_noship_hub(hub):
    """该渠道仓的「未发货」占位是否免标记（emag 专有，见 MUTE_NOSHIP_HUB_KEYWORD）。"""
    return MUTE_NOSHIP_HUB_KEYWORD in str(hub or "").lower()


def is_muted_noship_segment(seg):
    """「{hub}·未发货」占位段是否免标记；非占位段一律 False。"""
    seg = str(seg or "")
    if not seg.endswith(NOSHIP_SEG_SUFFIX):
        return False
    return is_muted_noship_hub(seg[:-len(NOSHIP_SEG_SUFFIX)])
