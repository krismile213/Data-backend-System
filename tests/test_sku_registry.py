# -*- coding: utf-8 -*-
"""test_sku_registry.py — 独立登记表（SKU登记表.xlsx）的「同源 + 护栏」回归

2026-09-30：两张人工登记子表（SKU换号 / 人工登记）从 `商品资料.xlsx`（机器每月重导、
用户会整体替换的主数据）**迁出**到独立文件 `资产文件\\SKU登记表.xlsx`。原因：一换商品资料
文件，子表就消失 → 后端 `import_products`（全量覆盖）会**静默清空** `sku_alias`/`sku_hold`。

本文件守四类不变量：
  ① **同源常量**：后端 `sku_alias.REGISTRY_XLSX_DEFAULT`、rule 默认 `registry_xlsx_path`、
     离线 `route_check_sys.ALIAS_XLSX_DEFAULT`/`HOLD_XLSX_DEFAULT` **字符串逐字相等**；
  ② **读入口签名未变**：`read_alias_from_wb` / `read_hold_from_wb` 仍是 `(wb)`；
  ③ **离线脚本不 import 后端包**（孪生实现，故意的）；
  ④ **「异常绝不清库」护栏**：文件/子表缺失时 `import_registry` 绝不 DELETE（DB 行数不变），
     只有「表头在位且 0 行」才视为主动清空并高声告警。

⚠️ 全部用**临时 SQLite** 与临时 xlsx，绝不触碰生产 MySQL。
"""

import importlib.util
import inspect
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]                       # …/数据后台系统
REPO = ROOT.parent                                               # …/work-main
sys.path.insert(0, str(ROOT / "backend"))

from app import sku_alias, sku_hold                              # noqa: E402
from app.models import Base, ProductCatalog, SkuAlias, SkuHold   # noqa: E402  (F401)
from app.rule_service import _default_rules                      # noqa: E402
from etl import products_loader as pl                            # noqa: E402

OFFLINE_SCRIPT = REPO / "流程复盘" / "route_check_sys.py"
REGISTRY_XLSX = Path(r"C:\Users\dell\Desktop\work-main\资产文件\SKU登记表.xlsx")

HDR_ALIAS = ["旧SKU", "新SKU", "换号说明", "生效日期"]
HDR_HOLD = ["SKU", "说明", "登记日期"]


