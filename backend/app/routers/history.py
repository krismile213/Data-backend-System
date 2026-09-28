# -*- coding: utf-8 -*-
"""
history.py — KPI 快照与自动周报（/api/history*）
================================================================
定位：解决「只有横截面、没有时间纵深」的问题。
每次复盘指纹（数据导入 / 规则调整）变化后的第一次访问，自动记录一张
关键 KPI 快照；快照积累后即可生成周报/月报（markdown 下载）。

存储：backend/state/kpi_snapshots.json（按指纹去重，快照不可变）。
自动触发：review.get_review_data 重算完成后调用 record_snapshot（懒导入防循环）；
访问本接口时补拍（进程刚启动时也会补上）。

接口：
  GET  /api/history            快照列表（访问即补拍）
  POST /api/history/snapshot   立即补拍一张快照（force=True 可同指纹强制）
  GET  /api/history/report     生成周报 markdown 下载（?days=7/30）
"""

import io
import json
import os
import threading
import urllib.parse
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException

from ..config import STATE_DIR

router = APIRouter(prefix="/api/history", tags=["history"])

_STATE_FILE = STATE_DIR / "kpi_snapshots.json"
# RLock（而非 Lock）：record_snapshot 与 get_review_data 互为调用方，
# 同线程会重入，非重入锁会让整个后端永久挂死。
_lock = threading.RLock()
# 同线程重入标记：命中时直接返回，避免嵌套再次 _build_kpi（无限套娃）。
_reentry = threading.local()


