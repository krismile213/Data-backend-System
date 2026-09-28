#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_category_live.py — 在线「品类分规则」冒烟验证（跑在活的后端上）
========================================================================
`verify_shell_rules.py` 是**离线**跑规则集（不依赖入库）；本脚本是它的在线补充：
真的打 HTTP，确认「接口层不炸、口径透传正确、快照不被品类交替搅乱」。

必须覆盖的坑（都在真机上踩过）：

  ① `/api/review/overview?category=手机壳` 曾**永久挂死**整个后端 ——
     get_review_data → record_snapshot → _build_kpi → get_review_data 同线程
     重入，非重入锁自锁。修复见 `review._cache_by_cat` / `history._reentry`。
     所以这里每个请求都带**硬超时**，超时即判 FAIL（而不是傻等）。
  ② 品类必须真的分发：手机壳响应里 `startNode` 必须是「提案」，
     `targetsProvisional` 必须是 True（继承膜口径、待校准）。
  ③ 快照按指纹去重：手机膜/手机壳交替访问后，`/api/history` 的长度必须**稳定**
     （只比"最后一张"会每访问一次就追加一张，把历史刷爆）。

用法：
    python scripts/verify_category_live.py [base_url]     # 默认 127.0.0.1:8001

退出码 0 = PASS。报告另存 scripts/verify_category_live.txt。
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
TIMEOUT = 60
REPORT = Path(__file__).with_suffix(".txt")

LINES = []
FAILS = []


def log(s=""):
    LINES.append(s)
    print(s)


def get(path, q=None):
    url = BASE + path + (("?" + urllib.parse.urlencode(q)) if q else "")
    t = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", "replace")
        return r.status, body, time.time() - t
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), time.time() - t
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", time.time() - t


def check(label, ok, detail=""):
    flag = "PASS" if ok else "FAIL"
    log(f"  [{flag}] {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(label)
    return ok


def json_of(body):
    try:
        return json.loads(body)
    except Exception:
        return None


def main():
    log("=" * 78)
    log("在线「品类分规则」冒烟验证")
    log("=" * 78)
    log(f"目标后端 : {BASE}")
    log(f"超时     : {TIMEOUT}s（超时 = FAIL，因为曾经出现过永久挂死）")
    log("")

    # ---- 0. 存活 -------------------------------------------------------
    log("[0] 后端存活")
    st, body, dt = get("/api/health")
    if not check("/api/health 可达", st == 200, f"{st} {dt:.2f}s"):
        log("\n后端不可达，后续跳过。请先启动后端：")
        log("  cd backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 8001")
        return finish()

    # ---- 1. 品类口径透传 -----------------------------------------------
    log("")
    log("[1] 品类口径透传（/api/review/overview）")
    for cat, want_start, want_prov in (("手机膜", "取数", False), ("手机壳", "提案", True)):
        st, body, dt = get("/api/review/overview", {"category": cat})
        j = json_of(body) or {}
        check(f"category={cat} 返回 200", st == 200, f"{st} {dt:.2f}s {len(body)}B")
        if st != 200:
            continue
        check(f"category={cat} startNode == {want_start}",
              j.get("startNode") == want_start, repr(j.get("startNode")))
        check(f"category={cat} targetsProvisional == {want_prov}",
              j.get("targetsProvisional") is want_prov, repr(j.get("targetsProvisional")))
        check(f"category={cat} 回显 category",
              j.get("category") == cat, repr(j.get("category")))

    # ---- 2. 缺省品类 = 手机膜（不能悄悄变成别的） ----------------------
    log("")
    log("[2] 缺省品类回退")
    st, body, dt = get("/api/review/overview")
    j = json_of(body) or {}
    check("无 category 参数 → 手机膜", st == 200 and j.get("startNode") == "取数",
          f"{st} startNode={j.get('startNode')!r}")

    # ---- 3. 关键接口逐个打（含历史/预警这些曾挂死的） ------------------
    log("")
    log("[3] 关键接口存活")
    for path in ("/api/history", "/api/alerts", "/api/review/assessment",
                 "/api/transport", "/api/scenarios", "/api/routes", "/api/watch"):
        st, body, dt = get(path)
        check(f"{path} 200", st == 200, f"{st} {dt:.2f}s {len(body)}B")

    # ---- 4. 品类交替不应把快照刷爆 -------------------------------------
    log("")
    log("[4] 快照去重（品类交替后 /api/history 长度应稳定）")
    counts = []
    for i in range(3):
        get("/api/review/overview", {"category": "手机壳"})
        get("/api/review/overview", {"category": "手机膜"})
        st, body, dt = get("/api/history")
        j = json_of(body) or {}
        counts.append(j.get("count", len(body)))
    check("三轮交替后快照张数稳定", len(set(counts)) == 1,
          f"观测={counts}（若递增=按'最后一张'去重，会把历史刷爆）")

    # ---- 5. 快照不得跨品类串味 -----------------------------------------
    log("")
    log("[5] 快照品类一致性")
    st, body, dt = get("/api/history")
    j = json_of(body) or {}
    snaps = j.get("snapshots") or []
    # 串味的判据：指纹里的品类前缀 与 快照自报的 category 不一致
    # （旧 bug 的表现 = 手机壳指纹 + 手机膜的 KPI + 没有 category 字段）
    bad = []
    for s in snaps:
        fp = str(s.get("fingerprint", ""))
        fp_cat = fp.split("|", 1)[0] if "|" in fp else None
        declared = s.get("category")
        if fp_cat in ("手机膜", "手机壳") and declared is not None and fp_cat != declared:
            bad.append(s)
    check("快照指纹品类 == 自报 category（无跨品类串味）", not bad,
          f"{len(bad)} 张不一致" if bad else "0 张不一致")
    # 自动落快照仅限默认品类 → 不应出现"无 category 字段的手机壳指纹"
    unlabelled_shell = [s for s in snaps
                        if str(s.get("fingerprint", "")).startswith("手机壳|")
                        and s.get("category") is None]
    check("无「手机壳指纹但无 category」的自动串味快照", not unlabelled_shell,
          f"{len(unlabelled_shell)} 张" if unlabelled_shell else "0 张")

    return finish()


def finish():
    log("")
    log("=" * 78)
    if FAILS:
        log(f"验收：FAIL（{len(FAILS)} 项）")
        for f in FAILS:
            log(f"  - {f}")
    else:
        log("验收：PASS")
    log("=" * 78)
    REPORT.write_text("\n".join(LINES), encoding="utf-8")
    print(f"→  {REPORT}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
