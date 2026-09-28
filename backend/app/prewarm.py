# -*- coding: utf-8 -*-
"""prewarm.py — 空闲预热：趁没人用的时候把进程内缓存先算好

============================================================
为什么要做：接口本身不慢，慢的是「第一个访问者」。

本机实测（批次 手机膜9.7，干净进程）：

    /api/overview/手机膜9.7    冷 2.39s  →  热 0.045s
    /api/alerts                冷 2.55s  →  热 0.036s

「冷」= 后端重启、或 Excel / 规则更新导致指纹变化时的**首次计算**。
缓存机制本身是好的（按指纹命中），缺的只是「谁来当第一个访问者」。

============================================================
⚠️ 一个被实测否掉的朴素做法（别再走一遍）

第一版是「启动后立刻在后台线程里全量预热」。A/B 实测**反而更慢**：

    首个访问耗时            关闭预热   立即预热
    /api/overview/手机膜9.7   2.39s  →  5.45s
    /api/alerts               2.55s  →  5.18s

原因是预热（单进程、pandas CPU 密集 + GIL）与用户的首次请求**抢 CPU**，
而 `get_batch_data` 的按批次锁让用户的请求只能排队等预热算完同一批次。
→ 12.77s 的全量预热窗口，正好盖在用户打开页面的那几秒上。

所以现在的设计是**礼让式**：预热只在「连续一段时间没有访问」时才动手，
一旦有请求进来就让路。它绝不与真实使用并行，因此**不可能比不做更差**。

============================================================
时序：

    服务就绪 → 等 DELAY 秒（覆盖「浏览器已开、前端还在加载模块」的空窗，
                            这段后端看不到任何请求，没法靠活动检测识别）
             → 每个预热项之前检查空闲：连续 IDLE 秒无请求才放行
             → 若持续有访问超过 MAX_WAIT，放弃本轮（实际使用会自行把缓存捂热）

============================================================
覆盖的三处进程内缓存（全后端仅这三处）：

    analysis._cache   键=批次目录指纹     → overview / timeliness / anomalies /
                                           records / compare 共用
    review._cache     键=DB指纹+规则版本  → review / transport / routes /
                                           watch / scenarios 共用
    alerts._cache     键=同上             → /api/alerts

顺序：批次 → 复盘 → 预警。
（alerts 内部会再取一次复盘数据，放在 review 之后做，这次命中热缓存、不重复算。）

============================================================
其他约定：

- daemon 线程，绝不阻塞服务启动。
- 任何一步失败只打印、不抛出，不影响服务可用性。
- 幂等：重复调用安全。已有预热在跑时，新的请求登记一次「补跑」
  （用于「导入 / 改规则后缓存失效」这类场景）。

环境变量：`PREWARM=0` 关闭；`PREWARM_DELAY` 调整启动等待秒数（默认 10）。
测试环境自动跳过（见 `_enabled`）。
"""

import os
import sys
import threading
import time

# 启动后先等这么久再考虑预热：浏览器打开到前端发出第一个 API 请求之间，
# 后端收到的是 0 个请求（那段时间在跟 5173 的 vite 要模块），
# 光靠「空闲检测」区分不出「用户正在打开页面」和「真没人用」。
DELAY_SECONDS = float(os.getenv("PREWARM_DELAY", "10"))
# 判定空闲的阈值：连续这么久没有请求才算没人用
IDLE_SECONDS = 5.0
# 一直等不到空闲就放弃本轮，别无限占着线程
MAX_WAIT_SECONDS = 300.0

# 预热步骤，按依赖顺序排列（alerts 复用 review 的结果，必须放后面）
_STEPS = ("batches", "review", "alerts")

_state = {"running": False, "pending": False}
_state_lock = threading.Lock()
_gated = False                      # 是否启用空闲门（仅异步预热启用）
_last_activity = time.monotonic()   # 最近一次用户请求时刻
_activity_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 活动追踪 / 空闲门
# ---------------------------------------------------------------------------

def note_activity() -> None:
    """由 HTTP 中间件在每次请求时调用。"""
    global _last_activity
    with _activity_lock:
        _last_activity = time.monotonic()


def _quiet_seconds() -> float:
    with _activity_lock:
        return time.monotonic() - _last_activity


def _gate(label: str) -> bool:
    """空闲门：等到连续 IDLE_SECONDS 无访问才放行，返回 False 表示放弃本轮。

    只在异步预热时生效——同步调用（诊断/测试）不受影响。
    """
    if not _gated:
        return True
    waited = 0.0
    announced = False
    while _quiet_seconds() < IDLE_SECONDS:
        if not announced:
            _say(f"  [预热] 有人在用，先让路（{label}）")
            announced = True
        if waited >= MAX_WAIT_SECONDS:
            _say(f"  [预热] 持续有访问超过 {MAX_WAIT_SECONDS:.0f}s，放弃本轮"
                 "（实际使用会自行把要用的缓存捂热）")
            return False
        time.sleep(0.5)
        waited += 0.5
    if announced:
        _say(f"  [预热] 已空闲，继续（{label}）")
    return True


def _say(msg: str) -> None:
    """打印预热进度。控制台编码异常（例如重定向时的 GBK）不应影响预热本身。"""
    try:
        print(msg, flush=True)
    except Exception:  # noqa: BLE001 — 输出失败本身不值得中断预热
        pass


