# -*- coding: utf-8 -*-
"""test_dual_track.py — 双轨时间判定（系统轨判 / audit 轨对照）

口径来源：流程复盘/route_check_sys.py 的「节点双轨时间对照」+「段级 audit 两端耗时」。
搬进后台时划了三条线，本文件逐条钉住：

  ① 方向：差 = **audit − 系统**（正数=上报晚于单据日期，补录常态）。
     离线 md 里那句「差异 = 系统 − audit」与它自己的代码相反，这里以代码为准。
  ② 缺失：audit 缺一端就不算、不判，绝不拿业务日期或动作时间顶替。
  ③ 铁律：audit 轨**不参与**正式判定。判定永远只有 _seg_judge（系统轨）一票，
     dual 层只回答「换审批时间算，这条判定还站得住吗」。

第 4 节的 golden 直接读离线件的产出 xlsx 对账 —— 不拿本实现自证。
"""

from datetime import datetime
from pathlib import Path

import pytest

from app import dual_track as dt
from app.routers.review import _seg_judge

OFFLINE_XLSX = Path(
    r"C:\Users\dell\Desktop\work-main\流程复盘\output\route_路线复盘_系统口径.xlsx")


# ---------------------------------------------------------------------------
# 1. 节点差方向
# ---------------------------------------------------------------------------
def test_node_diff_is_audit_minus_system():
    """audit 晚 0.6 天 → +0.6；audit 早 2 天 → -2。方向反了整张表就会读反。"""
    nd = {"sys": datetime(2026, 10, 10), "audit": datetime(2026, 10, 10, 14, 24)}
    assert dt.node_diff(nd) == 0.6
    nd2 = {"sys": datetime(2026, 10, 12), "audit": datetime(2026, 10, 10)}
    assert dt.node_diff(nd2) == -2


def test_node_diff_requires_both_sides():
    """缺任一端 → None。不能把「只有 audit」当成 0 天差异。"""
    assert dt.node_diff({"sys": None, "audit": datetime(2026, 11, 5)}) is None
    assert dt.node_diff({"sys": datetime(2026, 11, 5), "audit": None}) is None
    assert dt.node_diff({}) is None


# ---------------------------------------------------------------------------
# 2. 段级 audit 两端耗时
# ---------------------------------------------------------------------------
def _nd(sys=None, audit=None):
    return {"sys": sys, "audit": audit}


def test_seg_audit_days_counts_both_audit_ends():
    """G602 实测：取数 audit 10-10 14:47 → 签样 audit 10-26 16:59 ≈ 16.1 天。"""
    nodes = {"取数": _nd(datetime(2026, 10, 7), datetime(2026, 10, 10, 14, 47)),
             "签样": _nd(datetime(2026, 10, 26), datetime(2026, 10, 26, 16, 59))}
    span = dt.seg_audit_days(nodes, "取数", "签样")
    assert dt.fmt_days(span["days"]) == 16.1
    assert span["missing"] == []


def test_seg_audit_days_blank_when_one_end_missing():
    """G602 的「签样→寄送」：寄送没有 audit（只有动作）→ 不给数，并点名缺哪头。"""
    nodes = {"签样": _nd(audit=datetime(2026, 10, 26, 16, 59)),
             "寄送": _nd(audit=None)}
    span = dt.seg_audit_days(nodes, "签样", "寄送")
    assert span["days"] is None
    assert span["missing"] == ["寄送"]


def test_seg_audit_days_does_not_round_to_date():
    """audit 一律带时分，段内不按日期取整（取整规则只属于系统业务列）。"""
    nodes = {"a": _nd(audit=datetime(2026, 10, 26, 16, 59)),
             "b": _nd(audit=datetime(2026, 10, 26, 17, 3))}
    span = dt.seg_audit_days(nodes, "a", "b")
    assert span["days"] > 0 and dt.fmt_days(span["days"]) == 0
    # 严格大于 0：还是同一分钟内的 4 分钟差，只是展示成 0 天
    assert span["days"] < 0.01


def test_seg_gap_is_audit_minus_system():
    assert dt.seg_gap(19, 16.1) == -2.9


