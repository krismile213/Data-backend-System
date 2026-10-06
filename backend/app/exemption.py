# -*- coding: utf-8 -*-
"""
exemption.py — 「不再追踪」豁免判定单一真源（2026-10-06 从 alerts.py 提取）
================================================================================
背景（口径分裂事故）：同一套「这个 SKU 还追不追」的判定此前散在多处且**不一致**——

  · alerts.py（加急预警，2026-09-28 口径）：
        人工登记 > 换号 > 末端终止（作废单）> 催办，三者都单列备查；
  · inflight_service.py（事中日报定时任务）：
        只有「全部单据都终止」才排除 → 人工登记（G495/G524~G528）、已换号旧号、
        末端终止（前面 Part 已结束、末尾 Part 全终止）的 SKU **全部混进
        「在研 SKU 状态总表」**，终止的单据也在被追踪，未闭环清单大量虚高；
  · doc_delta_service.py（未闭环 SKU 单据增量追踪）：
        未闭环 = 复盘 valid=False → 同样混入豁免 SKU。

本模块把判据收敛成**一处**，三个消费方（预警 / 事中 / 单据增量）必须全部走这里；
以后改判据只改本文件（离线脚本 `流程复盘/route_check_sys.py` 的 void_of 同源实现
由 tests/test_alert_void.py 的跨实现对齐用例钉住）。

判定次序（与 alerts 2026-09-28 口径完全一致）：

    ① 人工登记（hold）  = 业务登记「不再走完流程」，没有承接号 —— 人工决策，最先判；
    ② 换号（alias）     = 旧号停用、新号承接 —— 业务登记；
    ③ 末端终止（void）  = **数据推断**：全 SKU 无在跑单据，且最后一个有单据的
                          Part 里单据**全部**为「终止」→ 流程作废；
    ④ 正常追踪（active）= 以上都不命中，照常进在研/催办/增量追踪。

单据形状兼容：alerts 的单据键是 status/create/update/finish，review 系的单据键是
status/create_time/update_time/finish_time —— `_status()` / `_doc_time()` 两者都认，
调用方直接传自己的单据 dict，不需要先转换。

真源：
    · 人工登记 = `资产文件\\SKU登记表.xlsx·人工登记 子表` → MySQL sku_hold（app/sku_hold.py）
    · 换号     = `资产文件\\SKU登记表.xlsx·SKU换号  子表` → MySQL sku_alias（app/sku_alias.py）
    · 末端终止 = 流程单据本身（数据推断，无人工登记）
"""

from datetime import datetime

from .review_rules import PART_LIST

#: 审批「已完结」状态集合（终止**不在**其中：终止 = 撤回/填错重开，不进后续环节）
OK_STATUS = {"已结束", "结束", "已通过", "已完成"}
#: 终止状态字面值（钉钉导出口径）
VOID_TERMINATED = "终止"

VOID_NOTE = "流程已终止，不进入后续环节，不列入催办/追踪"


# ---------------------------------------------------------------------------
# 单据字段兼容层（两种键名都认）
# ---------------------------------------------------------------------------
def _status(d):
    """单据审批状态（strip 后）。"""
    return str(d.get("status") or "").strip()


def _doc_time(d):
    """单据「最新动态」时间：finish > update > create（兼容 *_time 键名）。"""
    for k in ("finish", "finish_time", "update", "update_time",
              "create", "create_time"):
        v = d.get(k)
        if v:
            return v
    return None


# ---------------------------------------------------------------------------
# 末端终止（作废单）判定 —— 与 alerts._void_info 同源实现
# ---------------------------------------------------------------------------
def doc_running(d):
    """该单据是否「还在跑」：非已结束、也非终止。"""
    return _status(d) not in OK_STATUS and _status(d) != VOID_TERMINATED