def _err(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"


def _enabled() -> bool:
    """预热开关。

    - `PREWARM=0`（或 false/no/off）关闭；
    - 测试环境自动关闭：`with TestClient(app) as c:` 同样会走 lifespan，
      若不跳过，预热线程会在测试期间抢 CPU 与数据库连接，既拖慢又可能造成偶发失败。
    """
    if os.getenv("PREWARM", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    return "pytest" not in sys.modules


# ---------------------------------------------------------------------------
# 各步骤
# ---------------------------------------------------------------------------

def _warm_batches() -> None:
    """预热 analysis._cache：逐批次（顺序执行，逐批次过空闲门）。

    2026-09-22 起扫**全部品类**的批次（手机壳数据根也会预热）。
    """
    from .analysis import get_batch_data
    from .config import list_batches

    batches = list_batches(all_categories=True)
    if not batches:
        _say("  [预热] 未发现批次（检查数据根目录 DATA_ROOTS）")
        return
    for b in batches:
        name, cat = b["name"], b.get("category", "")
        if not _gate(f"批次 {name}"):
            return
        t0 = time.perf_counter()
        try:
            bd = get_batch_data(name)
            _say(f"  [预热] 批次 {cat}/{name:<14} {time.perf_counter() - t0:6.2f}s  "
                 f"{len(bd.raw)} 个 Part / {len(bd.anomalies)} 条异常")
        except Exception as e:  # noqa: BLE001 — 单批次失败不拖累其余批次
            _say(f"  [预热] 批次 {cat}/{name} 失败：{_err(e)}")


def _warm_review() -> None:
    """预热 review._cache（transport / routes / watch / scenarios 一并受益）。

    按**有数据的品类**逐个预热（默认品类放最前：绝大多数访问都落在这里）。
    """
    from .category_rules import DEFAULT_CATEGORY
    from .config import list_batches
    from .routers.review import get_review_data

    cats = []
    for b in list_batches(all_categories=True):
        c = b.get("category") or DEFAULT_CATEGORY
        if c not in cats:
            cats.append(c)
    cats.sort(key=lambda c: c != DEFAULT_CATEGORY)      # 默认品类优先
    for cat in cats or [DEFAULT_CATEGORY]:
        if not _gate(f"复盘 {cat}"):
            return
        t0 = time.perf_counter()
        data = get_review_data(cat)
        _say(f"  [预热] 复盘全量 {cat:<4}   {time.perf_counter() - t0:6.2f}s  "
             f"{data.get('skuCount', '?')} 个 SKU")


def _warm_alerts() -> None:
    """预热 alerts._cache。"""
    from .routers.alerts import _get_alerts_cached

    t0 = time.perf_counter()
    data = _get_alerts_cached()
    rows = data.get("rows") if isinstance(data, dict) else None
    extra = f"{len(rows)} 条明细" if isinstance(rows, list) else ""
    _say(f"  [预热] 加急预警          {time.perf_counter() - t0:6.2f}s  {extra}")


_FUNCS = {"batches": _warm_batches, "review": _warm_review, "alerts": _warm_alerts}


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def warm_all(reason: str = "startup") -> float:
    """同步执行一轮预热，返回耗时（秒）。

    每一步失败都不影响其余步骤。异步调用时（`_gated`），每一步之前会过空闲门，
    等到没人用才动手；同步调用（诊断 / 测试）直接跑，不受空闲门限制。
    """
    t0 = time.perf_counter()
    _say(f"[预热] 开始（{reason}）")
    for name in _STEPS:
        if not _gate(name):
            return time.perf_counter() - t0
        try:
            _FUNCS[name]()
        except Exception as e:  # noqa: BLE001 — 预热属于优化，绝不能让服务启动失败
            _say(f"  [预热] {name} 步骤失败：{_err(e)}")
    dt = time.perf_counter() - t0
    _say(f"[预热] 完成（{reason}）耗时 {dt:.2f}s —— 之后的页面打开不再撞冷启动")
    return dt


def _loop(reason: str) -> None:
    """预热线程主体。"""
    try:
        # 启动等待：覆盖「浏览器已打开、前端仍在加载模块」的空窗
        time.sleep(DELAY_SECONDS)
        while True:
            warm_all(reason)
            with _state_lock:
                if not _state["pending"]:
                    _state["running"] = False
                    return
                _state["pending"] = False
                reason = f"{reason} · 队列补跑"
    except BaseException as e:  # noqa: BLE001 — 兜底，避免线程静默死掉后 running 卡住
        _say(f"[预热] 线程异常退出：{_err(e)}")
        with _state_lock:
            _state["running"] = False


def warm_async(reason: str = "startup") -> bool:
    """在后台线程预热，立即返回，不阻塞调用方。

    返回是否真的启动了新线程。已有预热在进行时只登记一次「补跑」，不并发堆线程。
    """
    global _gated
    if not _enabled():
        _say(f"[预热] 已跳过（{reason}；PREWARM=0 或测试环境）")
        return False
    with _state_lock:
        if _state["running"]:
            _state["pending"] = True
            _say(f"[预热] 已有预热在进行，登记补跑（{reason}）")
            return False
        _state["running"] = True
    _gated = True
    threading.Thread(target=_loop, args=(reason,), name="prewarm", daemon=True).start()
    _say(f"[预热] 已排队（{reason}），将在空闲时执行（先等 {DELAY_SECONDS:.0f}s）")
    return True


def status() -> dict:
    """当前预热状态（观测用）。"""
    with _state_lock:
        return {"running": _state["running"], "pending": _state["pending"],
                "enabled": _enabled(), "quietSeconds": round(_quiet_seconds(), 1)}
