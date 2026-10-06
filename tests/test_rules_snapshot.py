# -*- coding: utf-8 -*-
"""test_rules_snapshot.py — T17「离线同源件跟随规则页覆盖值」守卫。

背景（缺陷）：离线同源件 `流程复盘/route_check_sys.py` 是**纯离线、不连数据库**，
项目约定「离线脚本不得 import 后端包」→ 它读不到规则页 `rule_config.seg_targets` /
`loss_route_limit` 的覆盖值，于是「采购→发货末批」等口径在在线页与离线件间会出现
**同一口径两个数**（T16 只把文件内字面量收敠为 L4[3]，离线件仍恒用代码默认）。

本次改造：新增**单向只读通道** —— 后端保存规则 / 每次启动时把**白名单键**原子落盘成
`资产文件\\规则快照.json`；离线件启动时读它并覆盖自己的 `SEGS` / `L4` / `ASSESS_LIMIT`。
文件缺失/损坏 → 全部回退代码默认值并**显式告警**（绝不静默）。

覆盖（**不使用生产 MySQL / 真实数据文件的绝对数写断言**）：
  1. 同源常量：后端 `RULES_SNAPSHOT_JSON_DEFAULT` == 离线件同名常量（逐字）；
  2. 离线件不 import 后端包（读源码文本断言）；
  3. 默认零漂移：离线 `SEGS` 目标天 == `default_seg_targets()` 同名段；`L4[3]`==22；
     `ASSESS_LIMIT` == `_default_rules()["loss_route_limit"]`（60）；
  4. 覆盖生效：临时快照给「采购→发货末批」30 → `NOT_SHIPPED_GRACE_DAYS`/`L4[3]`==30；
     给某段 30 → 该段目标天==30（未覆盖段保留默认）；
  5. null 语义对齐 T16：`judge_seg` 不崩、`not_shipped_judge` grace=None 走中性档；
  6. 坏/缺快照不崩：路径不存在 / 坏 JSON → `({}, 告警非空)`；
  7. 后端导出：原子写、白名单（不含 mail_ 凭据）、不可写路径 `ok=False` 不抛；
  8. `save_rules` 触发落盘、`snapshot_status` 读状态。
"""

import contextlib
import importlib.util
import io
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

import app.rule_service as RS
from app.seg_rules import default_seg_targets

# 离线同源件在**外层仓库**（与本项目仓库同级）：<work-main>\\流程复盘\\route_check_sys.py
REPO_ROOT = Path(__file__).resolve().parents[1]          # ...\\数据后台系统
FUKPAN_DIR = REPO_ROOT.parent / "流程复盘"               # ...\\流程复盘
SYS_PY = FUKPAN_DIR / "route_check_sys.py"

pytestmark = pytest.mark.skipif(
    not SYS_PY.exists(),
    reason="离线同源件 流程复盘/route_check_sys.py 不在本机（属外层仓库），跳过这组用例")

_counter = {"n": 0}


class _CaptureStdout(io.StringIO):
    """捕获模块级打印；同时兼容 route_check.py 的 `sys.stdout.reconfigure(...)`。"""

    def reconfigure(self, *args, **kwargs):
        return None


def load_sys_module(snapshot_path):
    """把离线件作为独立模块 exec（会执行模块级快照读取）。

    返回 `(模块, 模块级 stdout 文本)`。通过环境变量 `RULES_SNAPSHOT_JSON` 指定快照路径，
    因此可精确控制「无快照 / 有覆盖 / 坏快照」三种输入。
    """
    if str(FUKPAN_DIR) not in sys.path:
        sys.path.insert(0, str(FUKPAN_DIR))
    _counter["n"] += 1
    name = "route_check_sys_under_test_%d" % _counter["n"]
    spec = importlib.util.spec_from_file_location(name, SYS_PY)
    mod = importlib.util.module_from_spec(spec)
    buf = _CaptureStdout()
    old = os.environ.get("RULES_SNAPSHOT_JSON")
    os.environ["RULES_SNAPSHOT_JSON"] = str(snapshot_path)
    try:
        with contextlib.redirect_stdout(buf):
            spec.loader.exec_module(mod)
    finally:
        if old is None:
            os.environ.pop("RULES_SNAPSHOT_JSON", None)
        else:
            os.environ["RULES_SNAPSHOT_JSON"] = old
    return mod, buf.getvalue()


def _write_snap(path, rules):
    path.write_text(
        json.dumps({"schema": 1, "generated_at": "2026-09-30 09:00:00",
                    "rules_version": 9, "source": "test",
                    "keys": ["seg_targets", "loss_route_limit"], "rules": rules},
                   ensure_ascii=False),
        encoding="utf-8")
    return path


