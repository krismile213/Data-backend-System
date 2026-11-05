# -*- coding: utf-8 -*-
r"""test_rules_center.py — 规则配置中心的键位契约（T08）

守两条：
  ① 已删除的死键 `daily_pipeline_slots` 不得复活——既不在 rules/defaults/meta 里，
     也不再被 PUT 接受（否则「删了还能存」= 没删干净）；
  ② 调度类信息只能以**只读** `pipeline` 字段带出，真源是**计划任务注册态**
     （`<SystemRoot>\System32\Tasks\DataAdmin-AutoPipeline` 的 StartBoundary），
     `scripts/install_auto_etl.ps1` 只是回落默认值；不能在可编辑 rules 里再出现
     一份会漂移的副本。

本文件只做读 + **注定 422 的写**（校验在写库之前失败），不触碰真实 rule_config。
"""

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rule_service import (_default_rules, _read_task_times,
                             _utc_boundary_to_local_hhmm, pipeline_schedule)

DEAD_KEYS = ["daily_pipeline_slots"]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def rules_payload(client):
    r = client.get("/api/rules")
    assert r.status_code == 200
    return r.json()


def test_dead_key_absent_everywhere(rules_payload):
    """已删键不得出现在 rules / defaults / meta 任何一处。"""
    for bucket in ("rules", "defaults", "meta"):
        for k in DEAD_KEYS:
            assert k not in rules_payload[bucket], f"{k} 仍出现在 {bucket}"


def test_dead_key_rejected_on_write(client):
    """已删键 PUT 必须 422（校验先于写库，不产生副作用）。"""
    for k in DEAD_KEYS:
        r = client.put("/api/rules", json={"patch": {k: ["09:20", "14:50"]}})
        assert r.status_code == 422, f"{k} 竟被接受（{r.status_code}）"


def test_unknown_key_still_rejected(client):
    """回归：未知键一律 422（别为了删一个键把兜底校验也删掉）。"""
    r = client.put("/api/rules", json={"patch": {"nope_xyz": 1}})
    assert r.status_code == 422


def test_pipeline_readonly_field_shape(rules_payload):
    """只读 pipeline 字段的形状：真源是**计划任务注册态**，不是页面能改的配置。"""
    p = rules_payload.get("pipeline")
    assert isinstance(p, dict), "GET /api/rules 必须带只读 pipeline 字段"
    assert p.get("taskName") == "DataAdmin-AutoPipeline"
    # 来源只有两种合法取值：实际任务 / 脚本默认值回落。两种都必须说清是哪个，
    # 免得把「安装脚本里写着但任务已改」的值当成实际时刻展示（2026-11-05 事故）。
    src = p.get("source", "")
    assert ("计划任务" in src) or ("install_auto_etl.ps1" in src), f"来源不明：{src!r}"
    if "install_auto_etl.ps1" in src:
        assert "脚本默认值" in src, "回落脚本时必须标注「脚本默认值」，不能冒充实际调度"
    # 时刻格式必须是 HH:MM；读不到时 ok=False 且 times 为空（不编数）
    if p.get("ok"):
        assert p["times"], "ok=True 却没有任何时刻"
        for t in p["times"]:
            assert re.match(r"^[0-2]\d:[0-5]\d$", t), f"时刻格式异常：{t!r}"
    else:
        assert p["times"] == []
    # 脚本默认值始终带出，便于一眼看出与实际情况有没有漂移
    assert isinstance(p.get("scriptTimes"), list)


def test_pipeline_schedule_matches_registered_task_or_script():
    """真源优先：有计划任务就用任务 XML（本地时区），没有才回落 ps1。

    判据全部与「本机实际存在什么」对照，不硬编码任何时刻 —— 硬编码正是
    09:20/14:50 那次漂移能长期不被发现的原因。
    """
    info = pipeline_schedule()
    task_xml = Path(info["taskFile"])

    if task_xml.exists():
        times, _ = _read_task_times(task_xml)
        if times:                     # 任务存在且有日历触发器 ⇒ 必须走任务，不走脚本
            assert info["ok"] is True
            assert info["times"] == times, "展示的时刻必须等于计划任务注册态"
            assert "计划任务" in info["source"]
    else:
        # 本机没装任务（CI/换机）⇒ 只能回落脚本，且必须自报"脚本默认值"
        assert "install_auto_etl.ps1" in info["source"]
        if info["ok"]:
            assert info["times"] == info["scriptTimes"]