# ---------------------------------------------------------------------------
# 3. 一致性分类：只用来分清「真慢」和「日期填错」
# ---------------------------------------------------------------------------
def test_cross_agrees_when_both_over():
    """G602 取数→签样：系统 19 天 vs audit 16.1 天，目标 12 → 两边都超，结论一致。"""
    r = dt.cross(_seg_judge(19, 12, "取数→签样"), 16.1, 12, _seg_judge)
    assert r["verdict"] == "agree"
    assert r["auditJudge"].startswith("✗")


def test_cross_flags_system_only_when_audit_passes():
    """系统轨超、audit 轨不超 → 「仅系统轨超线」，提示超时来自填报日期。"""
    r = dt.cross(_seg_judge(14, 10, "采购→本地仓到货"), 9, 10, _seg_judge)
    assert r["verdict"] == "system_only"
    assert r["verdictText"] == "仅系统轨超线"
    assert r["auditJudge"].startswith("✓")


def test_cross_flags_audit_worse():
    """系统轨过、audit 轨超 → 表单日期比审批好看，审批侧才是真慢。"""
    r = dt.cross(_seg_judge(8, 10, "采购→本地仓到货"), 12, 10, _seg_judge)
    assert r["verdict"] == "audit_worse"


def test_cross_gives_no_verdict_when_audit_incomplete():
    r = dt.cross("✗超2天", None, 10, _seg_judge)
    assert r["verdict"] == "incomplete"
    assert r["auditJudge"] is None


def test_cross_gives_no_verdict_when_system_side_not_a_pass_fail():
    """系统轨是「—缺时间 / ⚠反向 / —留空」时没有可比的两票，不能强行给「一致」。"""
    for j in ("—缺时间", "⚠反向(-3天)", "—留空", "—未设目标"):
        assert dt.cross(j, 9, 10, _seg_judge)["verdict"] == "na"


def test_cross_respects_null_target():
    """规则页把目标天清空（None）→ 不设目标 → 不评一致性，也不许当 0 天判超。"""
    r = dt.cross("✓", 9, None, _seg_judge)
    assert r["verdict"] == "notarget" and r["auditJudge"] is None


def test_judge_fn_is_injected_not_reimplemented():
    """dual 层不许自己复刻判定规则：传一个假函数，结论必须跟着它走。"""
    calls = []

    def fake(days, target):
        calls.append((days, target))
        return "✓" if days <= target else "✗超"

    r = dt.cross("✗超9天", 9, 10, fake)
    assert calls == [(9, 10)]
    assert r["verdict"] == "system_only"


# ---------------------------------------------------------------------------
# 4. golden：拿离线件产出对账（外部真值，不用本实现自证）
# ---------------------------------------------------------------------------
def _parse(v):
    from app.routers.review import _parse_dt
    return _parse_dt(v) if v not in (None, "") else None


def test_golden_offline_dual_sheet_matches():
    """离线「双轨时间对照」sheet 的每一行差异，本模块必须复算出同一个数。

    比的是**外部产物**（route_check_sys 跑出来的 xlsx），不是自己调用自己。

    容差分两档 —— 都是离线件自身的精度损失，不是本模块口径问题：
      · 节点行 0.15：表里 audit 只写到分钟、差异又四舍五入到 1 位小数，两头累计
        就能差 0.1（G575/拍摄交付 离线 20.5 vs 按显示值复算 20.4）。
      · 货件行 0.6：系统时间列被 `_sday()` 截成日期，可差异用带时分的原值算
        （G599/FBA19VFXGMGK 离线 2.7，用显示日期只能算出 2.6）。
    方向错会差一倍以上，这个容差挡不住方向错误，挡得住精度噪音。
    """
    if not OFFLINE_XLSX.exists():
        pytest.skip(f"离线件产出不存在：{OFFLINE_XLSX}")
    from openpyxl import load_workbook

    ws = load_workbook(OFFLINE_XLSX, read_only=True)["双轨时间对照"]
    rows = ws.iter_rows(values_only=True)
    head = list(next(rows))
    i_node, i_sys, i_aud, i_diff = (head.index("节点"), head.index("系统时间"),
                                    head.index("audit时间"), head.index("差异(天)"))
    checked = 0
    for r in rows:
        if not r or r[i_sys] in (None, "") or r[i_aud] in (None, ""):
            continue                          # 离线件只在两端齐时给差异
        want = r[i_diff]
        got = dt.node_diff({"sys": _parse(r[i_sys]), "audit": _parse(r[i_aud])})
        assert got is not None, f"{r[0]}/{r[i_node]} 两端都有值却算不出差异"
        if want in (None, ""):
            continue
        tol = 0.6 if str(r[i_node] or "").startswith("货件·") else 0.15
        assert abs(float(got) - float(want)) <= tol, (
            f"{r[0]}/{r[i_node]}：离线 {want} vs 后台 {got}（方向或精度不一致）")
        checked += 1
    assert checked >= 10, f"只对上了 {checked} 行，样本太少，检查表头是否变了"