def _load_offline():
    sys.path.insert(0, str(OFFLINE_SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("route_check_sys_reg", OFFLINE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# ① 同源常量（三处必须逐字相同）
# ---------------------------------------------------------------------------
def test_registry_path_is_single_source():
    """后端常量 == rule 默认键 == 离线两常量（改一处必须同步，否则此用例红）。"""
    be = sku_alias.REGISTRY_XLSX_DEFAULT
    assert be == r"C:\Users\dell\Desktop\work-main\资产文件\SKU登记表.xlsx"
    assert _default_rules()["registry_xlsx_path"] == be, "rule 默认键与后端常量不一致"
    try:
        offline = _load_offline()
    except Exception as exc:                                     # noqa: BLE001
        pytest.skip(f"离线脚本不可加载（缺 pandas/openpyxl 等）: {exc}")
    assert str(offline.ALIAS_XLSX_DEFAULT) == be, "离线换号默认路径与后端不一致"
    assert str(offline.HOLD_XLSX_DEFAULT) == be, "离线人工登记默认路径与后端不一致"


def test_resolve_registry_path_defaults(monkeypatch):
    """无 env 覆盖、无 rule 覆盖时 resolve_registry_path() == 内置常量。"""
    monkeypatch.delenv("SKU_REGISTRY_XLSX", raising=False)
    assert str(sku_alias.resolve_registry_path()) == sku_alias.REGISTRY_XLSX_DEFAULT


# ---------------------------------------------------------------------------
# ② 读入口签名未变（现有测试依赖）
# ---------------------------------------------------------------------------
def test_read_from_wb_signatures_unchanged():
    assert list(inspect.signature(sku_alias.read_alias_from_wb).parameters) == ["wb"]
    assert list(inspect.signature(sku_hold.read_hold_from_wb).parameters) == ["wb"]


def test_scan_wb_exposes_state():
    """新增的 scan_*_wb 显式带出定位状态（护栏判据）。"""
    wb = Workbook()
    wb.active.title = "商品资料"                # 没有两张子表
    _, _, a_state = sku_alias.scan_alias_wb(wb)
    _, _, h_state = sku_hold.scan_hold_wb(wb)
    assert a_state == "no_sheet" and h_state == "no_sheet"


# ---------------------------------------------------------------------------
# ③ 离线脚本不 import 后端包（孪生实现）
# ---------------------------------------------------------------------------
def test_offline_script_does_not_import_backend():
    txt = OFFLINE_SCRIPT.read_text(encoding="utf-8")
    bad = re.findall(r"^\s*(?:import|from)\s+(?:app|backend|etl)\b", txt, re.M)
    assert not bad, f"离线脚本不得 import 后端包，命中：{bad}"


# ---------------------------------------------------------------------------
# ④ 老清库路径已消失：import_products 不再 DELETE alias/hold
# ---------------------------------------------------------------------------
def test_import_products_no_longer_touches_alias_hold():
    src = inspect.getsource(pl.import_products)
    assert "delete(SkuAlias)" not in src, "import_products 仍在 DELETE sku_alias（清库元凶）"
    assert "delete(SkuHold)" not in src, "import_products 仍在 DELETE sku_hold（清库元凶）"


def test_import_registry_owns_the_overwrite():
    """正向对照：全量覆盖的 DELETE 现在只应出现在 import_registry 里。"""
    src = inspect.getsource(pl.import_registry)
    assert "delete(SkuAlias)" in src and "delete(SkuHold)" in src


# ---------------------------------------------------------------------------
# ④ 护栏回归（临时 SQLite + 临时 xlsx，不碰生产库）
# ---------------------------------------------------------------------------
def _registry_xlsx(path, alias_rows=None, hold_rows=None,
                   with_alias=True, with_hold=True):
    """造一个登记表夹具：表头逐字、数据从第 2 行起。"""
    wb = Workbook()
    if with_alias:
        ws = wb.active
        ws.title = sku_alias.ALIAS_SHEET
        ws.append(list(HDR_ALIAS))
        for r in (alias_rows or []):
            ws.append(list(r))
    else:
        wb.active.title = "占位"          # 不留空 Sheet
    if with_hold:
        ws = wb.create_sheet(sku_hold.HOLD_SHEET)
        ws.append(list(HDR_HOLD))
        for r in (hold_rows or []):
            ws.append(list(r))
    wb.save(path)
    return path


def _temp_session():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)


def _seed(sess):
    now = datetime.now()
    sess.execute(SkuAlias.__table__.insert(), [
        {"old_sku": "G493", "new_sku": "G510", "note": "n1",
         "effective_at": "2025-11-19", "updated_at": now},
        {"old_sku": "G494", "new_sku": "G533", "note": "n2",
         "effective_at": "2026-03-02", "updated_at": now},
    ])
    sess.execute(SkuHold.__table__.insert(), [
        {"sku": "G495", "reason": "r1", "registered_at": "2026-09-28", "updated_at": now},
        {"sku": "G524", "reason": "r2", "registered_at": "2026-09-28", "updated_at": now},
    ])
    sess.commit()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """隔离 registry 索引文件 → 不写生产 state/registry_index.json。"""
    monkeypatch.setattr(pl, "REGISTRY_INDEX_PATH", tmp_path / "registry_index.json")
    _eng, Sess = _temp_session()
    with Sess() as s:
        _seed(s)
        yield s, tmp_path


def test_guard_missing_file_keeps_db(isolated):
    """登记文件不存在 → 绝不 DELETE，DB 行数不变，status=warned，notes 含显著告警。"""
    s, tmp = isolated
    res = pl.import_registry(session=s, force=True, path=tmp / "不存在的登记表.xlsx")
    assert res["status"] == "warned"
    assert s.query(SkuAlias).count() == 2, "缺文件竟清了 sku_alias！"
    assert s.query(SkuHold).count() == 2, "缺文件竟清了 sku_hold！"
    assert any("不存在" in n for n in res["notes"])
    assert any("未执行任何 DELETE" in n for n in res["notes"])
    assert res["lastError"]


def test_guard_missing_sheet_keeps_db(isolated):
    """文件在但缺『人工登记』子表 → 仍绝不 DELETE（全有才覆盖）。"""
    s, tmp = isolated
    p = _registry_xlsx(tmp / "reg.xlsx",
                       alias_rows=[("G999", "G998", "x", "")], with_hold=False)
    res = pl.import_registry(session=s, force=True, path=p)
    assert res["status"] == "warned"
    assert s.query(SkuAlias).count() == 2 and s.query(SkuHold).count() == 2
    assert any(sku_hold.HOLD_SHEET in n for n in res["notes"])


def test_guard_missing_header_keeps_db(isolated):
    """子表在但表头未命中（前 6 行无表头）→ 绝不 DELETE。"""
    s, tmp = isolated
    p = tmp / "reg_bad_hdr.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = sku_alias.ALIAS_SHEET
    for i in range(sku_alias.MAX_HDR_SCAN + 1):
        ws.append([f"说明{i}", "", "", ""])
    ws = wb.create_sheet(sku_hold.HOLD_SHEET)
    ws.append(list(HDR_HOLD))
    ws.append(["G495", "r", "2026-09-28"])
    wb.save(p)
    res = pl.import_registry(session=s, force=True, path=p)
    assert res["status"] == "warned"
    assert s.query(SkuAlias).count() == 2 and s.query(SkuHold).count() == 2


def test_full_overwrite_when_both_sheets_ok(isolated):
    """两张子表都在且表头在位 → 全量覆盖（含 before/after 计数）。"""
    s, tmp = isolated
    p = _registry_xlsx(tmp / "reg_ok.xlsx",
                       alias_rows=[("G1", "G2", "换", "2026-01-01")],
                       hold_rows=[("G3", "免", "2026-01-01")])
    res = pl.import_registry(session=s, force=True, path=p)
    assert res["status"] == "imported"
    assert [x.old_sku for x in s.query(SkuAlias).all()] == ["G1"]
    assert [x.sku for x in s.query(SkuHold).all()] == ["G3"]
    assert res["aliasBefore"] == 2 and res["aliasAfter"] == 1
    assert res["holdBefore"] == 2 and res["holdAfter"] == 1


def test_empty_sheets_clear_but_warn_loudly(isolated):
    """表头在位但 0 数据行 = 用户主动清空 → 允许 DELETE，但必须高声告警 + 记录计数。"""
    s, tmp = isolated
    p = _registry_xlsx(tmp / "reg_empty.xlsx", alias_rows=[], hold_rows=[])
    res = pl.import_registry(session=s, force=True, path=p)
    assert res["status"] == "imported"
    assert s.query(SkuAlias).count() == 0 and s.query(SkuHold).count() == 0
    assert any("主动清空" in n for n in res["notes"])
    assert res["aliasBefore"] == 2 and res["aliasAfter"] == 0
    assert res["holdBefore"] == 2 and res["holdAfter"] == 0


def test_fingerprint_skip_second_run(isolated):
    """同一文件第二次调用（未 force）→ 指纹未变，跳过（不重复覆盖）。"""
    s, tmp = isolated
    p = _registry_xlsx(tmp / "reg_fp.xlsx",
                       alias_rows=[("G1", "G2", "x", "")],
                       hold_rows=[("G3", "r", "")])
    first = pl.import_registry(session=s, force=True, path=p)
    assert first["status"] == "imported"
    second = pl.import_registry(session=s, force=False, path=p)
    assert second["status"] == "skipped"


# ---------------------------------------------------------------------------
# 真登记表：默认解析读到的就是独立文件（文件不在则 skip）
# ---------------------------------------------------------------------------
def test_default_reads_registry_file():
    if not REGISTRY_XLSX.is_file():
        pytest.skip(f"登记表不存在: {REGISTRY_XLSX}")
    a_rows, a_notes = sku_alias.read_alias_sheet()        # 无参 → resolve_registry_path()
    h_rows, h_notes = sku_hold.read_hold_sheet()
    assert not a_notes and not h_notes, f"默认读不应有告警：{a_notes} {h_notes}"
    assert {r["old"] for r in a_rows} >= {"G493", "G494"}
    assert {r["sku"] for r in h_rows} >= {"G495", "G524", "G525", "G526", "G527", "G528"}


# ---------------------------------------------------------------------------
# ⑤ 表头**大小写折叠**（QA 发现 A）：小写表头必须读到与大小写混写**同样**的行
# ---------------------------------------------------------------------------
# 修复前：`_pick` / `_alias_pick` 只去空格、不折叠大小写 → 表头写成 `旧sku|新sku`
# 会命中 0 条、state=no_header（docstring 却承诺「容忍大小写」）。现两侧同源折叠大写。
# ---------------------------------------------------------------------------
LOWER_HDR_ALIAS = ["旧sku", "新sku", "换号说明", "生效日期"]
LOWER_HDR_HOLD = ["sku", "说明", "登记日期"]


def _lower_case_registry(path):
    """夹具：两张子表都用**全小写**表头（`旧sku`/`新sku`、`sku`），数据从第 2 行起。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sku_alias.ALIAS_SHEET
    ws.append(list(LOWER_HDR_ALIAS))
    ws.append(["G493", "G510", "机型变更", "2025-11-19"])
    ws.append(["G494", "G533", "机型变更2", "2026-03-02"])
    ws = wb.create_sheet(sku_hold.HOLD_SHEET)
    ws.append(list(LOWER_HDR_HOLD))
    ws.append(["G495", "免走完", "2026-09-28"])
    ws.append(["G524", "老单停半路", "2026-09-28"])
    wb.save(path)
    return path


def test_header_lowercase_reads_same_rows(tmp_path):
    """小写表头（`旧sku`/`新sku`、`sku`）也能读到同样的行（修复前 = 0 条 / no_header）。"""
    p = _lower_case_registry(tmp_path / "reg_lower_hdr.xlsx")

    a_rows, a_notes = sku_alias.read_alias_sheet(p)
    assert not a_notes, f"小写表头不应告警：{a_notes}"
    assert [(r["old"], r["new"]) for r in a_rows] == [("G493", "G510"), ("G494", "G533")]

    h_rows, h_notes = sku_hold.read_hold_sheet(p)
    assert not h_notes, f"小写表头不应告警：{h_notes}"
    assert [r["sku"] for r in h_rows] == ["G495", "G524"]


def test_header_lowercase_import_registry_overwrites(isolated, tmp_path):
    """小写表头下护栏不误判：两表齐全 → 全量覆盖（不是 no_header 的 warned）。"""
    s, tmp = isolated
    p = _lower_case_registry(tmp / "reg_lower_import.xlsx")
    res = pl.import_registry(session=s, force=True, path=p)
    assert res["status"] == "imported", res
    assert [x.old_sku for x in s.query(SkuAlias).all()] == ["G493", "G494"]
    assert [x.sku for x in s.query(SkuHold).all()] == ["G495", "G524"]


def test_header_lowercase_cross_impl_aligned(tmp_path):
    """小写表头下后端 ↔ 离线读法**逐键相同**（两侧同源折叠大小写，改一处必须同步）。"""
    try:
        offline = _load_offline()
    except Exception as exc:                                     # noqa: BLE001
        pytest.skip(f"离线脚本不可加载（缺 pandas/openpyxl 等）: {exc}")

    p = _lower_case_registry(tmp_path / "reg_lower_cross.xlsx")

    be_rows, _ = sku_alias.read_alias_sheet(p)
    be_alias = {r["old"]: r["new"] for r in be_rows}
    off_alias = {k: v["新号"] for k, v in offline.read_alias_map(p).items()}
    assert be_alias == off_alias == {"G493": "G510", "G494": "G533"}

    be_h, _ = sku_hold.read_hold_sheet(p)
    be_hold = {r["sku"]: r["reason"] for r in be_h}
    off_hold = {k: v["说明"] for k, v in offline.read_hold_map(p).items()}
    assert be_hold == off_hold == {"G495": "免走完", "G524": "老单停半路"}
