# -*- coding: utf-8 -*-
"""test_sku_registry_verify_edward.py — QA 独立证伪验证（Edward / 严过关）

对 `SKU登记表.xlsx` 迁移重构做**独立**验证，目标是**证伪**而不是复跑现成用例。
不修改 `backend/` / `流程复盘/` 任何源文件；全部用**临时 SQLite + 临时 xlsx**，
绝不触碰生产 MySQL（也不写生产 state/ 索引）。

覆盖 team-lead 要求的 8 条：
  1. 清库路径消失（静态 AST + 静态文本 + 动态隔离会话复现原事故路径）
  2. `import_registry` 护栏 4 分支（自己构造 4 种输入）
  3. 跨实现对齐（真实登记表：后端 ↔ 离线 逐键逐字段一致）
  4. 同源常量四者逐字相等（+ LEGACY / CATALOG 不漂移）
  5. 离线脚本可独立运行（无后端包 import）+ 缺文件时**打印**告警（非静默 {}）
  6. 原事故端到端复现（旧逻辑 0 条 / 新逻辑 2+6 条）+ 8 个 SKU 判定入口核对
  7. 边角攻击（改名移走 / 子表名写错 / 表头在第 3 行 / 标题行防护 / 全角空格大小写）
  8. 全量回归由外部命令完成（本文件只负责前 7 条的可复现固化）
"""

import ast
import contextlib
import importlib.util
import inspect
import io
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]                       # …/数据后台系统
REPO = ROOT.parent                                               # …/work-main
sys.path.insert(0, str(ROOT / "backend"))

from app import sku_alias, sku_hold                              # noqa: E402
from app.models import Base, ProductCatalog, SkuAlias, SkuHold   # noqa: E402
from app.rule_service import _default_rules                      # noqa: E402
from etl import products_loader as pl                            # noqa: E402

OFFLINE_SCRIPT = REPO / "流程复盘" / "route_check_sys.py"
REAL_REGISTRY = Path(r"C:\Users\dell\Desktop\work-main\资产文件\SKU登记表.xlsx")
REAL_CATALOG = Path(r"C:\Users\dell\Desktop\work-main\资产文件\商品资料.xlsx")

HDR_ALIAS = ["旧SKU", "新SKU", "换号说明", "生效日期"]
HDR_HOLD = ["SKU", "说明", "登记日期"]

