# -*- coding: utf-8 -*-
"""verify_sku_order.py — P0-1 回归验证：SKU 是否按「立项时间从新到久」排序。

用 TestClient 在进程内打两个接口（不抢 8000 端口）：
  GET /api/skus            -> items[].firstProposalAt
  GET /api/review/overview -> rows[].sku（消费 by_sku.first_at）

断言：时间序列单调不增；并打印前 15 行便于人工核对。
用法：PREWARM=0 <envs/default>/python.exe scripts/verify_sku_order.py
"""
import os
import sys

os.environ.setdefault("PREWARM", "0")          # 不预热，避免等 10s
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "_sku_order_out.txt")


class _Tee:
    """把 stdout 同时写进 UTF-8 文件（本机 PowerShell 重定向会产出 UTF-16）。"""

    def __init__(self, path):
        self.f = open(path, "w", encoding="utf-8")
        self.out = sys.stdout

    def write(self, s):
        self.f.write(s)
        self.out.write(s)

    def flush(self):
        self.f.flush()
        self.out.flush()


sys.stdout = _Tee(_OUT)

from fastapi.testclient import TestClient   # noqa: E402
from app.main import app                    # noqa: E402


def monotonic_desc(seq):
    """返回 (是否单调不增, 首个逆序位置)"""
    for i in range(1, len(seq)):
        if seq[i] > seq[i - 1]:
            return False, i
    return True, -1


def main():
    fails = []
    with TestClient(app) as c:
        # ---- 1. /api/skus ----
        r = c.get("/api/skus")
        assert r.status_code == 200, f"/api/skus -> {r.status_code}"
        items = r.json()["items"]
        keys = [it.get("firstProposalAt") or "" for it in items]
        ok, bad = monotonic_desc(keys)
        print("=" * 78)
        print(f"/api/skus  total={len(items)}  立项时间倒序: {'OK' if ok else f'FAIL(逆序@{bad})'}")
        print("  前 15 行  sku | firstProposalAt | packets/parts")
        for it in items[:15]:
            parts = it.get("parts") or {}
            n = sum(sum(v.values()) for v in parts.values())
            print(f"  {it['sku']:<18} {str(it.get('firstProposalAt')):<20} "
                  f"{sorted(parts.keys())} n={n}/{'/'.join(it.get('batches') or [])}")
        if not ok:
            fails.append("skus 非倒序")
        missing = [it["sku"] for it in items if not it.get("firstProposalAt")]
        print(f"  缺立项时间（排最后）: {len(missing)} 个 {missing[:6]}")

        # ---- 2. /api/review/overview ----
        r2 = c.get("/api/review/overview")
        assert r2.status_code == 200, f"/api/review/overview -> {r2.status_code}"
        j = r2.json()
        rows = j.get("rows") or []
        skus = [x["sku"] for x in rows]
        detail = j.get("skuDetails") or j.get("details") or {}
        seq = []
        for s in skus:
            d = detail.get(s) or {}
            seq.append(d.get("firstProposalAt") or "")
        ok2, bad2 = monotonic_desc(seq) if any(seq) else (True, -1)
        print("=" * 78)
        print(f"/api/review/overview  rows={len(rows)}  倒序: {'OK' if ok2 else f'FAIL(逆序@{bad2})'}")
        print("  前 15 行  " + ", ".join(skus[:15]))
        sample = detail.get(skus[0]) if skus else None
        if sample is not None:
            print(f"  detail 首行字段: {sorted(sample.keys())[:14]}")
        if not ok2:
            fails.append("overview 非倒序")

    print("=" * 78)
    if fails:
        print("RESULT: FAIL -> " + "; ".join(fails))
        return 1
    print("RESULT: PASS  两个接口均按立项时间从新到久排序")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