NONEXISTENT = Path("__route_check_sys_no_such_snapshot__.json")


# ---------------------------------------------------------------------------
# 1. 同源常量（逐字一致）
# ---------------------------------------------------------------------------
class TestSameSourceConstant:
    def test_default_path_matches_backend(self):
        """后端 DEFAULT 与离线件 DEFAULT 必须逐字相同（一侧 str、一侧 Path）。"""
        mod, _ = load_sys_module(NONEXISTENT)
        assert RS.RULES_SNAPSHOT_JSON_DEFAULT == str(mod.RULES_SNAPSHOT_JSON_DEFAULT)

    def test_env_var_name_is_documented(self):
        """离线件确实从 RULES_SNAPSHOT_JSON 读路径（默认路径之外的可注入点）。"""
        text = SYS_PY.read_text(encoding="utf-8")
        assert "RULES_SNAPSHOT_JSON" in text


# ---------------------------------------------------------------------------
# 2. 离线件不 import 后端包（项目硬约定）
# ---------------------------------------------------------------------------
class TestOfflineNoBackendImport:
    def test_no_backend_package_import(self):
        text = SYS_PY.read_text(encoding="utf-8")
        bad = re.findall(r'^\s*(?:from|import)\s+app(?:\.|\s|$)', text, re.M)
        assert not bad, "离线件不得 import 后端 app 包，实得: %r" % bad

    def test_no_relative_backend_import(self):
        text = SYS_PY.read_text(encoding="utf-8")
        assert "from backend" not in text
        assert "import backend" not in text


# ---------------------------------------------------------------------------
# 3. 默认零漂移（无快照 / 快照值==默认 → 逐段等于代码默认）
# ---------------------------------------------------------------------------
class TestDefaultZeroDrift:
    def test_segs_match_backend_defaults(self):
        mod, out = load_sys_module(NONEXISTENT)
        defaults = default_seg_targets()
        for dept, name, _n0, _n1, target, _owner, _note, _a0, _a1 in mod.SEGS:
            if dept == "__GAP__":                    # 留空段不进配置
                continue
            assert target == defaults[name]["target"], name
        assert "未找到" in out, "快照缺失必须显式告警，不许静默"

    def test_l4_and_assess_default(self):
        mod, _ = load_sys_module(NONEXISTENT)
        defaults = default_seg_targets()
        assert mod.L4[1] == "采购→发货末批"
        assert mod.L4[3] == defaults["采购→发货末批"]["target"] == 22
        assert mod.NOT_SHIPPED_GRACE_DAYS == 22
        assert mod.ASSESS_LIMIT == RS._default_rules()["loss_route_limit"] == 60


# ---------------------------------------------------------------------------
# 4. 覆盖生效
# ---------------------------------------------------------------------------
class TestOverrideApplied:
    def test_purchase_grace_override(self, tmp_path):
        snap = _write_snap(tmp_path / "snap.json",
                           {"seg_targets": {"采购→发货末批": {"target": 30}},
                            "loss_route_limit": 61})
        mod, out = load_sys_module(snap)
        assert mod.L4[3] == 30
        assert mod.NOT_SHIPPED_GRACE_DAYS == 30         # 天然跟随 L4
        assert mod.ASSESS_LIMIT == 61
        assert "已应用" in out

    def test_seg_target_override_keeps_others_default(self, tmp_path):
        snap = _write_snap(tmp_path / "snap.json",
                           {"seg_targets": {"提案→取数": {"target": 30}}})
        mod, _ = load_sys_module(snap)
        seg = {row[1]: row[4] for row in mod.SEGS}
        assert seg["提案→取数"] == 30
        assert seg["取数→签样"] == 12                   # 未覆盖段保留代码默认

    def test_gap_segment_not_overridden(self, tmp_path):
        """__GAP__ 留空段不参与覆盖（即便快照里塞了同名键）。"""
        snap = _write_snap(tmp_path / "snap.json",
                           {"seg_targets": {"[产品部留空·后勤接管段]": {"target": 99}}})
        mod, _ = load_sys_module(snap)
        gap = [row for row in mod.SEGS if row[0] == "__GAP__"]
        assert gap and gap[0][4] == 0

    def test_apply_is_pure(self):
        """_apply_rule_snapshot 不改入参（纯函数）。"""
        mod, _ = load_sys_module(NONEXISTENT)
        segs0 = list(mod.SEGS)
        l40 = mod.L4
        _segs, _l4, _a, _app, _bad = mod._apply_rule_snapshot(
            mod.SEGS, mod.L4, 60, {"seg_targets": {"提案→取数": {"target": 1}}})
        assert mod.SEGS == segs0 and mod.L4 == l40

    def test_non_numeric_targets_are_ignored_with_warning(self, tmp_path):
        """手改快照写成字符串 target（"30"）→ 忽略覆盖、回退默认、warnings 非空、不抛。"""
        snap = _write_snap(tmp_path / "snap.json",
                           {"seg_targets": {"采购→发货末批": {"target": "30"},
                                            "提案→取数": {"target": "40"}},
                            "loss_route_limit": "60"})
        mod, out = load_sys_module(snap)             # 模块加载不得抛
        assert mod.L4[3] == 22                       # 回退代码默认值
        assert mod.NOT_SHIPPED_GRACE_DAYS == 22
        assert mod.ASSESS_LIMIT == 60
        seg = {row[1]: row[4] for row in mod.SEGS}
        assert seg["提案→取数"] == 75                 # 回退默认
        assert mod._snap_warn and any("非数值" in w for w in mod._snap_warn)
        # 判定层不再因 str 目标报 TypeError
        assert mod.judge_seg(3, mod.L4[3]) == "✓"

    def test_bool_target_is_rejected(self, tmp_path):
        """bool 是 int 子类 → 必须当作非数值拒绝（True 不得被当成 1）。"""
        snap = _write_snap(tmp_path / "snap.json",
                           {"seg_targets": {"采购→发货末批": {"target": True}}})
        mod, _ = load_sys_module(snap)
        assert mod.L4[3] == 22
        assert any("非数值" in w for w in mod._snap_warn)