def _load():
    if not _STATE_FILE.exists():
        return {"snapshots": []}
    try:
        with open(_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "snapshots" not in data:
            return {"snapshots": []}
        return data
    except Exception:
        return {"snapshots": []}


def _save(data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _STATE_FILE)


def _build_kpi(review_data, category=None) -> dict:
    """从各看板汇聚一张快照的关键 KPI（全部走各自的缓存，开销可控）。

    category 必须与 review_data 同品类 —— 否则快照里的 KPI 会串味
    （典型现象：手机壳指纹配手机膜的运输/P0/场景计数）。
    """
    from .alerts import _get_alerts_cached
    from .scenarios import compute_scenarios
    from .transport import compute_transport

    rows = review_data["rows"]
    seg_stats = review_data.get("segStats") or []
    sample = sum(s["sampleCount"] for s in seg_stats)
    over = sum(s["overCount"] for s in seg_stats)

    tk = compute_transport(category)["kpi"]
    al = _get_alerts_cached(category)
    sc = compute_scenarios(category)

    top_over = sorted(seg_stats, key=lambda s: -s["overCount"])[:3]
    return {
        "skuCount": len(rows),
        "openSkus": sum(1 for r in rows if not r["valid"]),
        "onTimeRate": round(100 * (sample - over) / sample, 1) if sample else None,
        "segSamples": sample,
        "transportTotal": tk.get("total"),
        "transportOverRate": tk.get("overRate"),
        "p0": al.get("p0"), "p1": al.get("p1"), "p2": al.get("p2"),
        "scenA": next((s["count"] for s in sc["summary"] if s["code"] == "A"), 0),
        "scenB": next((s["count"] for s in sc["summary"] if s["code"] == "B"), 0),
        "scenC": next((s["count"] for s in sc["summary"] if s["code"] == "C"), 0),
        "topOverSegs": [{"seg": s["seg"], "dept": s["dept"],
                         "overCount": s["overCount"],
                         "onTimeRate": s["onTimeRate"]} for s in top_over],
    }


# 快照品类作用域：2026-09-22 起**已放开到全部品类**。
# 原先只给默认品类打快照，是因为 _build_kpi 里的运输/预警/场景看板尚未按品类
# 分发（永远返回手机膜的数字 → 给壳打快照 = 静默串味）。现在这三个看板都接受
# category 并做批次隔离，_build_kpi 也透传品类，因此可以安全放开。


def record_snapshot(fp: str, review_data=None, force: bool = False,
                    category=None) -> dict:
    """按指纹记一张快照；指纹相同默认跳过（force 可强制）。

    同线程重入保护（必须保留）：调用链
        get_review_data → record_snapshot → _build_kpi
          → compute_transport / _get_alerts_cached / compute_scenarios
          → get_review_data
    在嵌套的 get_review_data 未命中缓存时，会在**同一线程**再次进入本函数。
    此时直接返回：外层那次已经覆盖当前状态，跳过的快照会在下一次
    `_current_fingerprint()` 变化时由 /api/history 补拍（自愈），
    但**绝不能在锁内套娃**，否则会把整个后端永久卡住。
    """
    from .review import get_review_data

    if getattr(_reentry, "active", False) and not force:
        return {"fingerprint": fp, "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "skipped": "reentrant"}

    _reentry.active = True
    try:
        with _lock:
            store = _load()
            snaps = store["snapshots"]
            # 按**指纹**去重（而非只比最后一张）：品类交替访问时，
            # "最后一张"总是另一品类，会把同一个状态反复追加成多张快照。
            if not force:
                for s in reversed(snaps):
                    if s.get("fingerprint") == fp:
                        return s
            if review_data is None:
                review_data = get_review_data(category)
            snap = {
                "fingerprint": fp,
                "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "kpi": _build_kpi(review_data, category),
            }
            if category is not None:
                snap["category"] = category
            snaps.append(snap)
            _save(store)
        return snap
    finally:
        _reentry.active = False


def _current_fingerprint(category=None) -> str:
    """指定品类的复盘指纹。

    ⚠️ 不要读 review._cache 的"最近一次计算"镜像：那是**全进程共享的最后一格**，
    算过手机壳之后再取快照，会把手机膜的 KPI 挂到手机壳的指纹上（跨品类串味）。
    """
    from .review import fingerprint_for
    return fingerprint_for(category)


def maybe_snapshot(category=None):
    """确保**指定品类**当前指纹已有快照（页面访问/定时任务均可调用）。

    指纹与数据必须取自同一品类，否则快照会串味。
    """
    from ..category_rules import rules_for
    from .review import get_review_data
    cat = rules_for(category).key
    data = get_review_data(cat)              # 先确保该品类已算好（并可能触发补拍）
    fp = _current_fingerprint(cat)            # 与刚拿到的数据同品类
    return record_snapshot(fp, data, category=cat)


@router.get("")
def history(category: str = None):
    """快照列表（访问即补拍，保证最新状态总有一张）。

    category: 品类（手机膜/手机壳），缺省 = 默认品类（手机膜）。
    """
    try:
        snap = maybe_snapshot(category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"快照生成失败: {e}")
    with _lock:
        store = _load()
    return {
        "computedAt": snap["at"],
        "snapshots": store["snapshots"],
        "count": len(store["snapshots"]),
    }


@router.post("/snapshot")
def snapshot(force: bool = False, category: str = None):
    """立即补拍快照（force=True 时同指纹也强制记录，供定时任务/手动打点）。"""
    from ..category_rules import rules_for
    try:
        snap = maybe_snapshot(category)
        if force:
            snap = record_snapshot(snap["fingerprint"] + f"|manual-{datetime.now():%H%M%S}",
                                   force=True, category=rules_for(category).key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"快照生成失败: {e}")
    return {"ok": True, "snapshot": snap}


@router.get("/report")
def report(days: int = 7, category: str = None):
    """生成周报 markdown 下载（对比区间首末快照 + 当前看板要点）。"""
    try:
        snap = maybe_snapshot(category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"快照生成失败: {e}")
    with _lock:
        store = _load()
    snaps = store["snapshots"]
    now = datetime.now()
    since = now - timedelta(days=days)
    window = [s for s in snaps
              if _parse_snap_at(s["at"]) >= since] or snaps[-2:]
    if not window:
        raise HTTPException(status_code=404, detail="暂无快照，无法生成报告")

    first, last = window[0], window[-1]
    kf, kl = first["kpi"], last["kpi"]

    def _d(key, fmt="{}"):
        a, b = kf.get(key), kl.get(key)
        if a is None and b is None:
            return "—", "—"
        delta = (None if (a is None or b is None) else round(b - a, 1))
        return fmt.format(a if a is not None else "—"), \
               (f"{delta:+.1f}" if delta is not None else "—")

    def _row(name, key, unit="", good_down=False):
        a, b = kf.get(key), kl.get(key)
        a_s = "—" if a is None else f"{a}{unit}"
        b_s = "—" if b is None else f"{b}{unit}"
        if a is None or b is None:
            d_s = "—"
        else:
            d = round(b - a, 1)
            arrow = ""
            if d != 0:
                better = (d < 0) if good_down else (d > 0)
                arrow = " ✅" if better else " ⚠️"
            d_s = f"{d:+.1f}{unit}{arrow}" if d != 0 else "持平"
        return f"| {name} | {a_s} | {b_s} | {d_s} |"

    L = []
    L.append(f"# 新品售前流程 · 数据周报")
    L.append("")
    L.append(f"- **统计区间**：{first['at']} → {last['at']}"
             f"（{len(window)} 张快照）")
    L.append(f"- **生成时间**：{now.strftime('%Y-%m-%d %H:%M')}"
             f"（口径：实际动作映射；快照按数据指纹自动记录）")
    L.append("")
    L.append("## 一、KPI 变化（期初 → 期末）")
    L.append("")
    L.append("| 指标 | 期初 | 期末 | 变化 |")
    L.append("|---|---|---|---|")
    L.append(_row("复盘 SKU 数", "skuCount"))
    L.append(_row("未闭环 SKU", "openSkus", good_down=True))
    L.append(_row("总体段级达标率", "onTimeRate", "%", good_down=False))
    L.append(_row("货件超时率", "transportOverRate", "%", good_down=True))
    L.append(_row("P0 预警", "p0", "", good_down=True))
    L.append(_row("P1 预警", "p1", "", good_down=True))
    L.append(_row("P2 预警", "p2", "", good_down=True))
    L.append(_row("场景 A（素材未完成）", "scenA", "", good_down=True))
    L.append(_row("场景 C（发货未完成）", "scenC", "", good_down=True))
    L.append("")

    L.append("## 二、当前看板要点")
    L.append("")
    L.append(f"- **复盘**：{kl.get('skuCount')} 个 SKU，未闭环 "
             f"{kl.get('openSkus')} 个；总体达标率 "
             f"{kl.get('onTimeRate') if kl.get('onTimeRate') is not None else '—'}%")
    tops = kl.get("topOverSegs") or []
    if tops:
        L.append("- **超标最多的段**：" + "；".join(
            f"{t['dept']} {t['seg']}（超标 {t['overCount']} 次，"
            f"达标率 {t['onTimeRate']}%）" for t in tops))
    L.append(f"- **货件运输**：共 {kl.get('transportTotal')} 票，"
             f"超时率 {kl.get('transportOverRate')}%")
    L.append(f"- **加急预警**：P0 {kl.get('p0')} / P1 {kl.get('p1')} /"
             f" P2 {kl.get('p2')}")
    L.append(f"- **场景诊断**：A {kl.get('scenA')} · B {kl.get('scenB')} ·"
             f" C {kl.get('scenC')}")
    L.append("")

    # 简单自动解读
    L.append("## 三、区间解读")
    L.append("")
    notes = []
    if kf.get("onTimeRate") is not None and kl.get("onTimeRate") is not None:
        d = round(kl["onTimeRate"] - kf["onTimeRate"], 1)
        if d <= -2:
            notes.append(f"总体达标率下降 {abs(d)} 个百分点，需关注新近结算段的执行质量"
                         "（结合复盘页「月度达标趋势」定位回落月份）。")
        elif d >= 2:
            notes.append(f"总体达标率提升 {d} 个百分点，流程执行在改善。")
        else:
            notes.append("总体达标率基本持平（波动 < 2 个百分点）。")
    if kf.get("openSkus") is not None and kl.get("openSkus") is not None \
            and kl["openSkus"] > kf["openSkus"]:
        notes.append(f"未闭环 SKU 由 {kf['openSkus']} 增至 {kl['openSkus']}，"
                     "建议按「联动提醒」页清单位推进闭环。")
    if kf.get("transportOverRate") is not None \
            and kl.get("transportOverRate") is not None \
            and kl["transportOverRate"] > kf["transportOverRate"]:
        notes.append("货件超时率上升，可在「货件运输」页按渠道定位超时 TOP。")
    if not notes:
        notes.append("各项指标平稳，无显著变化。")
    for n in notes:
        L.append(f"- {n}")
    L.append("")
    L.append("---")
    L.append("*本报告由数据后台系统自动生成；快照口径：复盘/运输/预警/场景四看板"
             "在数据指纹变化后的首次访问时自动记录。*")

    md = "\n".join(L)
    buf = io.BytesIO(md.encode("utf-8"))
    filename = urllib.parse.quote(f"数据周报_{now:%Y%m%d}.md")
    from fastapi.responses import StreamingResponse
    return StreamingResponse(buf, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
    }, media_type="text/markdown; charset=utf-8")


def _parse_snap_at(s: str):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M")
    except ValueError:
        return datetime.min
