# -*- coding: utf-8 -*-
"""
transport_closure.py — 运输闭环判定单一真源（纯函数 · 零业务依赖）
================================================================================
背景（2026-10 需求）：不少 SKU 三部门节点都已推进到位、只差「已发货货件到货」这一件
物流事实；而物流到货与否**不是人可催办的动作**（等船/等航班），继续把它当卡点会让
「在研清单 / 加急预警」长期挂着一批其实已经闭环、只等物流的 SKU，把真正的人为卡点淹没。

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
"""

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
      pendingCount  : 未到货票数（= shippedCount − arrivedCount）。
    """
    shipped = arrived = 0
    shipped_hubs, pending_hubs, not_shipped_hubs = [], [], []
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
        elif _has(hub) and hub not in pending_hubs:
            pending_hubs.append(hub)

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