# ---------------------------------------------------------------------------
# 5. null 语义对齐 T16
# ---------------------------------------------------------------------------
class TestNullSemantics:
    def test_judge_seg_target_none(self):
        mod, _ = load_sys_module(NONEXISTENT)
        assert mod.judge_seg(5, None) == "—未设目标"
        assert mod.judge_seg(None, None) == "—缺时间"
        assert mod.judge_seg(-3, None).startswith("⚠反向")   # 反向判定在 target 之前
        assert mod.judge_seg(3, 7) == "✓"                    # 有目标不受影响
        assert mod.judge_seg(9, 7).startswith("✗超")

    def test_not_shipped_grace_none_is_neutral_and_no_crash(self):
        mod, _ = load_sys_module(NONEXISTENT)
        mod.NOT_SHIPPED_GRACE_DAYS = None
        judge, n, why = mod.not_shipped_judge(datetime(2026, 1, 1))
        assert judge == "未发货" and "✗" not in judge and "⚠" not in judge
        assert isinstance(n, int)
        assert mod.not_shipped_judge(None)[0] == "采购未下单"


# ---------------------------------------------------------------------------
# 6. 坏 / 缺快照不崩
# ---------------------------------------------------------------------------
class TestBadSnapshot:
    def test_missing_path_returns_warning(self, tmp_path):
        mod, _ = load_sys_module(NONEXISTENT)
        rules, warns = mod.load_rule_snapshot(tmp_path / "nope.json")
        assert rules == {} and warns

    def test_bad_json_returns_warning(self, tmp_path):
        mod, _ = load_sys_module(NONEXISTENT)
        p = tmp_path / "bad.json"
        p.write_text("not json", encoding="utf-8")
        rules, warns = mod.load_rule_snapshot(p)
        assert rules == {} and warns

    def test_missing_rules_key_returns_warning(self, tmp_path):
        mod, _ = load_sys_module(NONEXISTENT)
        p = tmp_path / "norules.json"
        p.write_text(json.dumps({"schema": 1}), encoding="utf-8")
        rules, warns = mod.load_rule_snapshot(p)
        assert rules == {} and warns

    def test_schema_mismatch_warns_but_uses_content(self, tmp_path):
        mod, _ = load_sys_module(NONEXISTENT)
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"schema": 2, "rules": {"seg_targets": {}}}),
                     encoding="utf-8")
        rules, warns = mod.load_rule_snapshot(p)
        assert rules == {"seg_targets": {}}
        assert any("schema" in w for w in warns)