def last_part_with_docs(parts, part_order=None):
    """最后一个有单据的 Part（Part1→Part9 顺序里最靠后的那个）。"""
    order = part_order if part_order is not None else PART_LIST
    last = None
    for pk in order:
        if parts.get(pk):
            last = pk
    return last


def void_info(parts, part_order=None):
    """末端终止 → (True, part, 终止时刻)；否则 (False, None, None)。

    判据（②与离线 route_check_sys.void_of 同源，改一处必须同步改另一处）：
      ① 该 SKU 没有任何「审批中/在跑」单据 —— 流程是真的停住了，不是在跑
      ② 最后一个有单据的 Part 里，单据状态**全部**为「终止」
    """
    parts = parts or {}
    if any(doc_running(d) for docs in parts.values() for d in docs):
        return False, None, None                    # 还有在跑的单据 → 不当作废
    pk = last_part_with_docs(parts, part_order)
    if pk is None:
        return False, None, None
    docs = parts[pk]
    if any(_status(d) != VOID_TERMINATED for d in docs):
        return False, None, None                    # 末尾 Part 不是全终止
    times = [t for t in (_doc_time(d) for d in docs) if t]
    at = max(times) if times else None
    return True, pk, at


# ---------------------------------------------------------------------------
# 登记表加载（两表都带自身指纹缓存，重复调用无额外开销）
# ---------------------------------------------------------------------------
def load_registries():
    """→ (holds, aliases)。DB/登记表缺失时各自返回 {}（绝不抛）。"""
    from .sku_alias import load_aliases
    from .sku_hold import load_holds
    return load_holds(), load_aliases()


# ---------------------------------------------------------------------------
# 四层分类（单一入口）
# ---------------------------------------------------------------------------
def classify(sku, parts, part_order=None, holds=None, aliases=None):
    """对单个 SKU 做豁免分类 → (kind, info)。

    kind ∈ {"hold", "alias", "void", "active"}；
    info：hold/alias = 登记表行副本；void = {"part", "terminatedAt"}；active = {}。
    holds/aliases 缺省时自动加载（带缓存）；批量调用建议先 load_registries() 传入，
    避免逐 SKU 触碰缓存字典。
    """
    key = str(sku or "").strip().upper()
    if holds is None or aliases is None:
        _h, _a = load_registries()
        holds = _h if holds is None else holds
        aliases = _a if aliases is None else aliases
    hd = holds.get(key)
    if hd:
        return "hold", dict(hd)                     # 人工决策，最先判
    al = aliases.get(key)
    if al:
        return "alias", dict(al)                    # 业务登记次之
    is_void, part, at = void_info(parts, part_order)
    if is_void:
        return "void", {"part": part, "terminatedAt": at, "note": VOID_NOTE}
    return "active", {}


def classify_many(parts_by_sku, part_order=None, holds=None, aliases=None):
    """批量分类 → {sku: (kind, info)}（只收非 active 的，省内存）。"""
    holds, aliases = (load_registries()
                      if holds is None or aliases is None
                      else (holds, aliases))
    out = {}
    for sku, parts in (parts_by_sku or {}).items():
        kind, info = classify(sku, parts, part_order, holds, aliases)
        if kind != "active":
            out[sku] = (kind, info)
    return out


def exempt_reason(kind, info):
    """豁免原因的一句人话（日报备查 / API 展示共用）。"""
    if kind == "hold":
        r = (info or {}).get("reason") or "业务上已不需走完流程"
        return f"人工登记：{r}（不列入追踪）"
    if kind == "alias":
        new = (info or {}).get("new") or "?"
        return f"已换号 → {new}（流程由新号承接，旧号不列入追踪）"
    if kind == "void":
        part = (info or {}).get("part") or "?"
        at = (info or {}).get("terminatedAt")
        at_s = (at.strftime("%Y-%m-%d") if hasattr(at, "strftime")
                else str(at or "时间缺失"))
        return f"末端终止：{part} 单据全部终止（{at_s}），流程作废不列入追踪"
    return ""
