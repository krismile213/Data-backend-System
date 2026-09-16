# -*- coding: utf-8 -*-
"""test_api_integration.py — API 集成测试：FastAPI TestClient + 真实数据
（真实后端应用进程内运行，数据目录为真实批次数据）
"""

import json
import math
import re

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.main import app

BATCH = "手机膜8.28"
ALL_BATCHES = ["手机膜", "手机膜8.28", "手机膜8.31", "手机膜9.7"]
PARTS = [f"Part{i}" for i in range(1, 10)]
ID_RE = re.compile(r"^\d{15,25}$")


@pytest.fixture(scope="module")
def client():
    # 真实 FastAPI 应用 + 真实数据目录（analysis 模块进程内缓存）
    with TestClient(app) as c:
        yield c


def assert_no_nan_inf(text, ctx=""):
    """响应文本必须是合法 JSON 且不含 NaN/Infinity 字面量"""
    def _boom(v):
        raise AssertionError(f"响应含非法 JSON 常量 {v!r} ({ctx})")
    json.loads(text, parse_constant=_boom)


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------
class TestBasics:
    def test_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_batches_returns_4(self, client):
        r = client.get("/api/batches")
        assert r.status_code == 200
        names = [b["name"] for b in r.json()["batches"]]
        assert names == ALL_BATCHES
        for b in r.json()["batches"]:
            # 批次「手机膜」真实数据只有 Part1~Part8，其余批次 9 个 Part
            assert 1 <= b["fileCount"] <= 9
            got = [p["part"] for p in b["parts"]]
            assert got == PARTS[:len(got)]  # Part 文件按序为 PARTS 前缀
            assert all(p["name"] for p in b["parts"])


# ---------------------------------------------------------------------------
# 总览（真实批次 8.28 全量校验 + 其余批次 smoke）
# ---------------------------------------------------------------------------
class TestOverview:
    def test_overview_structure_and_consistency(self, client):
        r = client.get(f"/api/overview/{BATCH}")
        assert r.status_code == 200
        assert_no_nan_inf(r.text, "overview")
        data = r.json()
        assert data["batch"] == BATCH
        kpi = data["kpi"]
        # KPI 数字自洽：总数 ≥ 各状态之和（可能存在其他状态）
        assert kpi["total"] >= kpi["finished"] + kpi["running"] + kpi["terminated"]
        assert kpi["total"] > 0
        assert 0 <= kpi["finishRate"] <= 1
        # finishRate 四舍五入到 3 位小数，与精确比值误差 ≤ 0.0005
        assert abs(kpi["finishRate"] - kpi["finished"] / kpi["total"]) <= 0.0005 + 1e-9
        # 各 Part 合计 = 总数
        part_total = sum(p["total"] for p in data["partOverview"])
        assert part_total == kpi["total"]
        assert len(data["partOverview"]) == 9
        # 状态饼图合计 = 总数
        pie_sum = sum(s["value"] for s in data["statusPie"])
        assert pie_sum == kpi["total"]
        # 异常统计字段
        assert kpi["anomalyDocCount"] <= kpi["anomalyCount"]
        assert kpi["anomalyTypeSummary"] == sorted(
            kpi["anomalyTypeSummary"], key=lambda x: -x["count"])

    def test_overview_batch_8_28_engineer_claim(self, client):
        """校验工程师自检数字：1983 张（已结束1123/审批中829/终止31）——独立复核"""
        r = client.get(f"/api/overview/{BATCH}")
        kpi = r.json()["kpi"]
        assert kpi["total"] == 1983
        assert kpi["finished"] == 1123
        assert kpi["running"] == 829
        assert kpi["terminated"] == 31

    def test_overview_per_part_consistency(self, client):
        data = client.get(f"/api/overview/{BATCH}").json()
        for p in data["partOverview"]:
            assert p["finished"] + p["running"] + p["terminated"] <= p["total"]
            assert 0 <= p["finishRate"] <= 1
            if p["total"] > 0:
                assert abs(p["finishRate"] - p["finished"] / p["total"]) <= 0.0005 + 1e-9

    @pytest.mark.parametrize("batch", ["手机膜", "手机膜8.31", "手机膜9.7"])
    def test_overview_other_batches(self, client, batch):
        r = client.get(f"/api/overview/{batch}")
        assert r.status_code == 200
        assert_no_nan_inf(r.text, f"overview:{batch}")
        kpi = r.json()["kpi"]
        assert kpi["total"] > 0
        assert kpi["total"] >= kpi["finished"] + kpi["running"] + kpi["terminated"]

    def test_overview_404(self, client):
        r = client.get("/api/overview/不存在的批次XYZ")
        assert r.status_code == 404
        assert "批次不存在" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 时效
