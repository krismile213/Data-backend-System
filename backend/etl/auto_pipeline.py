# -*- coding: utf-8 -*-
"""auto_pipeline.py — 自动管线：增量导入 → 快照 → 事中日报 → 邮件（T05 §6.1）

一次 run_pipeline() = 「钉钉导出到位 → 看板就绪 → 日报落盘 → 邮件出去」的完整闭环：

    1. ensure_schema + 按文件指纹增量导入全部品类批次（未变化自动跳过）
    2. 每个有数据的品类：复盘重算（触发 KPI 快照）→ 在研快照 → 单据增量基线
       （T10，未闭环 SKU）→ 当日事中日报 xlsx
    3. 按保留期清理过期日报
    4. mail_enabled 且未 skip_mail 时发送邮件（一天一封，去重）

可靠性约定：
- **失败不静默**：任何一步失败都写 state/auto_pipeline.log（FAIL 行）+ 返回
  ok=False，计划任务与手动触发都能看到真实结果。
- **撞锁不放弃**：日报文件被 Excel/WPS 占用时等 60s 重试，最多 5 次
  （`REPORT_LOCK_RETRIES` / `REPORT_LOCK_WAIT_SEC` 可覆盖），仍失败才 FAIL。
- **幂等**：指纹增量导入 / 快照按指纹+日期去重 / 日报同日覆盖 / 邮件按日去重。
- CLI：`python backend/etl/auto_pipeline.py [--skip-mail] [--category 手机膜]`
  计划任务 DataAdmin-AutoPipeline 每天 09:00 / 14:10 调它（2026-10-19 核对）。
"""

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

# etl 包与 app 包平级（backend/ 下），本文件被直接执行时补 sys.path
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import DATA_ROOTS, STATE_DIR, list_batches  # noqa: E402

LOG_FILE = STATE_DIR / "auto_pipeline.log"
LAST_RUN = STATE_DIR / "pipeline_last_run.json"

# 日报文件被 Excel/WPS 占用不是偶发，而是常态（用户白天一直开着日报看）。
# 撞锁就等一会儿再写，比整轮 FAIL、当天没报表好。5×60s 上限 = 5 分钟，
# 离下游的晨检/午后简报（09:00 / 14:20）还有余量。
# 环境变量可覆盖（测试/临时调试）：AUTO_PIPELINE_LOCK_RETRIES / _WAIT_SEC
REPORT_LOCK_RETRIES = int(os.environ.get("AUTO_PIPELINE_LOCK_RETRIES") or 5)
REPORT_LOCK_WAIT_SEC = float(os.environ.get("AUTO_PIPELINE_LOCK_WAIT_SEC") or 60)


def _is_lock_error(e):
    """写盘撞锁：generate_daily_report 会包成 RuntimeError(msg) from PermissionError。"""
    if isinstance(e, PermissionError):
        return True
    if isinstance(e.__cause__, PermissionError):
        return True
    s = str(e)
    return "被占用" in s or "Permission denied" in s or "Errno 13" in s


def _with_lock_retry(fn, cat, retries=None, wait=None, sleep=time.sleep):
    """跑 fn()，撞锁则等 wait 秒重试 → (返回值, 重试次数)。非锁错误立即抛。

    sleep 可注入，单测不需要真的等。
    """
    n = REPORT_LOCK_RETRIES if retries is None else retries
    gap = REPORT_LOCK_WAIT_SEC if wait is None else wait
    for i in range(n + 1):
        try:
            return fn(), i
        except Exception as e:                  # noqa: BLE001
            if i >= n or not _is_lock_error(e):
                raise
            log_line(f"WARN report {cat} 被占用，{gap:.0f}s 后重试 ({i + 1}/{n})")
            sleep(gap)



def log_line(msg):
    """管线日志：所有成功/失败都落一行，排障唯一入口。"""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass


def _step_import():
    """指纹增量导入全部品类批次。返回 (导入数, 跳过数)。"""
    from etl.import_excel import ensure_schema, import_batch
    from app.db import SessionLocal
    ensure_schema()
    imported = skipped = 0
    for meta in list_batches(all_categories=True):
        cat = meta.get("category")
        try:
            with SessionLocal() as session:
                r = import_batch(session, meta["name"], force=False,
                                 category=cat)
                session.commit()
            if r:
                imported += 1
            else:
                skipped += 1
        except Exception as e:                  # noqa: BLE001
            log_line(f"FAIL import {cat}/{meta['name']}: "
                     f"{type(e).__name__}: {e}")
            raise
    return imported, skipped