REGISTRY_ALIAS_KEYS = {"G493", "G494"}
REGISTRY_HOLD_KEYS = {"G495", "G524", "G525", "G526", "G527", "G528"}


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _load_offline():
    sys.path.insert(0, str(OFFLINE_SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("route_check_sys_edward", OFFLINE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def offline():
    if not OFFLINE_SCRIPT.is_file():
        pytest.skip(f"离线脚本不存在：{OFFLINE_SCRIPT}")
    try:
        return _load_offline()
    except Exception as exc:                                     # noqa: BLE001
        pytest.skip(f"离线脚本不可加载（缺 pandas/openpyxl 等）：{exc}")


@pytest.fixture
def sqlite_env(tmp_path, monkeypatch):
    """隔离 SQLAlchemy 引擎/索引路径 —— 保证不碰生产 MySQL、不写生产 state/。

    返回 (engine, Session, tmp_path)。
    """
    db = tmp_path / "qa.sqlite"
    eng = create_engine(f"sqlite:///{db}", future=True)
    Base.metadata.create_all(eng)
    Sess = sessionmaker(bind=eng, expire_on_commit=False)
    # products_loader 顶层 `from app.db import engine/SessionLocal` → 打补丁到同名符号
    monkeypatch.setattr(pl, "engine", eng, raising=False)
    monkeypatch.setattr(pl, "SessionLocal", Sess, raising=False)
    monkeypatch.setattr(pl, "INDEX_PATH", tmp_path / "products_index.json")
    monkeypatch.setattr(pl, "REGISTRY_INDEX_PATH", tmp_path / "registry_index.json")
    # 不写生产 state/product_grades.json
    monkeypatch.setattr(pl, "save_grades", lambda *a, **k: "", raising=False)
    return eng, Sess, tmp_path


def _seed_alias_hold(sess):
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


def _make_registry(path, alias_rows=None, hold_rows=None,
                   with_alias=True, with_hold=True,
                   alias_header=None, hold_header=None,
                   alias_prelude=(), hold_prelude=()):
    """造登记表夹具；可自定义表头/前置行以构造边角输入。"""
    wb = Workbook()
    if with_alias:
        ws = wb.active
        ws.title = sku_alias.ALIAS_SHEET
        for line in alias_prelude:
            ws.append(list(line))
        ws.append(list(alias_header or HDR_ALIAS))
        for r in (alias_rows or []):
            ws.append(list(r))
    else:
        wb.active.title = "占位"
    if with_hold:
        ws = wb.create_sheet(sku_hold.HOLD_SHEET)
        for line in hold_prelude:
            ws.append(list(line))
        ws.append(list(hold_header or HDR_HOLD))
        for r in (hold_rows or []):
            ws.append(list(r))
    wb.save(path)
    return path


def _make_catalog(path, rows=None, header=None, desc=None):
    """造一份**不含** SKU换号/人工登记 子表的商品资料工作簿（= 事故现场）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = pl.SHEET_NAME                                  # '商品资料'
    ws.append([desc or "A1 档位说明占位：A:1个月销量500套及以上。X:暂无销售数据的。"])
    ws.append(list(header or ["SKU", "类型", "状态", "型号", "销量得分", "参考成本价"]))
    for r in (rows or [["G999", "水凝膜", "在售", "M1", "A", "12.5"]]):
        ws.append(list(r))
    wb.save(path)
    return path


# ===========================================================================
# 检查 1 —— 清库路径确实消失（静态 + 动态）
# ===========================================================================
def test_01a_static_text_no_delete_alias_hold():
    """静态（文本）：import_products 函数体内没有 delete(SkuAlias)/delete(SkuHold)。"""
    src = inspect.getsource(pl.import_products)
    assert "delete(SkuAlias)" not in src, "import_products 仍在 DELETE sku_alias"
    assert "delete(SkuHold)" not in src, "import_products 仍在 DELETE sku_hold"
    # 反向对照：覆盖 product_catalog 的 DELETE 仍在（否则就不是"只覆盖商品资料"了）
    assert "delete(ProductCatalog)" in src


def test_01b_static_ast_references_no_alias_hold():
    """静态（AST，比文本更强）：import_products 的 AST 里**不出现** SkuAlias/SkuHold 标识符。

    AST 天然不含注释，故可排除"仅出现在注释里"的假阳性/假阴性。
    """
    tree = ast.parse(inspect.getsource(pl.import_products))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    assert "SkuAlias" not in names, "import_products 的 AST 引用了 SkuAlias"
    assert "SkuHold" not in names, "import_products 的 AST 引用了 SkuHold"


def test_01c_dynamic_catalog_without_subtables_keeps_db(sqlite_env):
    """动态复现原事故路径：商品资料**不含**两张子表 → 调 import_products 后 alias/hold 行数不变。

    这正是 2026-09-29 事故的复现：旧逻辑会在这里静默清空两张表。
    """
    eng, Sess, tmp = sqlite_env
    catalog = _make_catalog(tmp / "商品资料_无子表.xlsx")
    with Sess() as s:
        _seed_alias_hold(s)
        assert s.query(SkuAlias).count() == 2 and s.query(SkuHold).count() == 2

        res = pl.import_products(session=s, force=True, path=catalog)
        s.commit()

        # 导入本身必须真的发生（否则"没清库"只是因为它压根没跑）
        assert res["status"] == "imported", res
        assert s.query(ProductCatalog).count() == 1, "商品资料未写入，动态证明不成立"
        # 核心断言：两张登记表纹丝不动
        assert s.query(SkuAlias).count() == 2, "缺子表的商品资料竟清空了 sku_alias！"
        assert s.query(SkuHold).count() == 2, "缺子表的商品资料竟清空了 sku_hold！"
        assert {a.old_sku for a in s.query(SkuAlias).all()} == {"G493", "G494"}
        assert {h.sku for h in s.query(SkuHold).all()} == {"G495", "G524"}


def test_01d_dynamic_with_real_catalog_keeps_db(sqlite_env):
    """用**真实**商品资料.xlsx（确实不含两张子表）再跑一遍同样的动态复现。"""
    if not REAL_CATALOG.is_file():
        pytest.skip(f"商品资料不存在：{REAL_CATALOG}")
    eng, Sess, tmp = sqlite_env
    with Sess() as s:
        _seed_alias_hold(s)
        res = pl.import_products(session=s, force=True, path=REAL_CATALOG)
        s.commit()
        assert res["status"] == "imported", res
        assert s.query(ProductCatalog).count() > 100, "真实商品资料应导入数百行"
        assert s.query(SkuAlias).count() == 2, "真实商品资料竟清空了 sku_alias！"
        assert s.query(SkuHold).count() == 2, "真实商品资料竟清空了 sku_hold！"
    # 佐证：这份真实商品资料里确实没有两张子表（事故现场）
    wb = load_workbook(REAL_CATALOG, read_only=True)
    try:
        assert sku_alias.ALIAS_SHEET not in wb.sheetnames
        assert sku_hold.HOLD_SHEET not in wb.sheetnames
    finally:
        wb.close()


def test_01e_import_registry_is_the_only_overwriter():
    """正向对照：全量覆盖的 DELETE 只应出现在 import_registry 里。"""
    src = inspect.getsource(pl.import_registry)
    assert "delete(SkuAlias)" in src and "delete(SkuHold)" in src


# ===========================================================================
# 检查 2 —— 护栏 4 分支（自己构造 4 种输入）
# ===========================================================================
def _seeded_session(sqlite_env):
    eng, Sess, tmp = sqlite_env
    s = Sess()
    _seed_alias_hold(s)
    return s, tmp


def test_02_branch1_missing_file(sqlite_env):
    """分支①：登记文件不存在 → 不 DELETE、warned、告警含期望路径。"""
    s, tmp = _seeded_session(sqlite_env)
    missing = tmp / "不存在的登记表.xlsx"
    res = pl.import_registry(session=s, force=True, path=missing)
    assert res["status"] == "warned"
    assert s.query(SkuAlias).count() == 2 and s.query(SkuHold).count() == 2
    assert any(str(missing) in n for n in res["notes"]), "告警未含期望路径"
    assert any("未执行任何 DELETE" in n for n in res["notes"])
    s.close()


def test_02_branch2_missing_subtable(sqlite_env):
    """分支②：子表缺失（有 SKU换号、缺 人工登记）→ 不 DELETE、warned。"""
    s, tmp = _seeded_session(sqlite_env)
    p = _make_registry(tmp / "only_alias.xlsx",
                       alias_rows=[("G999", "G998", "x", "")], with_hold=False)
    res = pl.import_registry(session=s, force=True, path=p)
    assert res["status"] == "warned"
    assert s.query(SkuAlias).count() == 2 and s.query(SkuHold).count() == 2
    assert any(sku_hold.HOLD_SHEET in n for n in res["notes"])
    s.close()


def test_02_branch3_header_not_matched(sqlite_env):
    """分支③：子表在但表头未命中（前 MAX_HDR_SCAN 行无表头）→ 不 DELETE、warned。"""
    s, tmp = _seeded_session(sqlite_env)
    p = tmp / "bad_hdr.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = sku_alias.ALIAS_SHEET
    for i in range(sku_alias.MAX_HDR_SCAN + 1):
        ws.append([f"说明{i}", "", "", ""])
    ws2 = wb.create_sheet(sku_hold.HOLD_SHEET)
    ws2.append(list(HDR_HOLD))
    ws2.append(["G495", "r", "2026-09-28"])
    wb.save(p)
    res = pl.import_registry(session=s, force=True, path=p)
    assert res["status"] == "warned"
    assert s.query(SkuAlias).count() == 2 and s.query(SkuHold).count() == 2
    assert any(sku_alias.ALIAS_SHEET in n for n in res["notes"])
    s.close()


def test_02_branch4_empty_rows_allowed_but_loud(sqlite_env):
    """分支④：子表 + 表头在位但数据行 0 条 → **允许** DELETE + 高声告警 + before/after 计数。"""
    s, tmp = _seeded_session(sqlite_env)
    p = _make_registry(tmp / "empty.xlsx", alias_rows=[], hold_rows=[])
    res = pl.import_registry(session=s, force=True, path=p)
    assert res["status"] == "imported", res
    assert s.query(SkuAlias).count() == 0 and s.query(SkuHold).count() == 0
    assert any("主动清空" in n for n in res["notes"])
    assert res["aliasBefore"] == 2 and res["aliasAfter"] == 0
    assert res["holdBefore"] == 2 and res["holdAfter"] == 0
    s.close()


def test_02_branch4_partial_only_one_empty(sqlite_env):
    """分支④变体：仅一张子表 0 行、另一张有数据 → 仍 imported，但清空那张要告警。"""
    s, tmp = _seeded_session(sqlite_env)
    p = _make_registry(tmp / "half_empty.xlsx",
                       alias_rows=[("G1", "G2", "x", "")], hold_rows=[])
    res = pl.import_registry(session=s, force=True, path=p)
    assert res["status"] == "imported"
    assert {a.old_sku for a in s.query(SkuAlias).all()} == {"G1"}
    assert s.query(SkuHold).count() == 0
    assert any(sku_hold.HOLD_SHEET in n and "主动清空" in n for n in res["notes"])
    assert not any(sku_alias.ALIAS_SHEET in n and "主动清空" in n for n in res["notes"])
    s.close()


# ===========================================================================
# 检查 3 —— 跨实现对齐（真实登记表）
# ===========================================================================
def test_03_cross_impl_alias_on_real_file(offline):
    if not REAL_REGISTRY.is_file():
        pytest.skip(f"登记表不存在：{REAL_REGISTRY}")
    be_rows, be_notes = sku_alias.read_alias_sheet(REAL_REGISTRY)
    off = offline.read_alias_map(REAL_REGISTRY)
    assert not be_notes, f"真实登记表读到告警：{be_notes}"
    assert len(be_rows) == 2, f"后端换号应 2 条，实得 {len(be_rows)}"
    assert len(off) == 2, f"离线换号应 2 条，实得 {len(off)}"
    be = {r["old"]: (r["new"], r["note"], r["effectiveAt"]) for r in be_rows}
    off_m = {k: (v["新号"], v["说明"], v["生效日期"]) for k, v in off.items()}
    assert be == off_m, f"逐键不一致：\n后端 {be}\n离线 {off_m}"
    assert set(be) == REGISTRY_ALIAS_KEYS


def test_03_cross_impl_hold_on_real_file(offline):
    if not REAL_REGISTRY.is_file():
        pytest.skip(f"登记表不存在：{REAL_REGISTRY}")
    be_rows, be_notes = sku_hold.read_hold_sheet(REAL_REGISTRY)
    off = offline.read_hold_map(REAL_REGISTRY)
    assert not be_notes, f"真实登记表读到告警：{be_notes}"
    assert len(be_rows) == 6, f"后端人工登记应 6 条，实得 {len(be_rows)}"
    assert len(off) == 6, f"离线人工登记应 6 条，实得 {len(off)}"
    be = {r["sku"]: (r["reason"], r["registeredAt"]) for r in be_rows}
    off_m = {k: (v["说明"], v["登记日期"]) for k, v in off.items()}
    assert be == off_m, f"逐键不一致：\n后端 {be}\n离线 {off_m}"
    assert set(be) == REGISTRY_HOLD_KEYS


# ===========================================================================
# 检查 4 —— 同源常量逐字相等
# ===========================================================================
def test_04_four_constants_identical(offline):
    be = sku_alias.REGISTRY_XLSX_DEFAULT
    assert be == r"C:\Users\dell\Desktop\work-main\资产文件\SKU登记表.xlsx"
    assert _default_rules()["registry_xlsx_path"] == be, "rule 默认键 ≠ 后端常量"
    assert str(offline.ALIAS_XLSX_DEFAULT) == be, "离线 ALIAS_XLSX_DEFAULT ≠ 后端"
    assert str(offline.HOLD_XLSX_DEFAULT) == be, "离线 HOLD_XLSX_DEFAULT ≠ 后端"


def test_04_legacy_and_catalog_not_drifted(offline):
    """LEGACY 回退源仍指商品资料；离线 CATALOG_XLSX 不动（仍=商品资料）。"""
    assert sku_alias.LEGACY_XLSX_DEFAULT == r"C:\Users\dell\Desktop\work-main\资产文件\商品资料.xlsx"
    assert str(offline.LEGACY_XLSX_DEFAULT) == sku_alias.LEGACY_XLSX_DEFAULT
    assert str(offline.CATALOG_XLSX).endswith("商品资料.xlsx"), str(offline.CATALOG_XLSX)
    # 新真源与旧回退源必须是**不同**文件（否则迁移无意义）
    assert sku_alias.REGISTRY_XLSX_DEFAULT != sku_alias.LEGACY_XLSX_DEFAULT


def test_04_resolve_registry_path_default(monkeypatch):
    monkeypatch.delenv("SKU_REGISTRY_XLSX", raising=False)
    assert str(sku_alias.resolve_registry_path()) == sku_alias.REGISTRY_XLSX_DEFAULT


def test_04_read_from_wb_signatures_still_wb_only():
    assert list(inspect.signature(sku_alias.read_alias_from_wb).parameters) == ["wb"]
    assert list(inspect.signature(sku_hold.read_hold_from_wb).parameters) == ["wb"]


# ===========================================================================
# 检查 5 —— 离线脚本可独立运行 + 缺文件时打印告警（非静默 {}）
# ===========================================================================
def test_05_offline_has_no_backend_imports():
    """独立 grep：离线脚本不得 import 后端包（app/backend/etl）。"""
    txt = OFFLINE_SCRIPT.read_text(encoding="utf-8")
    hits = re.findall(r"^\s*(?:import|from)\s+(app|backend|etl)\b", txt, re.M)
    assert not hits, f"离线脚本 import 了后端包：{hits}"
    # 更严格：不得把 backend 目录塞进 sys.path（那样也会变相依赖）
    assert "sys.path.insert" not in txt or "backend" not in txt.split("sys.path.insert")[1][:80], \
        "离线脚本疑似把 backend 加入 sys.path"


def test_05_missing_file_prints_warning_alias(tmp_path, offline, capsys):
    """文件缺失 → **确实打印**显眼告警（含路径），而非静默 {}。"""
    missing = tmp_path / "被移走的登记表.xlsx"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = offline.read_alias_map(missing)
    printed = buf.getvalue()
    assert out == {}, "缺文件应返回空映射"
    assert "⚠" in printed, f"缺文件未打印告警，stdout={printed!r}"
    assert str(missing) in printed, f"告警未含路径，stdout={printed!r}"


def test_05_missing_file_prints_warning_hold(tmp_path, offline):
    missing = tmp_path / "被移走的登记表.xlsx"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = offline.read_hold_map(missing)
    printed = buf.getvalue()
    assert out == {}
    assert "⚠" in printed and str(missing) in printed, f"stdout={printed!r}"


# ===========================================================================
# 检查 6 —— 原事故端到端复现
# ===========================================================================
def test_06_old_logic_reads_zero_from_catalog(offline):
    """旧逻辑（从**商品资料**读两张子表）→ 0 条（事故现场：子表已随替换消失）。"""
    if not REAL_CATALOG.is_file():
        pytest.skip(f"商品资料不存在：{REAL_CATALOG}")
    # 离线旧读法
    assert offline.read_alias_map(REAL_CATALOG) == {}, "商品资料里竟还能读到换号表"
    assert offline.read_hold_map(REAL_CATALOG) == {}, "商品资料里竟还能读到人工登记"
    # 后端旧读法（显式指向商品资料）
    a_rows, _ = sku_alias.read_alias_sheet(REAL_CATALOG)
    h_rows, _ = sku_hold.read_hold_sheet(REAL_CATALOG)
    assert a_rows == [] and h_rows == []


def test_06_new_logic_reads_two_and_six(offline):
    """新逻辑（独立登记表）→ 换号 2 条 / 人工登记 6 条。"""
    if not REAL_REGISTRY.is_file():
        pytest.skip(f"登记表不存在：{REAL_REGISTRY}")
    # 离线（默认无参 → 独立登记表）
    amap = offline.read_alias_map()
    hmap = offline.read_hold_map()
    assert set(amap) == REGISTRY_ALIAS_KEYS, set(amap)
    assert set(hmap) == REGISTRY_HOLD_KEYS, set(hmap)
    # 后端（默认无参 → 独立登记表；notes 为空说明**不是**走了商品资料回退）
    a_rows, a_notes = sku_alias.read_alias_sheet()
    h_rows, h_notes = sku_hold.read_hold_sheet()
    assert {r["old"] for r in a_rows} == REGISTRY_ALIAS_KEYS
    assert {r["sku"] for r in h_rows} == REGISTRY_HOLD_KEYS
    assert not a_notes and not h_notes, f"默认读不应有告警（说明走了回退）：{a_notes} {h_notes}"


def test_06_swapping_catalog_no_longer_affects_registry(offline, monkeypatch, tmp_path):
    """证明：换一个不含子表的商品资料，**不再影响**登记表读数。

    做法：把"默认商品资料路径"指向一个不含子表的临时文件；默认登记表读数仍 2/6。
    """
    if not REAL_REGISTRY.is_file():
        pytest.skip(f"登记表不存在：{REAL_REGISTRY}")
    fake_catalog = _make_catalog(tmp_path / "换掉的商品资料.xlsx")
    # 让回退源指向假商品资料（若代码真去读它，就会得到 0 条）
    monkeypatch.setattr(sku_alias, "resolve_legacy_path", lambda: Path(fake_catalog))
    monkeypatch.setattr(sku_hold, "resolve_legacy_path", lambda: Path(fake_catalog))
    monkeypatch.delenv("SKU_REGISTRY_XLSX", raising=False)
    a_rows, _ = sku_alias.read_alias_sheet()
    h_rows, _ = sku_hold.read_hold_sheet()
    assert len(a_rows) == 2 and len(h_rows) == 6


def _mini_sku_result(sku):
    """最小 sku_result：未闭环 + 空 parts + 完整 nodes 形状（催办分支需要 nodes 的键）。"""
    return {"overview": {"SKU": sku, "型号": f"M-{sku}", "复盘有效性": "未闭环"},
            "parts": {},
            "nodes": {"素材加工": {"adopted": None},
                      "上架": {"adopted": None},
                      "广告": {"adopted": None}}}


def test_06_eight_skus_not_misjudged_as_chase(offline, tmp_path):
    """8 个 SKU 走**离线判定入口** write_warn_md → 进「已换号/已豁免」，不进「催办」。"""
    if not REAL_REGISTRY.is_file():
        pytest.skip(f"登记表不存在：{REAL_REGISTRY}")
    skus = sorted(REGISTRY_ALIAS_KEYS | REGISTRY_HOLD_KEYS)
    results = [_mini_sku_result(s) for s in skus]
    amap = offline.read_alias_map()
    hmap = offline.read_hold_map()
    stats = offline.write_warn_md(results, str(tmp_path), tmp_path / "warn.md",
                                  aliases=amap, holds=hmap)
    md = (tmp_path / "warn.md").read_text(encoding="utf-8")
    # write_warn_md 返回 (催办条数, n0, n1, n2, void, alias, hold)
    assert stats[0] == 0, f"催办条数应为 0，实得 {stats[0]}"
    assert stats[5] == 2 and stats[6] == 6, f"alias/hold 计数不符：{stats}"

    # 催办表（第二节）里的 SKU
    sec2 = md.split("## 二、加急明细")[1].split("## 三、")[0]
    chase = set(re.findall(r"^\|\s*P\d\s*\|\s*([^|\s]+)\s*\|", sec2, re.M))
    assert not (chase & set(skus)), f"登记过的 SKU 竟进了催办：{sorted(chase & set(skus))}"

    # 第四节（已换号）应有 G493/G494；第五节（已豁免）应有 6 个 hold SKU
    sec4 = md.split("## 四、已换号")[1].split("## 五、")[0]
    sec5 = md.split("## 五、已豁免")[1]
    assert REGISTRY_ALIAS_KEYS <= set(re.findall(r"G\d+", sec4)), sec4[:400]
    assert REGISTRY_HOLD_KEYS <= set(re.findall(r"G\d+", sec5)), sec5[:400]


def test_06_contrast_would_be_chased_without_registry(offline, tmp_path):
    """反向对照：若登记表为空映射，同样输入会落进「催办」（证明上面确实是靠登记表救的）。"""
    skus = sorted(REGISTRY_ALIAS_KEYS | REGISTRY_HOLD_KEYS)
    results = [_mini_sku_result(s) for s in skus]
    stats = offline.write_warn_md(results, str(tmp_path), tmp_path / "warn2.md",
                                  aliases={}, holds={})
    md = (tmp_path / "warn2.md").read_text(encoding="utf-8")
    assert stats[0] == 8, f"空登记表时应 8 条全进催办，实得 {stats[0]}"
    sec2 = md.split("## 二、加急明细")[1].split("## 三、")[0]
    chase = set(re.findall(r"^\|\s*P\d\s*\|\s*([^|\s]+)\s*\|", sec2, re.M))
    assert set(skus) <= chase, f"空登记表时这些 SKU 本应进催办：{sorted(set(skus) - chase)}"


# ===========================================================================
# 检查 7 —— 边角攻击
# ===========================================================================
def test_07a_registry_renamed_or_moved(tmp_path):
    """登记表被改名/移走 → 后端读到空并给出含路径告警，绝不抛。"""
    moved = tmp_path / "SKU登记表_改名了.xlsx"
    rows, notes = sku_alias.read_alias_sheet(moved)
    assert rows == [] and notes and "不存在" in notes[0] and str(moved) in notes[0]
    rows2, notes2 = sku_hold.read_hold_sheet(moved)
    assert rows2 == [] and notes2 and "不存在" in notes2[0]


def test_07b_subtable_name_typo(tmp_path):
    """子表名写错（SKU換號 繁体）→ 判定为 no_sheet；读写都给出告警。"""
    p = tmp_path / "typo.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "SKU換號"                 # 繁体，故意写错
    ws.append(list(HDR_ALIAS))
    ws.append(["G493", "G510", "x", ""])
    wb.create_sheet(sku_hold.HOLD_SHEET).append(list(HDR_HOLD))
    wb.save(p)
    wb2 = load_workbook(p, read_only=True)
    try:
        _, _, state = sku_alias.scan_alias_wb(wb2)
    finally:
        wb2.close()
    assert state == "no_sheet", state
    rows, notes = sku_alias.read_alias_sheet(p)
    assert rows == [] and notes and "不存在" in notes[0]


def test_07c_header_on_third_row(tmp_path):
    """表头写在第 3 行（在 MAX_HDR_SCAN=6 内）→ 两张子表仍能命中。"""
    p = _make_registry(
        tmp_path / "hdr_row3.xlsx",
        alias_rows=[("G1", "G2", "n", "2026-01-01")],
        hold_rows=[("G3", "r", "2026-01-01")],
        alias_prelude=[("换号对照说明", None, None, None), (None, None, None, None)],
        hold_prelude=[("人工登记说明", None, None)],
    )
    a_rows, a_notes = sku_alias.read_alias_sheet(p)
    h_rows, h_notes = sku_hold.read_hold_sheet(p)
    assert a_rows and a_rows[0]["old"] == "G1" and a_rows[0]["new"] == "G2", a_rows
    assert h_rows and h_rows[0]["sku"] == "G3", h_rows
    assert not a_notes and not h_notes


def test_07d_hold_title_row_guard_still_effective(tmp_path):
    """`人工登记` 前插一行标题行（单格、含『SKU』字样）→ 标题行防护生效、**不吞表**。"""
    title = "以下 SKU 不需走完流程"                 # 单格标题，含『SKU』
    p = _make_registry(
        tmp_path / "hold_title.xlsx",
        alias_rows=[("G1", "G2", "n", "")],
        hold_rows=[("G495", "r1", "2026-09-28"), ("G524", "r2", "2026-09-28")],
        hold_prelude=[(title, None, None)],
    )
    h_rows, h_notes = sku_hold.read_hold_sheet(p)
    assert {r["sku"] for r in h_rows} == {"G495", "G524"}, \
        f"标题行吞了/污染了数据：{h_rows}"
    assert not h_notes


def test_07e_fullwidth_space_and_value_case_tolerance(tmp_path):
    """表头**全角空格**耐受 + 数据值**大小写**归一（实测成立）。"""
    p = _make_registry(
        tmp_path / "fw.xlsx",
        alias_rows=[(" g493\u3000", " g510 ", "n", "2025-11-19")],
        hold_rows=[("g495\u3000", "r", "2026-09-28")],
        alias_header=("旧 SKU\u3000", " 新SKU ", "换号 说明", "生效 日期"),
    )
    a_rows, _ = sku_alias.read_alias_sheet(p)
    assert a_rows and a_rows[0]["old"] == "G493" and a_rows[0]["new"] == "G510", a_rows
    h_rows, _ = sku_hold.read_hold_sheet(p)
    assert h_rows and h_rows[0]["sku"] == "G495", h_rows


def test_07f_header_case_is_folded_after_fix(tmp_path):
    """【发现 A·2026-09-30 已修】表头**大小写折叠**已落实：`旧sku`/`新sku` 现在能命中。

    修复前 `_pick`/`_alias_pick` 只去空格、不 fold 大小写 → 小写表头 no_header（本用例初版
    固化了该行为，并在断言信息里注明「若被识别说明已支持大小写，可更新本文档」）。
    现两侧（后端 `sku_alias._pick` / 离线 `route_check_sys._alias_pick`）**同源**折叠大写。
    """
    p = _make_registry(tmp_path / "case.xlsx",
                       alias_rows=[("G493", "G510", "n", "")],
                       alias_header=("旧sku", "新sku", "换号说明", "生效日期"))
    a_rows, a_notes = sku_alias.read_alias_sheet(p)
    assert not a_notes, f"小写表头现应命中且无告警：{a_notes}"
    assert [(r["old"], r["new"]) for r in a_rows] == [("G493", "G510")], a_rows
    p2 = _make_registry(tmp_path / "case2.xlsx",
                        hold_rows=[("G495", "r", "")],
                        with_alias=False, hold_header=("sku", "说明", "登记日期"))
    h_rows, h_notes = sku_hold.read_hold_sheet(p2)
    assert not h_notes, f"小写表头现应命中且无告警：{h_notes}"
    assert [r["sku"] for r in h_rows] == ["G495"], h_rows


def test_07g_two_cell_short_title_row_can_spawn_spurious_row(tmp_path):
    """【发现·低危】`人工登记` 若写**两格**短标题行（首格含 SKU/编码、次格含 说明）会被当表头。

    后果：真实表头行被当数据 → 多出一条 `SKU="SKU"` 的**伪行**。
    但**数据未丢**（G495 仍在）、**不静默吞表**、且后端护栏会警告其格式可疑 → 非致命。
    """
    p = _make_registry(tmp_path / "two_cell_title.xlsx",
                       alias_rows=[("G1", "G2", "n", "")],
                       hold_rows=[("G495", "r", "2026-09-28")],
                       hold_prelude=[("SKU登记表", "说明")])
    h_rows, _ = sku_hold.read_hold_sheet(p)
    skus = {r["sku"] for r in h_rows}
    assert "G495" in skus, "数据被吞了（比预期更糟）"
    # 固化现状：存在伪行 "SKU"
    assert "SKU" in skus, "若此处失败说明标题行防护已加强，可移除本风险项"


def test_07h_alias_header_row3_and_hold_no_sheet_guard(sqlite_env):
    """边角合击：换号表头在第 3 行（仍 ok）+ 人工登记子表缺失 → import_registry 仍 warned 不清库。"""
    s, tmp = _seeded_session(sqlite_env)
    p = _make_registry(tmp / "mix.xlsx",
                       alias_rows=[("G1", "G2", "n", "")],
                       alias_prelude=[("说明", None, None, None), (None, None, None, None)],
                       with_hold=False)
    res = pl.import_registry(session=s, force=True, path=p)
    assert res["status"] == "warned"
    assert s.query(SkuAlias).count() == 2 and s.query(SkuHold).count() == 2
    s.close()
