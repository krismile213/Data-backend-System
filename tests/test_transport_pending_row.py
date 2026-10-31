# -*- coding: utf-8 -*-
"""
test_transport_pending_row.py — 甘特图「运输·在途」行
=====================================================
背景（2026-11-01 用户提问）：「拿 G594 举例，为什么在途甘特显示已经都有到货时间了，
但是具体货件其实还是有没到的。」

根因：`transport_rows` 的空运/海运/4国行**只汇总已到货的票**
（过滤条件 `it["arr"] and it["ship"]`），在途票根本不进这些行。段一闭合，
甘特图看起来就像"运输全到齐了"；括号里的票数也是**已到货票数**，
越堵数字越小、越显得正常（G594 实测：15 票到 10 票、5 票在路上）。

修法：额外出一行「运输·在途(K票)」，起点=最早在途发货、终点留空
（甘特图延伸至今日、终点列写「进行中」），明细列给
「已发N·到M·未到K：站点…」。

本文件钉住：
  ① 有在途票才出行，全到货不出；位置在「运输·空运/海运」之后、「4国」之前；
  ② 明细文案与站点去前缀；
  ③ `displayOnly=True` + `days=None` —— 只画不判，不许污染当前停留/超期/卡点统计；
  ④ 甘特图「超期」列对在途行写「在途K票」，不是「✓」也不是「—」。
"""

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import reporting as rp                                   # noqa: E402
from app.routers import review as rv                              # noqa: E402


def _dt(s):
    return datetime.strptime(s, "%Y-%m-%d")


def _t(trk, hub, ship, arr=None, cls="海运", mode="海运", target=35):
    return {"trk": trk, "hub": hub, "ship": _dt(ship) if ship else None,
            "arr": _dt(arr) if arr else None,
            "days": (float((_dt(arr) - _dt(ship)).days) if (ship and arr) else None),
            "mode": mode, "target": target, "cls": cls, "noShip": False}


# 按 G594 的真实形态构造（已到货 + 在途混排，5 票在途）
def _g594_items():
    arrived = [
        _t("77739558", "美客多墨西哥", "2026-09-28", "2026-10-22"),
        _t("FBA15MHMFBC9", "亚马逊阿联酋", "2026-09-28", "2026-10-09",
           cls="空运", mode="空运", target=15),
        _t("FBA15MHHH99G", "亚马逊沙特", "2026-09-28", "2026-10-19",
           cls="空运", mode="空运", target=15),
        _t("FBA19QQ8H4L2", "亚马逊美国", "2026-09-30", "2026-10-16"),
        _t("FBA15MHJMWC9", "亚马逊英国", "2026-09-30", "2026-10-14",
           cls="空运", mode="空运", target=15),
    ]
    pending = [
        _t("750156441207", "东南亚菲律宾", "2026-10-09"),
        _t("CG26090632", "东南亚印尼", "2026-10-09"),
        _t("RVA15-260930-0004", "东南亚泰国", "2026-10-09"),
        _t("FBA19RJ5R9LK", "亚马逊加拿大", "2026-10-10"),
        _t("300378", "emag罗马尼亚", "2026-10-26",
           cls="空运", mode="空运", target=15),
    ]
    return arrived + pending


# ---------------------------------------------------------------------------
# ① 出行条件与位置
# ---------------------------------------------------------------------------
def test_pending_row_appears_with_counts():
    rows = rv.transport_rows(_g594_items(), today=_dt("2026-11-01"))
    pend = [r for r in rows if r.get("pendingCount")]
    assert len(pend) == 1
    r = pend[0]
    assert r["seg"] == "运输·在途(5票)"
    assert r["dept"] == "运输"
    # 起点 = 最早在途发货；终点留空 → 甘特图会延伸到今日
    assert r["t0"] == _dt("2026-10-09")
    assert r["t1"] is None
    assert r["noteText"] == ("已发10·到5·未到5：菲律宾、印尼、泰国、加拿大、罗马尼亚")


def test_pending_row_sits_between_transport_and_four_countries():
    rows = rv.transport_rows(_g594_items(), today=_dt("2026-11-01"))
    segs = [r["seg"] for r in rows]
    i_pend = next(i for i, s in enumerate(segs) if s.startswith("运输·在途"))
    # 前面是 运输·空运/海运，后面是 4 国
    assert all(segs[i].startswith("运输·") for i in range(i_pend))
    assert all(not segs[i].startswith("运输·") for i in range(i_pend + 1, len(segs)))


def test_no_pending_row_when_all_arrived():
    items = [it for it in _g594_items() if it["arr"]]
    rows = rv.transport_rows(items, today=_dt("2026-11-01"))
    assert not [r for r in rows if r.get("pendingCount")]


