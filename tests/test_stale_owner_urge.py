# -*- coding: utf-8 -*-
"""久悬单 + 按责任人·催办（2026-11-06 方案4 / 方案1）契约测试。

口径（用户确认）：
  · 久悬 = **当前停留段**超期 ≥90 天（OVER_STALE）—— 历史段超 90 但当前段在跑的不算；
    这批单子催已失效，需决策（重启评估 / 登记豁免 / 关闭）。
  · 按责任人·催办 = **SKU 级**（与「本月绩效视角」的段级考核分工）；
    块内按距超时升序，块间久悬多 → 超期多 → 在研多。
  · 两张表恒产（0 条时有占位说明行），顺序：久悬单跟在超期明细后、
    按责任人·催办跟在绩效视角后 —— 这一条由 test_inflight/test_exemption 的全量
    sheetnames 断言守着，这里只测内容。
"""

import io

from openpyxl import load_workbook

from app import reporting


def _item(sku, owner, days_left, target=10, elapsed=10, dept="运营",
          seg="取数→签样", status="超期", warn=None):
    return {"sku": sku, "model": f"MODEL-{sku}", "status": status,
            "staySeg": seg, "staySegDept": dept,
            "stayElapsed": elapsed, "stayTarget": target,
            "stayDaysLeft": days_left, "warnSegs": warn or [],
            "multiRound": False, "stayOver": days_left is not None and days_left < 0,
            "overSegs": [], "rootCause": {}, "owner": owner}


def _gen(items):
    alerts = {"todayNewOverdue": [], "ongoingOverdue": [], "warnings": []}
    return load_workbook(io.BytesIO(reporting.daily_report(
        category="手机膜", computed_at="2026-11-06 22:00", date_str="2026-11-06",
        items=items, alerts=alerts, gantts=[])))


def _rows(ws, start=4, cols=12):
    out = []
    for r in range(start, ws.max_row + 1):
        vals = [ws.cell(row=r, column=c).value for c in range(1, cols + 1)]
        if any(v is not None for v in vals):
            out.append(vals)
    return out


# ---------------------------------------------------------------------------
# 久悬单
# ---------------------------------------------------------------------------
def test_stale_only_current_segment_over_90_and_sorted():
    """只有当前段超期 ≥90 天进久悬单；按超期天数降序；89.9 天不进。"""
    wb = _gen([
        _item("G538", "李杰华", -205.0, elapsed=215, seg="运输→4国"),
        _item("G600", "李杰华", -95.0, elapsed=105),
        _item("G601", "夏康富", -89.9, elapsed=99.9),      # 差一点，不算久悬
        _item("G602", "夏康富", 5.0, elapsed=5, status="进行中"),  # 没超期
    ])
    ws = wb["久悬单"]
    rows = _rows(ws)
    assert len(rows) == 2, f"应只有 2 条久悬，实际 {len(rows)}"
    assert rows[0][0] == "G538" and rows[1][0] == "G600", "按超期天数降序"
    assert rows[0][6] == 205.0
    # 运输域久悬 → 建议动作走货件核对，不是「重启评估」
    assert "货件" in (rows[0][8] or "")


def test_stale_action_branches():
    """建议动作三分支：运输域 / 无目标段 / 常规；只出文案，不改状态。"""
    assert "货件" in reporting.stale_action(
        {"staySegDept": "运输", "staySeg": "运输→4国", "stayTarget": 15})
    assert "货件" in reporting.stale_action(
        {"staySegDept": "4国", "staySeg": "送达·未发货", "stayTarget": None})
    assert "无目标" in reporting.stale_action(
        {"staySegDept": "后勤", "staySeg": "采购→本地仓到货", "stayTarget": None})
    assert reporting.stale_action(
        {"staySegDept": "运营", "staySeg": "取数→签样", "stayTarget": 10}
    ).startswith("重启评估")


def test_stale_sheet_placeholder_when_empty():
    """无久悬单 → 恒产本表 + 占位说明行（不产生空页，与本月表一致）。"""
    wb = _gen([_item("G1", "张三", -3.0)])
    ws = wb["久悬单"]
    assert ws.cell(row=4, column=1).value == "（无超期 ≥90 天的久悬单——好现象）"


# ---------------------------------------------------------------------------
# 按责任人 · 催办
# ---------------------------------------------------------------------------
def test_owner_sheet_groups_and_sorts():
    """块间：久悬多 → 超期多 → 在研多；块内：距超时升序（超最多的在最上）。"""
    wb = _gen([
        _item("A1", "夏康富", -5.0),
        _item("A2", "夏康富", -2.0),
        _item("B1", "李杰华", -200.0, seg="运输→4国", dept="运输"),  # 久悬
        _item("B2", "李杰华", -3.0),
        _item("B3", "李杰华", 8.0, status="进行中"),
    ])
    ws = wb["按责任人·催办"]
    rows = _rows(ws, cols=11)
    names = [r[0] for r in rows if r[0]]
    assert names == ["李杰华", "夏康富"], "久悬多的责任人排最前"
    # 李杰华块首行：在研 3 / 超期 2 / 久悬 1；明细按距超时升序
    li = [r for r in rows if r[0] == "李杰华"][0]
    assert li[1] == 3 and li[2] == 2 and li[3] == 1
    li_skus = [r[4] for r in rows[rows.index(li):rows.index(li) + 3]]
    assert li_skus == ["B1", "B2", "B3"], "块内按距超时升序"
    # 块内续行的汇总列必须为空（不重复计数，透视/合计才不翻倍）
    cont = rows[rows.index(li) + 1]
    assert cont[1] is None and cont[2] is None and cont[3] is None


def test_owner_sheet_status_texts():
    """距超时人话 + 催办动作分档（久悬走 stale_action）。"""
    wb = _gen([
        _item("S1", "张三", -120.0, elapsed=130),                 # 久悬
        _item("S2", "张三", -6.0),                                # 催当前环节
        _item("S3", "张三", 2.0, target=None, status="进行中"),    # 无目标
        _item("S4", "张三", 3.0, warn=["签样→寄送"], status="预警"),  # 提前干预
        _item("S5", "张三", 9.0, status="进行中"),                 # 正常跟进
    ])
    ws = wb["按责任人·催办"]
    rows = {r[4]: r for r in _rows(ws, cols=11) if r[4]}
    assert rows["S1"][9] == "已超120天" and "重启评估" in (rows["S1"][10] or "")
    assert rows["S2"][9] == "已超6天" and rows["S2"][10] == "催当前环节"
    assert rows["S3"][9] == "无目标" and "人工判断" in (rows["S3"][10] or "")
    assert rows["S4"][9] == "剩3天" and "提前干预" in (rows["S4"][10] or "")
    assert rows["S5"][10] == "正常跟进"


def test_owner_sheet_unassigned_group():
    """owner 缺失 → 「(未配置)」成组，不静默丢行。"""
    it = _item("N1", None, -4.0)
    it["owner"] = ""
    wb = _gen([it])
    ws = wb["按责任人·催办"]
    assert any(r[0] == "(未配置)" for r in _rows(ws, cols=11))
