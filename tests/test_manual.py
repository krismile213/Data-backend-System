# -*- coding: utf-8 -*-
"""test_manual.py — 操作手册接口测试（列表 / 读取 / 白名单防护）"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


class TestManualList:
    def test_list_shape(self, client):
        r = client.get("/api/manual")
        assert r.status_code == 200
        d = r.json()
        assert "docsDir" in d
        keys = {i["key"] for i in d["items"]}
        assert {"sop", "handover"} <= keys

    def test_all_docs_exist_on_disk(self, client):
        for it in client.get("/api/manual").json()["items"]:
            assert it["exists"], f"文档文件缺失：{it['file']}"
            assert it["updatedAt"]

    def test_meta_fields(self, client):
        for it in client.get("/api/manual").json()["items"]:
            assert it["title"]
            assert it["desc"]
            assert it["file"].endswith(".md")


class TestManualContent:
    def test_sop_covers_all_parts(self, client):
        r = client.get("/api/manual/sop")
        assert r.status_code == 200
        d = r.json()
        assert d["key"] == "sop"
        assert "# 新品售前流程 SOP 操作手册" in d["content"]
        # SOP 必须覆盖全部 9 个环节
        for i in range(1, 10):
            assert f"### Part{i}" in d["content"], f"缺少 Part{i} 章节"
        # 三个部门终节点必须写明
        for node in ("签样完成", "最终上架与广告上架完成", "渠道仓到货"):
            assert node in d["content"]

    def test_handover_has_checklist(self, client):
        r = client.get("/api/manual/handover")
        assert r.status_code == 200
        content = r.json()["content"]
        assert "# 岗位职责与交接说明书" in content
        assert "交接" in content
        assert "已离职" in content

    def test_inflight_manual_covers_the_two_pages(self, client):
        """T08：手册第 3 篇必须讲清「事中日报 / 事后评估」两页。

        这份文档就是 T05 漏做、2026-09-24 补齐的交付物——把关键口径钉在测试里，
        避免有人把内容删空或把它从白名单摘掉还静默通过。
        """
        r = client.get("/api/manual/inflight")
        assert r.status_code == 200
        content = r.json()["content"]
        assert "# 流程事中与事后评估 使用说明" in content
        for token in ("事中日报", "事后评估", "85%", "超期", "预警",
                      "事中日报_YYYYMMDD.xlsx", "待校准"):
            assert token in content, f"手册缺少关键口径：{token}"

    def test_unknown_key_returns_404(self, client):
        r = client.get("/api/manual/not-exist")
        assert r.status_code == 404
        assert "detail" in r.json()


class TestManualSecurity:
    def test_path_traversal_blocked(self, client):
        """白名单机制：非白名单 key 一律读不到文件"""
        for bad in ("..%2FREADME", "..%2F..%2FREADME", ".env", "etc%2Fpasswd"):
            r = client.get(f"/api/manual/{bad}")
            assert r.status_code in (400, 404, 422), f"{bad} 竟返回 {r.status_code}"

    def test_cannot_read_arbitrary_md(self, client):
        """docs 目录下的其它文件不在白名单内，同样不可读"""
        r = client.get("/api/manual/%E4%BB%A3%E7%A0%81%E5%AE%A1%E6%9F%A5%E6%8A%A5%E5%91%8A-2026-09-12.md")
        assert r.status_code == 404