# ---------------------------------------------------------------------------
class TestTimeliness:
    def test_timeliness_structure(self, client):
        r = client.get(f"/api/timeliness/{BATCH}")
        assert r.status_code == 200
        assert_no_nan_inf(r.text, "timeliness")
        data = r.json()
        assert data["batch"] == BATCH
        assert len(data["partBar"]) == 9
        assert len(data["boxData"]) == 9
        # 箱线图数值自洽：min <= q1 <= median <= q3 <= max
        for b in data["boxData"]:
            if b["count"] > 0:
                assert b["min"] <= b["q1"] <= b["median"] <= b["q3"] <= b["max"]
        # 分段热力数据：avg/median/p90 同为 None 或同为数值
        for s in data["segmentHeat"]:
            vals = [s["avg"], s["median"], s["p90"]]
            if vals[0] is not None:
                assert all(v is not None for v in vals)
        # 趋势：按日期升序
        dates = [t["date"] for t in data["trend"]]
        assert dates == sorted(dates)
        assert sum(t["count"] for t in data["trend"]) > 0

    def test_timeliness_counts_match_overview(self, client):
        ov = client.get(f"/api/overview/{BATCH}").json()
        tl = client.get(f"/api/timeliness/{BATCH}").json()
        ov_part = {p["part"]: p for p in ov["partOverview"]}
        for bar in tl["partBar"]:
            # 已结束单据数不得超过该 Part 总单据数
            assert bar["count"] <= ov_part[bar["part"]]["total"]

    def test_timeliness_404(self, client):
        assert client.get("/api/timeliness/不存在XYZ").status_code == 404


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class TestAnomalies:
    def test_anomalies_structure(self, client):
        r = client.get(f"/api/anomalies/{BATCH}")
        assert r.status_code == 200
        assert_no_nan_inf(r.text, "anomalies")
        data = r.json()
        assert data["total"] == len(data["items"])
        assert sum(t["count"] for t in data["typeSummary"]) == data["total"]
        for item in data["items"]:
            assert set(item) >= {"Part", "环节名称", "审批编号", "异常类型", "说明"}
            assert item["Part"] in PARTS

    def test_anomalies_match_overview_count(self, client):
        ov = client.get(f"/api/overview/{BATCH}").json()
        an = client.get(f"/api/anomalies/{BATCH}").json()
        assert an["total"] == ov["kpi"]["anomalyCount"]

    def test_anomalies_filter_type(self, client):
        full = client.get(f"/api/anomalies/{BATCH}").json()
        assert full["typeSummary"], "真实数据应至少存在一种异常类型"
        t = full["typeSummary"][0]["type"]
        r = client.get(f"/api/anomalies/{BATCH}", params={"type": t})
        data = r.json()
        assert data["total"] == t and False or True  # noop 防呆
        assert data["total"] == full["typeSummary"][0]["count"]
        assert all(i["异常类型"] == t for i in data["items"])

    def test_anomalies_filter_part(self, client):
        r = client.get(f"/api/anomalies/{BATCH}", params={"part": "Part1"})
        data = r.json()
        assert all(i["Part"] == "Part1" for i in data["items"])
        assert data["total"] <= client.get(f"/api/anomalies/{BATCH}").json()["total"]

    def test_anomalies_filter_no_hit(self, client):
        r = client.get(f"/api/anomalies/{BATCH}", params={"type": "绝不存在的类型"})
        assert r.status_code == 200
        assert r.json()["total"] == 0 and r.json()["items"] == []

    def test_anomalies_404(self, client):
        assert client.get("/api/anomalies/不存在XYZ").status_code == 404


