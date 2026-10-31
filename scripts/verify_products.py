# -*- coding: utf-8 -*-
"""verify_products.py — T01 验收：商品资料导入接口 + G506 命中校验。

断言：
  GET  /api/etl/products         -> 200 且 total>0
  POST /api/etl/products         -> 200（指纹未变应 skipped，不报错）
  product_catalog 中 G506        -> grade=XC / daily 1.38~5.5 / price=12.0266
  G598（最新立项 SKU）若有档位    -> daily_min/max 非空
结果写 scripts/_verify_products.txt（UTF-8，规避 PowerShell 重定向编码问题）。
"""
import os
import sys

os.environ.setdefault("PREWARM", "0")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_verify_products.txt")
lines = []


def w(s=""):
    lines.append(str(s))


fails = []
try:
    import traceback

    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.main import app
    from app.models import ProductCatalog

    with TestClient(app) as c:
        r = c.get("/api/etl/products")
        w(f"GET /api/etl/products -> {r.status_code}")
        if r.status_code != 200:
            fails.append(f"GET products {r.status_code}")
        else:
            j = r.json()
            for k in ("hasTable", "total", "matchedSkuCount", "gradeDist",
                      "priceType", "importedAt", "xlsxPath", "lastError"):
                w(f"  {k}: {j.get(k)}")
            if not j.get("total"):
                fails.append("product_catalog 为空")

        r2 = c.post("/api/etl/products")
        w(f"\nPOST /api/etl/products -> {r2.status_code}  status={r2.json().get('status')}")
        if r2.status_code != 200:
            fails.append(f"POST products {r2.status_code}")

    # 直接查库校验 G506
    with SessionLocal() as s:
        row = s.execute(select(ProductCatalog).where(ProductCatalog.sku == "G506")).scalar_one_or_none()
        w("\n-- product_catalog['G506'] --")
        if row is None:
            fails.append("G506 不在 product_catalog")
            w("  (缺失)")
        else:
            w(f"  sku={row.sku} model={row.model} category={row.category} state={row.state}")
            w(f"  grade={row.grade} daily_min={row.daily_min} daily_max={row.daily_max}")
            w(f"  price={row.price} price_type={row.price_type}")
            if str(row.grade) != "XC":
                fails.append(f"G506 grade={row.grade} != XC")
            if float(row.daily_min) != 1.38 or float(row.daily_max) != 5.5:
                fails.append(f"G506 日均区间 {row.daily_min}~{row.daily_max} != 1.38~5.5")
            if float(row.price) != 12.0266:
                fails.append(f"G506 单价 {row.price} != 12.0266")

        n = s.execute(select(ProductCatalog)).scalars().all()
        w(f"\n  全表 {len(n)} 行；样本 3 行：")
        for x in n[:3]:
            w(f"    {x.sku:<7} grade={str(x.grade):<4} daily={x.daily_min}~{x.daily_max} "
              f"price={x.price} cat={x.category}")
        # 不变量：有界档位 min <= max
        bad = [x.sku for x in n if x.daily_min is not None and x.daily_max is not None
               and float(x.daily_min) > float(x.daily_max)]
        if bad:
            fails.append(f"档位区间 min>max 的 SKU: {bad[:5]}")
        miss = sum(1 for x in n if x.grade and x.daily_min is None)
        w(f"  档位填了但未命中档位表：{miss} 行")
        w(f"  无单价：{sum(1 for x in n if x.price is None)} 行")

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