def test_pipeline_schedule_utc_is_converted_to_local(tmp_path):
    """任务 XML 存 UTC，必须换算成本地时区（本机 GMT+8 实测差 8 小时）。"""
    boundaries = ["2026-09-23T01:00:00Z", "2026-09-23T06:10:00Z"]
    xml = ("<?xml version='1.0' encoding='UTF-16'?><Task><Triggers>"
           + "".join(f"<CalendarTrigger><StartBoundary>{b}</StartBoundary>"
                     "</CalendarTrigger>" for b in boundaries)
           + "</Triggers></Task>")
    p = tmp_path / "DataAdmin-AutoPipeline"
    p.write_bytes(xml.encode("utf-16"))

    info = pipeline_schedule(task_xml=p, ps1=tmp_path / "nope.ps1")
    expected = sorted({_utc_boundary_to_local_hhmm(b) for b in boundaries})
    assert info["ok"] is True
    assert info["times"] == expected
    assert "计划任务" in info["source"]
    # 反证 1：不能直接把 UTC 字符串截出来用（01:00/06:10 是错的那一版）
    assert info["times"] != ["01:00", "06:10"]
    assert "01:00" not in info["times"] and "06:10" not in info["times"]
    # 反证 2：本机时区已知时用**真值表**钉死，不让上面的 expected 变成函数自证
    if datetime.now().astimezone().utcoffset() == timedelta(hours=8):
        assert info["times"] == ["09:00", "14:10"]


def test_pipeline_schedule_falls_back_to_script_and_leaves_no_time(tmp_path):
    """无任务 → 回落脚本并标注；两处都缺 → ok=False 且 times 空（绝不编数）。"""
    ps1 = tmp_path / "install_auto_etl.ps1"
    ps1.write_text('$At1 = "09:00"\n$At2 = "14:10"\n', encoding="utf-8-sig")

    fallback = pipeline_schedule(task_xml=tmp_path / "missing", ps1=ps1)
    assert fallback["ok"] is True
    assert fallback["times"] == ["09:00", "14:10"]
    assert "脚本默认值" in fallback["source"]

    empty = pipeline_schedule(task_xml=tmp_path / "missing", ps1=tmp_path / "missing.ps1")
    assert empty["ok"] is False
    assert empty["times"] == []
    assert empty["source"]


def test_defaults_have_no_scheduler_keys():
    """代码默认值里不得再有任何「时刻类」配置（调度不归规则管）。"""
    for k in _default_rules():
        assert not k.endswith("_slots"), f"疑似把调度塞回规则里：{k}"


def test_registry_xlsx_path_key_registered(rules_payload):
    """新增键 `registry_xlsx_path`（独立登记表路径）必须三处齐备：rules / defaults / meta。"""
    from app.sku_alias import REGISTRY_XLSX_DEFAULT

    for bucket in ("rules", "defaults", "meta"):
        assert "registry_xlsx_path" in rules_payload[bucket], f"{bucket} 缺 registry_xlsx_path"
    assert rules_payload["defaults"]["registry_xlsx_path"] == REGISTRY_XLSX_DEFAULT
    # meta 必须有中文 label（规则页要能渲染）
    assert rules_payload["meta"]["registry_xlsx_path"].get("label")


def test_registry_xlsx_path_registered_in_str_keys():
    """该键必须在路由的文本键白名单里，否则保存会被 422（此处只读白名单，不写库）。"""
    from app.routers.rules import _STR_KEYS

    assert "registry_xlsx_path" in _STR_KEYS