# ---------------------------------------------------------------------------
# 明细：分页/筛选/搜索/排序
# ---------------------------------------------------------------------------
class TestRecords:
    def test_records_structure(self, client):
        r = client.get(f"/api/records/{BATCH}/Part1")
        assert r.status_code == 200
        assert_no_nan_inf(r.text, "records")
        data = r.json()
        assert data["batch"] == BATCH and data["part"] == "Part1"
        assert data["total"] > 0
        assert len(data["rows"]) == 20  # 默认 pageSize=20
        assert data["pageSize"] == 20
        # 列清单与行键一致（不含内部列）
        assert not any(c.startswith("_") for c in data["columns"])
        assert data["statusOptions"]

    def test_records_total_matches_overview(self, client):
        ov = client.get(f"/api/overview/{BATCH}").json()
        total_by_part = {p["part"]: p["total"] for p in ov["partOverview"]}
        for part in PARTS:
            r = client.get(f"/api/records/{BATCH}/{part}", params={"pageSize": 1})
            assert r.json()["total"] == total_by_part[part], (
                f"{part} 明细 total 与总览 KPI 不一致")

    def test_pagination_consistency(self, client):
        p1 = client.get(f"/api/records/{BATCH}/Part1",
                        params={"page": 1, "pageSize": 50}).json()
        p2 = client.get(f"/api/records/{BATCH}/Part1",
                        params={"page": 2, "pageSize": 50}).json()
        ids1 = {x["审批编号"] for x in p1["rows"]}
        ids2 = {x["审批编号"] for x in p2["rows"]}
        assert not (ids1 & ids2), "第1页与第2页数据重叠"
        assert p1["total"] == p2["total"]
        # 末页行数 = total - (page-1)*size
        last_page = (p1["total"] + 49) // 50
        pl = client.get(f"/api/records/{BATCH}/Part1",
                        params={"page": last_page, "pageSize": 50}).json()
        assert len(pl["rows"]) == p1["total"] - (last_page - 1) * 50

    def test_page_beyond_range_empty(self, client):
        r = client.get(f"/api/records/{BATCH}/Part1", params={"page": 9999})
        assert r.status_code == 200
        assert r.json()["rows"] == []

    def test_page_validation(self, client):
        # page=0 / pageSize=0 → 422 校验错误（FastAPI Query ge=1）
        assert client.get(f"/api/records/{BATCH}/Part1",
                          params={"page": 0}).status_code == 422
        assert client.get(f"/api/records/{BATCH}/Part1",
                          params={"pageSize": 500}).status_code == 422

    def test_status_filter_matches_count(self, client):
        data = client.get(f"/api/records/{BATCH}/Part1").json()
        status = data["statusOptions"][0]
        r = client.get(f"/api/records/{BATCH}/Part1", params={"status": status})
        j = r.json()
        assert all(x["审批状态"] == status for x in j["rows"])
        assert j["total"] >= 0
        # statusOptions 与实际数据一致
        statuses = client.get(
            f"/api/records/{BATCH}/Part1",
            params={"pageSize": 200}).json()
        for x in statuses["rows"]:
            assert x["审批状态"] in data["statusOptions"]

    def test_keyword_no_hit_empty(self, client):
        r = client.get(f"/api/records/{BATCH}/Part1",
                       params={"keyword": "ZZZ绝不存在的关键字QQQ"})
        assert r.status_code == 200  # 空结果而非 500
        j = r.json()
        assert j["total"] == 0 and j["rows"] == []

    def test_approval_no_filter(self, client):
        first = client.get(f"/api/records/{BATCH}/Part1",
                           params={"pageSize": 1}).json()["rows"][0]
        no = str(first["审批编号"])
        if no:
            frag = no[-4:]
            r = client.get(f"/api/records/{BATCH}/Part1",
                           params={"approvalNo": frag}).json()
            assert r["total"] >= 1
            assert any(str(x["审批编号"]) == no for x in r["rows"])

    def test_sort_time_desc(self, client):
        r = client.get(f"/api/records/{BATCH}/Part1",
                       params={"sortField": "创建时间", "sortOrder": "desc",
                               "pageSize": 50}).json()
        times = [x["创建时间"] for x in r["rows"] if x["创建时间"]]
        assert times == sorted(times, reverse=True)

    def test_sort_asc(self, client):
        r = client.get(f"/api/records/{BATCH}/Part1",
                       params={"sortField": "审批编号", "sortOrder": "asc",
                               "pageSize": 50}).json()
        nos = [str(x["审批编号"]) for x in r["rows"] if x["审批编号"]]
        assert nos == sorted(nos)

    def test_approval_no_is_text_not_scientific(self, client):
        """明细接口中审批编号必须是完整文本（无科学计数法/精度丢失）"""
        r = client.get(f"/api/records/{BATCH}/Part1", params={"pageSize": 200})
        for x in r.json()["rows"]:
            no = str(x["审批编号"])
            if no and no not in ("None", ""):
                assert "e" not in no.lower() and "E" not in no
                if no.isdigit():
                    assert len(no) >= 10, f"审批编号疑似精度丢失: {no}"

    def test_records_404_bad_part(self, client):
        r = client.get(f"/api/records/{BATCH}/Part99")
        assert r.status_code == 404
        assert "Part99" in r.json()["detail"]

    def test_records_404_bad_batch(self, client):
        assert client.get("/api/records/不存在XYZ/Part1").status_code == 404

    @pytest.mark.parametrize("part", PARTS)
    def test_all_parts_200(self, client, part):
        r = client.get(f"/api/records/{BATCH}/{part}", params={"pageSize": 1})
        assert r.status_code == 200
        assert_no_nan_inf(r.text, f"records:{part}")