# ---------------------------------------------------------------------------
# 7. 后端导出：原子写 + 白名单 + 失败吞掉
# ---------------------------------------------------------------------------
class TestBackendExport:
    def test_export_writes_whitelist_only(self, tmp_path):
        out = tmp_path / "snap.json"
        rules = {"seg_targets": {"采购→发货末批": {"target": 30}},
                 "loss_route_limit": 61,
                 "mail_auth_code": "TOP-SECRET-CODE",
                 "mail_user": "someone@example.com"}
        res = RS.export_rules_snapshot(rules=rules, version=7, path=out)
        assert res["ok"] is True and res["rulesVersion"] == 7
        text = out.read_text(encoding="utf-8")
        doc = json.loads(text)
        assert doc["rules"]["seg_targets"] == rules["seg_targets"]
        assert doc["rules"]["loss_route_limit"] == 61
        # ⚠️ 白名单：绝不 dump 整个 rules（含 mail_ 凭据明文）
        assert "mail_" not in text
        assert "TOP-SECRET-CODE" not in text
        assert "someone@example.com" not in text

    def test_build_snapshot_keys(self):
        # ⚠️ 入参刻意掺入**非白名单**键（尤其是明文凭据）：若 build_snapshot 退化为
        # 「整包 dump rules」，下方两条断言会立刻变红——这是本用例存在的唯一意义。
        rules = {
            "seg_targets": {},
            "loss_route_limit": 60,
            "mail_auth_code": "SECRET-CODE",
            "mail_user": "a@b.c",
            "future_unknown_key": 123,
        }
        doc = RS.build_snapshot(rules, 3)
        assert doc["schema"] == 1 and doc["rules_version"] == 3
        assert doc["keys"] == list(RS.SNAPSHOT_KEYS)
        assert set(doc["rules"].keys()) == set(RS.SNAPSHOT_KEYS)
        # 非白名单键（含凭据）绝不出现在输出里
        assert "mail_auth_code" not in doc["rules"]
        assert "mail_user" not in doc["rules"]
        assert "future_unknown_key" not in doc["rules"]
        assert "SECRET-CODE" not in json.dumps(doc, ensure_ascii=False)

    def test_export_failure_is_swallowed(self, tmp_path):
        """写到不可写路径 → ok=False 且**不抛异常**。"""
        # (a) 确定性：父路径是一个文件 → mkdir 必失败
        afile = tmp_path / "afile"
        afile.write_text("x", encoding="utf-8")
        res = RS.export_rules_snapshot(rules={"seg_targets": {}}, version=1,
                                       path=afile / "x.json")
        assert res["ok"] is False and "error" in res
        # (b) Windows 非法/不存在盘符路径 → 同样只返回 ok=False，不抛
        res2 = RS.export_rules_snapshot(rules={"seg_targets": {}}, version=1,
                                        path=Path("Z:/nope/x.json"))
        assert res2["ok"] is False and "error" in res2

    def test_export_creates_parent_dir(self, tmp_path):
        out = tmp_path / "deep" / "nested" / "snap.json"
        res = RS.export_rules_snapshot(rules={"seg_targets": {}, "loss_route_limit": 60},
                                       version=2, path=out)
        assert res["ok"] is True and out.exists()

    def test_snapshot_status_roundtrip(self, tmp_path, monkeypatch):
        out = tmp_path / "snap.json"
        RS.export_rules_snapshot(rules={"seg_targets": {}, "loss_route_limit": 60},
                                 version=4, path=out)
        monkeypatch.setenv("RULES_SNAPSHOT_JSON", str(out))
        st = RS.snapshot_status()
        assert st["exists"] is True and st["rulesVersion"] == 4 and st["generatedAt"]
        monkeypatch.setenv("RULES_SNAPSHOT_JSON", str(tmp_path / "missing.json"))
        st2 = RS.snapshot_status()
        assert st2["exists"] is False


# ---------------------------------------------------------------------------
# 8. save_rules 触发落盘（不碰生产库）
# ---------------------------------------------------------------------------
class TestSaveRulesTriggersSnapshot:
    def test_save_rules_writes_snapshot(self, monkeypatch, tmp_path):
        out = tmp_path / "snap.json"
        monkeypatch.setenv("RULES_SNAPSHOT_JSON", str(out))

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a, **k):
                return None

            def commit(self):
                return None

        monkeypatch.setattr(RS, "SessionLocal", lambda: FakeSession())
        monkeypatch.setattr(RS, "_state",
                            {"rules": RS._default_rules(), "version": 3})

        ver = RS.save_rules({"loss_route_limit": 61})
        assert ver == 4
        assert out.exists()
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert doc["rules_version"] == 4
        assert doc["rules"]["loss_route_limit"] == 61

    def test_export_call_is_outside_lock(self):
        """快照落盘必须发生在 `with _lock:` 块**之外**（持锁不做文件 IO）。"""
        src = Path(RS.__file__).read_text(encoding="utf-8")
        i = src.index("def save_rules(")
        j = src.index("def seg_target(", i)
        body = src[i:j]
        for line in body.splitlines():
            if line.strip() == "export_rules_snapshot(merged, new_version)":
                indent = len(line) - len(line.lstrip())
                # `with _lock:` / `with SessionLocal() as session:` 均在 4 空格缩进；
                # 调用点同为 4 空格 → 在锁块之外。
                assert indent == 4, "快照落盘调用必须在 with _lock: 之外"
                break
        else:
            pytest.fail("save_rules 未调用 export_rules_snapshot(merged, new_version)")
