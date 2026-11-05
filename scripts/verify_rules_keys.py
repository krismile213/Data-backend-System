# -*- coding: utf-8 -*-
"""verify_rules_keys.py — T01b 验收：新规则键可读、可校验、可保存。

⚠️ PUT /api/rules 会真的写库。本脚本先备份 rule_config 原值，测完**原样还原**，
   以免把用户已调过的规则覆盖成默认值。
断言：
  GET  /api/rules            -> 新键都在 rules 里，且 meta 有中文说明；
                                只读调度信息 pipeline 带出实际时刻（计划任务 XML 优先，
                                ps1 兜底；两处都读不到则为空）
  PUT 非法值（比例 1.5 / 已删除的死键 / 未知键）-> 422
  PUT 合法值（与当前值相同）-> 200
结果写 scripts/_verify_rules.txt（UTF-8）。
"""
import os
import sys

os.environ.setdefault("PREWARM", "0")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_verify_rules.txt")
lines, fails = [], []


def w(s=""):
    lines.append(str(s))


NEW_KEYS = ["inflight_warn_ratio", "report_retention_days",
            "products_xlsx_path", "registry_xlsx_path", "loss_route_limit",
            "loss_daily_default", "mail_enabled", "mail_to", "mail_host", "mail_port",
            "mail_user", "mail_auth_code"]

# 2026-09-24 删除的键：与真实调度脱钩、无任何消费方 → 必须**不再被接受**
DEAD_KEYS = ["daily_pipeline_slots"]

try:
    import json
    import re
    import traceback

    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.main import app

    # ---- 备份 rule_config ----
    with SessionLocal() as s:
        row = s.execute(text("SELECT value FROM rule_config WHERE name='main'")).first()
        backup = row[0] if row else None
    w(f"rule_config 备份：{'有' if backup else '(无，使用内置默认值)'} "
      f"长度={len(str(backup)) if backup else 0}")

    def restore():
        with SessionLocal() as s:
            if backup is None:
                s.execute(text("DELETE FROM rule_config WHERE name='main'"))
            else:
                s.execute(text("UPDATE rule_config SET value=:v WHERE name='main'"),
                          {"v": backup if isinstance(backup, str)
                           else json.dumps(backup, ensure_ascii=False)})
            s.commit()

    try:
        with TestClient(app) as c:
            r = c.get("/api/rules")
            w(f"\nGET /api/rules -> {r.status_code}")
            if r.status_code != 200:
                fails.append(f"GET rules {r.status_code}")
            else:
                j = r.json()
                rules, meta = j.get("rules", {}), j.get("meta", {})
                missing = [k for k in NEW_KEYS if k not in rules]
                w(f"  rules 缺失新键: {missing or '无'}")
                if missing:
                    fails.append(f"rules 缺 {missing}")
                nmiss = [k for k in NEW_KEYS if k not in meta]
                w(f"  meta 缺失说明: {nmiss or '无'}")
                if nmiss:
                    fails.append(f"meta 缺 {nmiss}")
                w("  新键当前值：")
                for k in NEW_KEYS:
                    w(f"    {k:<24} = {rules.get(k)!r:<28} "
                      f"{meta.get(k, {}).get('label', '')}")
                if j.get("version"):
                    w(f"  version = {j['version']}")

                # ---- 只读调度信息（真源 = 计划任务 XML，ps1 兜底）----
                pipe = j.get("pipeline") or {}
                w(f"  pipeline(只读) = {pipe}")
                if not pipe.get("ok"):
                    # 不是致命错误（换机没装任务时正常），但要显式记一笔
                    w("  ⚠ pipeline 未读到触发时刻（计划任务与 ps1 都不可用）")
                else:
                    if not pipe.get("times"):
                        fails.append("pipeline.times 为空")
                    for t in pipe["times"]:
                        if not re.match(r"^[0-2]\d:[0-5]\d$", t):
                            fails.append(f"pipeline 时刻格式异常 {t}")
                    # 脚本默认值 vs 实际任务不一致时提示（不是失败：任务才是真值）
                    st = pipe.get("scriptTimes") or []
                    if st and st != pipe["times"]:
                        w(f"  ⚠ 脚本默认值 {st} 与实际任务 {pipe['times']} 不一致（以任务为准）")
                # 只读字段不得出现在可编辑 rules 里
                still = [k for k in DEAD_KEYS if k in rules]
                w(f"  rules 里残留的已删键: {still or '无'}")
                if still:
                    fails.append(f"已删键仍在 rules：{still}")

            # ---- 非法值应被拒 ----
            bad_cases = [
                ({"inflight_warn_ratio": 1.5}, "比例 >1"),
                ({"inflight_warn_ratio": True}, "布尔混入数值"),
                ({"loss_daily_default": {"min": 9, "max": 1}}, "下限>上限"),
                ({"mail_enabled": "yes"}, "非布尔"),
                ({"unknown_key_xyz": 1}, "未知键"),
                ({"mail_port": 99999}, "端口越界"),
            ]
            # 已删除的死键必须被拒（否则「删了还能存」= 没删干净）
            for k in DEAD_KEYS:
                bad_cases.append(({k: ["09:20", "14:50"]}, f"已删除的死键 {k}"))
            w("\nPUT 非法值（期望 422）：")
            for body, why in bad_cases:
                rr = c.put("/api/rules", json={"patch": body})
                ok = rr.status_code == 422
                w(f"  [{'OK ' if ok else 'FAIL'}] {rr.status_code}  {why}")
                if not ok:
                    fails.append(f"非法值未被拒：{why}")

            # ---- 合法值（与当前值相同）应被接受 ----
            cur = j.get("rules", {})
            same = {k: cur[k] for k in NEW_KEYS if k in cur}
            rr = c.put("/api/rules", json={"patch": same})
            w(f"\nPUT 合法值（回写当前值）-> {rr.status_code}  status={rr.status_code == 200}")
            if rr.status_code != 200:
                fails.append(f"合法值被拒 {rr.status_code}: {rr.text[:200]}")
            else:
                got = rr.json().get("rules", {})
                diff = {k: (same[k], got.get(k)) for k in same
                        if json.dumps(same[k], sort_keys=True)
                        != json.dumps(got.get(k), sort_keys=True)}
                w(f"  回写后不一致的键: {diff or '无'}")
                if diff:
                    fails.append(f"回写不一致 {diff}")
    finally:
        restore()
        with SessionLocal() as s:
            row2 = s.execute(text("SELECT value FROM rule_config WHERE name='main'")).first()
        w(f"\nrule_config 已还原：{'是' if (row2[0] if row2 else None) == backup else '⚠ 否'}")

    w("\n" + "=" * 70)
    w("RESULT: " + ("PASS" if not fails else "FAIL -> " + "; ".join(fails)))
except Exception:
    w("EXCEPTION:")
    w(traceback.format_exc())
    fails.append("exception")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("written", OUT, "fails=", len(fails))
sys.exit(1 if fails else 0)