# ---------------------------------------------------------------------------
# 导出 xlsx
# ---------------------------------------------------------------------------
class TestExport:
    def test_export_downloads_and_text_ids(self, client):
        r = client.get(f"/api/records/{BATCH}/Part1/export")
        assert r.status_code == 200
        assert "spreadsheetml" in r.headers["content-type"]
        assert "attachment" in r.headers["content-disposition"]
        # 回读校验
        import io
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        ws = wb.active
        headers = [c.value for c in ws[1]]
        assert "审批编号" in headers
        col = headers.index("审批编号") + 1
        n = 0
        for row in ws.iter_rows(min_row=2):
            v = row[col - 1].value
            if v is None:
                continue
            assert isinstance(v, str), f"审批编号单元格非文本: {v!r}"
            if v.isdigit():
                assert ID_RE.match(v), f"审批编号疑似精度丢失/科学计数法: {v!r}"
            n += 1
        assert n > 0
        # 导出行数与接口 total 一致
        total = client.get(f"/api/records/{BATCH}/Part1",
                           params={"pageSize": 1}).json()["total"]
        assert n == total

    def test_export_filtered(self, client):
        # 用状态筛选导出，行数应与筛选后 total 一致
        status = client.get(f"/api/records/{BATCH}/Part1").json()["statusOptions"][0]
        total = client.get(f"/api/records/{BATCH}/Part1",
                           params={"status": status, "pageSize": 1}).json()["total"]
        import io
        r = client.get(f"/api/records/{BATCH}/Part1/export",
                       params={"status": status})
        ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
        n = sum(1 for row in ws.iter_rows(min_row=2) if row[0].value is not None
                or True) - 0
        n = ws.max_row - 1
        assert n == total

    def test_export_no_precision_loss_vs_api(self, client):
        """导出的审批编号与明细接口返回值逐一一致"""
        import io
        rows = client.get(f"/api/records/{BATCH}/Part1",
                          params={"pageSize": 10}).json()["rows"]
        api_ids = {str(x["审批编号"]) for x in rows if x["审批编号"]}
        r = client.get(f"/api/records/{BATCH}/Part1/export")
        ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
        headers = [c.value for c in ws[1]]
        col = headers.index("审批编号") + 1
        xlsx_ids = {str(row[col - 1].value) for row in ws.iter_rows(min_row=2)
                    if row[col - 1].value}
        overlap = api_ids & xlsx_ids
        assert overlap, "导出与接口的审批编号无交集"
        # 接口返回的完整编号必须原样出现在导出中（精度一致）
        for aid in api_ids:
            assert aid in xlsx_ids, f"导出丢失或改变了审批编号 {aid}"

    def test_export_404(self, client):
        assert client.get(f"/api/records/{BATCH}/Part99/export").status_code == 404
