# -*- coding: utf-8 -*-
"""test_dataadmin.py — 数据管理接口测试（批次扫描 / 导入 / 备份列表）

注意：本文件刻意**不触发真实重建**——导入类用例只走 skip 与 404 路径，
需要重建数据的场景一律 pytest.skip，避免测试运行把真实批次删了重灌。
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

VALID_STATES = {"new", "changed", "imported", "missing"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------
class TestScan:
    def test_response_shape(self, client):
        r = client.get("/api/etl/scan")
        assert r.status_code == 200
        d = r.json()
        for key in ("dataRoot", "dataRootExists", "items", "pending",
                    "pendingCount", "totalDocs", "totalNodes"):
            assert key in d, f"缺少字段 {key}"
        assert isinstance(d["items"], list) and d["items"]

    def test_item_fields_and_state(self, client):
        for it in client.get("/api/etl/scan").json()["items"]:
            assert it["state"] in VALID_STATES
            assert it["name"]
            assert it["docCount"] >= 0
            assert isinstance(it["fileCount"], int)
            assert isinstance(it["parts"], list)
            # imported 状态必须有过导入时间
            if it["state"] == "imported":
                assert it["importedAt"]

    def test_pending_consistent_with_items(self, client):
        d = client.get("/api/etl/scan").json()
        expect = [i["name"] for i in d["items"] if i["state"] in ("new", "changed")]
        assert d["pending"] == expect
        assert d["pendingCount"] == len(expect)

    def test_totals_are_non_negative(self, client):
        d = client.get("/api/etl/scan").json()
        assert d["totalDocs"] >= 0
        assert d["totalNodes"] >= 0


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------
class TestImport:
    def test_unknown_batch_returns_404(self, client):
        r = client.post("/api/etl/import", json={"batch": "并不存在的批次"})
        assert r.status_code == 404
        assert "detail" in r.json()

    def test_unchanged_batch_is_skipped(self, client):
        """已导入且文件指纹未变的批次必须走 skip，且不得改动已有数据"""
        before = client.get("/api/etl/scan").json()
        target = next((i for i in before["items"] if i["state"] == "imported"), None)
        if target is None:
            pytest.skip("当前没有 imported 状态的批次可供验证")

        r = client.post("/api/etl/import", json={"batch": target["name"]})
        assert r.status_code == 200
        body = r.json()
        assert body["imported"] == 0
        assert body["skipped"] == 1
        assert body["failed"] == 0
        assert body["results"][0]["status"] == "skipped"

        after = client.get("/api/etl/scan").json()
        same = next(i for i in after["items"] if i["name"] == target["name"])
        assert same["docCount"] == target["docCount"]
        assert same["state"] == "imported"

    def test_import_all_when_nothing_pending(self, client):
        """无待导入批次时，全量导入应全部 skip 且零失败。

        ⚠️ scan 的 items 含 DB 里的**退役批次记录**（目录已移走，fileCount=0）
        ——它们不是导入目标（导入目标 = 目录里真有数据文件的批次），所以
        skipped 应等于「活批次」数而不是 items 总数。
        """
        d = client.get("/api/etl/scan").json()
        if d["pendingCount"]:
            pytest.skip(f"存在待导入批次 {d['pending']}，跳过以免真实重建数据")

        r = client.post("/api/etl/import", json={})
        assert r.status_code == 200
        body = r.json()
        assert body["failed"] == 0
        assert body["imported"] == 0
        live = [i for i in d["items"] if i.get("fileCount")]
        assert body["skipped"] == len(live)


# ---------------------------------------------------------------------------
# 备份
# ---------------------------------------------------------------------------
class TestBackupList:
    def test_list_shape(self, client):
        r = client.get("/api/etl/backup")
        assert r.status_code == 200
        d = r.json()
        assert "backupDir" in d
        assert isinstance(d["items"], list)

    def test_item_fields(self, client):
        for it in client.get("/api/etl/backup").json()["items"]:
            assert it["name"]
            assert it["createdAt"]
            assert it["sizeKB"] >= 0
            assert isinstance(it["files"], list)
            # 快照目录名即时间戳 YYYYMMDD-HHMMSS
            assert len(it["name"]) == 15 and it["name"][8] == "-"

    def test_list_sorted_desc(self, client):
        names = [i["name"] for i in client.get("/api/etl/backup").json()["items"]]
        assert names == sorted(names, reverse=True)
