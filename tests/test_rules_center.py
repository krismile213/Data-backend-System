# -*- coding: utf-8 -*-
"""test_rules_center.py — 规则配置中心的键位契约（T08）

守两条：
  ① 已删除的死键 `daily_pipeline_slots` 不得复活——既不在 rules/defaults/meta 里，
     也不再被 PUT 接受（否则「删了还能存」= 没删干净）；
  ② 调度类信息只能以**只读** `pipeline` 字段带出，真源是
     `scripts/install_auto_etl.ps1`，不能在可编辑 rules 里再出现一份会漂移的副本。

本文件只做读 + **注定 422 的写**（校验在写库之前失败），不触碰真实 rule_config。
"""

import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rule_service import _default_rules, pipeline_schedule

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
    """只读 pipeline 字段的形状：真源必须是脚本，不是页面能改的配置。"""
    p = rules_payload.get("pipeline")
    assert isinstance(p, dict), "GET /api/rules 必须带只读 pipeline 字段"
    assert p.get("taskName") == "DataAdmin-AutoPipeline"
    assert p.get("source", "").endswith("install_auto_etl.ps1")
    # 时刻来自 ps1，格式必须是 HH:MM；读不到时 ok=False 且 times 为空（不编数）
    if p.get("ok"):
        assert p["times"], "ok=True 却没有任何时刻"
        for t in p["times"]:
            assert re.match(r"^[0-2]\d:[0-5]\d$", t), f"时刻格式异常：{t!r}"
    else:
        assert p["times"] == []


def test_pipeline_schedule_reads_the_real_script():
    """真源可读：本仓库里 ps1 就在位，所以必须读到 2 个触发时刻。"""
    info = pipeline_schedule()
    assert info["ok"] is True, "读不到 scripts/install_auto_etl.ps1 的 $At1/$At2"
    assert len(info["times"]) == 2
    assert info["updatedAt"]
    # 且必须与已注册任务的 09:20 / 14:50 一致（上游同步 09:00/14:30 + 7.5min）
    assert info["times"] == ["09:20", "14:50"]


def test_defaults_have_no_scheduler_keys():
    """代码默认值里不得再有任何「时刻类」配置（调度不归规则管）。"""
    for k in _default_rules():
        assert not k.endswith("_slots"), f"疑似把调度塞回规则里：{k}"