def test_golden_offline_latest_operator_matches():
    """段级「最新审批人」与离线 xlsx「段级明细」同列对账。

    ⚠️ 唯一口径差：离线件会把人名后的括号备注剥掉（`谢雪娜(已离职)` → `谢雪娜`），
    后台**保留原文** —— 离职标记对催办有用，不该为了好看丢掉。所以比之前先剥括号，
    比的是"同一个人"，不是同一串字符。
    """
    if not OFFLINE_XLSX.exists():
        pytest.skip(f"离线件产出不存在：{OFFLINE_XLSX}")
    import re

    from openpyxl import load_workbook

    ws = load_workbook(OFFLINE_XLSX, read_only=True)["段级明细"]
    rows = ws.iter_rows(values_only=True)
    head = list(next(rows))
    i_sku, i_seg, i_op = (head.index("SKU"), head.index("环节"),
                          head.index("最新审批人"))
    want = {}
    for r in rows:
        if r and r[i_sku]:
            want.setdefault(r[i_sku], {})[r[i_seg]] = r[i_op]

    def bare(s):
        return re.sub(r"\s*[（(][^（）()]*[）)]", "", s or "").strip()

    checked = 0
    for sku in ("G602", "G600", "G604"):
        if sku not in want:
            continue
        got = {s["seg"]: s.get("latestOp") for s in _sku_payload(sku)["segments"]}
        for seg, w in want[sku].items():
            if seg not in got:
                continue                       # 运输/未发货行由 transport_rows 生成，不在此比
            assert bare(got[seg]) == bare(w), (
                f"{sku}/{seg} 最新审批人：离线 {w!r} vs 后台 {got[seg]!r}")
            checked += 1
    assert checked >= 20, f"只对上了 {checked} 行"


# ---------------------------------------------------------------------------
# 5. 接口契约：字段真的端得出去
# ---------------------------------------------------------------------------
def _sku_payload(code="G602"):
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    r = c.get(f"/api/review/sku/{code}", params={"category": "手机膜"})
    if r.status_code == 404:
        pytest.skip(f"当前库无 {code}")
    return r.json()


def test_payload_carries_dual_fields():
    j = _sku_payload()
    for name, nd in j["nodes"].items():
        assert "diffDays" in nd and "latestOp" in nd, f"{name} 缺双轨字段"
    for s in j["segments"]:
        for k in ("auditDays", "auditJudge", "dualGap",
                  "dualVerdict", "dualVerdictText", "auditMissing"):
            assert k in s, f"{s['seg']} 缺字段 {k}"
    for sh in j["shipments"]:
        for k in ("shipAudit", "shipAuditOp", "arrAudit", "arrAuditOp",
                  "shipGap", "arrGap"):
            assert k in sh, f"货件 {sh['trk']} 缺字段 {k}"


def test_payload_judge_still_system_only():
    """判定不受 audit 影响：每条非留空段的 judge 必须能由「系统轨耗时 + 目标」复算出来。

    这里直接拿 payload 里的 days/target 再跑一遍 _seg_judge —— 若哪天有人把 audit
    混进判定，复算就对不上（正是「双轨只对照」这条铁律的守门测试）。
    """
    j = _sku_payload()
    for s in j["segments"]:
        if s.get("gap") or s.get("days") is None or s.get("target") is None:
            continue
        if not str(s.get("judge", "")).startswith(("✓", "✗", "⚠")):
            continue
        assert s["judge"] == _seg_judge(s["days"], s["target"], s["seg"]), (
            f"{s['seg']} 判定不是按系统轨算的：{s['judge']}")