def _step_snapshots_and_reports():
    """每品类：复盘重算（含 KPI 快照）→ 在研快照 → 单据增量基线 → 当日日报。

    T10 的 `maybe_doc_snapshot` 放在这里而不是日报里：它只依赖复盘数据，
    且必须**紧跟导入之后**（这时的指纹才是刚导进来的那份）。
    指纹没变它自己会跳过，所以一天两次管线不会产生假增量。
    """
    from app.doc_delta_service import maybe_doc_snapshot
    from app.inflight_service import (generate_daily_report,
                                      maybe_inflight_snapshot)
    from app.routers.review import get_review_data

    reports = {}
    failures = []                           # [(品类, 错误摘要)]
    # 品类顺序：DATA_ROOTS 声明序（手机膜在前），只处理**目录里真有批次**的品类。
    # 逐品类 try/except：一个品类挂掉（典型：Excel/WPS 开着该品类日报 → 写入
    # PermissionError）不连累其他品类——各自尽力出报告，最后统一抛出，
    # 保持 pipeline FAIL 告警语义（2026-10-07 实锤：手机膜撞锁 → 手机壳陪葬）。
    for cat in DATA_ROOTS:
        if not list_batches(cat):
            continue
        try:
            get_review_data(cat)                # 复盘重算内部会记 KPI 快照
            maybe_inflight_snapshot(cat)        # 在研节点快照（进度 diff 用）
            d = maybe_doc_snapshot(cat)         # T10 未闭环 SKU 单据增量基线
            log_line(f"{'OK' if d.get('recorded') else 'SKIP'} doc-delta {cat}: "
                     f"{d.get('reason')}")
            r, tries = _with_lock_retry(
                lambda: generate_daily_report(cat), cat)
            if tries:
                log_line(f"OK report {cat} 占用重试 {tries} 次后写入成功")
            reports[cat] = r
            log_line(f"OK report {cat} -> {r.get('path')}")
        except Exception as e:                  # noqa: BLE001
            failures.append(f"{cat}: {type(e).__name__}: {e}")
            log_line(f"FAIL category {cat}: {traceback.format_exc(limit=3)}")
    if failures:
        raise RuntimeError("部分品类未出日报（" + "；".join(failures) + "）——"
                           "其余品类已正常落盘，未出的一般是被 Excel/WPS 占用")
    return reports


def _step_cleanup():
    from app.inflight_service import cleanup_expired_reports
    r = cleanup_expired_reports()
    if r.get("removed"):
        log_line(f"OK cleanup removed={len(r['removed'])}")
    return r


def _step_mail(reports, skip_mail):
    """邮件只发默认品类（mail_to 是一套收件人，避免两封重复打扰）。"""
    from app.category_rules import DEFAULT_CATEGORY
    from etl.mailer import send_daily_report

    if skip_mail:
        return {"sent": False, "reason": "skip_mail=true"}
    r = send_daily_report(
        DEFAULT_CATEGORY,
        (reports.get(DEFAULT_CATEGORY) or {}).get("path"),
        (reports.get(DEFAULT_CATEGORY) or {}).get("date")
        or datetime.now().strftime("%Y-%m-%d"))
    log_line(f"{'OK' if r[0] else 'WARN'} mail: {r[1]}")
    return {"sent": r[0], "reason": r[1]}


def run_pipeline(category=None, skip_mail=True):
    """跑完整管线。返回 {ok, steps, errors, reports, mail}（永不抛异常）。"""
    t0 = datetime.now()
    errors, steps, reports = [], {}, {}
    log_line(f"=== pipeline start (category={category or 'all'}, "
             f"skip_mail={skip_mail}) ===")

    # 1. 导入
    try:
        imported, skipped = _step_import()
        steps["import"] = {"imported": imported, "skipped": skipped}
        log_line(f"OK import imported={imported} skipped={skipped}")
    except Exception as e:                      # noqa: BLE001
        errors.append(f"导入失败: {type(e).__name__}: {e}")
        log_line(f"FAIL pipeline(import): {traceback.format_exc(limit=3)}")

    # 2. 快照 + 日报
    try:
        reports = _step_snapshots_and_reports()
        steps["reports"] = {k: v.get("path") for k, v in reports.items()}
    except Exception as e:                      # noqa: BLE001
        errors.append(f"快照/日报失败: {type(e).__name__}: {e}")
        log_line(f"FAIL pipeline(reports): {traceback.format_exc(limit=3)}")

    # 3. 清理（失败不影响整体）
    try:
        _step_cleanup()
    except Exception as e:                      # noqa: BLE001
        errors.append(f"日报清理失败: {type(e).__name__}: {e}")

    # 4. 邮件（前面失败时也尝试发——日报可能是昨天的，总比不发好）
    try:
        mail = _step_mail(reports, skip_mail)
    except Exception as e:                      # noqa: BLE001
        mail = {"sent": False, "reason": f"{type(e).__name__}: {e}"}
        errors.append(f"邮件失败: {mail['reason']}")
        log_line(f"FAIL pipeline(mail): {mail['reason']}")

    ok = not errors
    result = {
        "ok": ok,
        "at": t0.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsedSec": round((datetime.now() - t0).total_seconds(), 1),
        "steps": steps,
        "reports": {k: v.get("path") for k, v in reports.items()},
        "mail": mail,
        "errors": errors,
    }
    try:
        LAST_RUN.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    except OSError:
        pass
    log_line(f"{'===' if ok else '***'} pipeline "
             f"{'ok' if ok else 'FAIL'} ({result['elapsedSec']}s) "
             f"errors={errors}")
    return result


def main():
    ap = argparse.ArgumentParser(description="自动管线：导入→快照→日报→邮件")
    ap.add_argument("--category", default=None, help="只处理指定品类（缺省全部）")
    ap.add_argument("--skip-mail", action="store_true",
                    help="不发邮件（调试/白天手动触发用）")
    args = ap.parse_args()
    r = run_pipeline(category=args.category, skip_mail=args.skip_mail)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