# ---------------------------------------------------------------------------
# ② 明细文案：站点去渠道家族前缀
# ---------------------------------------------------------------------------
def test_short_hub_strips_channel_prefix():
    assert rv._short_hub("亚马逊美国") == "美国"
    assert rv._short_hub("东南亚印尼") == "印尼"
    assert rv._short_hub("emag罗马尼亚") == "罗马尼亚"
    assert rv._short_hub("美客多墨西哥") == "墨西哥"
    # 没有前缀时原样返回，不能把站名吃掉
    assert rv._short_hub("加拿大") == "加拿大"
    assert rv._short_hub("") == ""


def test_pending_row_counts_are_whole_order_not_per_mode():
    """已发/到货/未到是**整单**口径（不是空运/海运分开），用户要的是"这批货"的进度。"""
    rows = rv.transport_rows(_g594_items(), today=_dt("2026-11-01"))
    r = next(x for x in rows if x.get("pendingCount"))
    assert r["note"].startswith("已发10票·到货5票·未到5票")


# ---------------------------------------------------------------------------
# ③ 只画不判：不许污染当前停留/超期/卡点
# ---------------------------------------------------------------------------
def test_pending_row_is_display_only():
    rows = rv.transport_rows(_g594_items(), today=_dt("2026-11-01"))
    r = next(x for x in rows if x.get("pendingCount"))
    assert r["displayOnly"] is True
    # days=None → 复盘「卡点=耗时最长段」与段统计都不会选中它
    assert r["days"] is None
    assert r["assessed"] is False
    assert r["target"] is None


def test_pending_row_not_treated_as_stay():
    """compute_inflight 里 displayOnly 行不进 state_rows → _pick_stay 看不到它。

    这里直接验「段状态机」这一层的契约：displayOnly 行若被喂进 state_rows，
    它会因为 t1 恒空、t0 较新而抢走「当前停留」—— 必须靠调用方过滤掉。
    """
    from app.inflight_service import ST_RUNNING, seg_state
    rows = rv.transport_rows(_g594_items(), today=_dt("2026-11-01"))
    r = next(x for x in rows if x.get("pendingCount"))
    st = seg_state(r, today=_dt("2026-11-01").date())
    # 它确实会被判成"进行中"（t0 有值、t1 空）—— 正因如此才必须排除在 state_rows 之外
    assert st["state"] == ST_RUNNING
    assert st["elapsed"] == 23


def test_display_only_row_would_steal_stay_if_not_filtered():
    """反证：漏掉 displayOnly 过滤，「当前停留」会从真实卡点变成「运输·在途」。

    G594 实测：不过滤 → 运输·在途(5票)；过滤 → 采购→发货末批。
    这个断言就是那条守卫的存在理由，删了它必挂。
    """
    from app.inflight_service import _pick_stay, seg_state
    today = _dt("2026-11-01").date()
    base = {"dept": "后勤", "seg": "采购→发货末批", "t0": _dt("2026-09-11"),
            "t1": None, "days": None, "target": 22, "judge": "⏳",
            "gap": False, "assessed": True}
    pend = {"dept": "运输", "seg": "运输·在途(5票)", "t0": _dt("2026-10-09"),
            "t1": None, "days": None, "target": None, "judge": "⏳",
            "gap": False, "assessed": False, "displayOnly": True,
            "pendingCount": 5}
    rows = [base, pend]
    sr_all = [(r, seg_state(r, today=today)) for r in rows]
    assert _pick_stay(sr_all)[0]["seg"] == "运输·在途(5票)"      # 不过滤 → 被抢
    sr_ok = [(r, seg_state(r, today=today)) for r in rows
             if not r.get("displayOnly")]
    assert _pick_stay(sr_ok)[0]["seg"] == "采购→发货末批"        # 过滤 → 正确


# ---------------------------------------------------------------------------
# ④ 甘特图「超期」列
# ---------------------------------------------------------------------------
def test_over_column_shows_pending_count():
    rows = rv.transport_rows(_g594_items(), today=_dt("2026-11-01"))
    r = next(x for x in rows if x.get("pendingCount"))
    item = {"judge": "⏳进行中(已23天)", "pendingCount": r["pendingCount"]}
    assert rp._g_over_text(item) == "在途5票"


def test_over_column_unchanged_for_normal_rows():
    assert rp._g_over_text({"judge": "✓"}) == "✓"
    assert rp._g_over_text({"judge": "✗超8天"}) == "超8天"
    assert rp._g_over_text({"judge": "—留空"}) == "—"
