# -*- coding: utf-8 -*-
"""只读探针：取消 5 天窗后，事中日报「货件运输」sheet 行数变化（两品类）。

用途：验证 2026-11-01 口径改动（`_shipment_tracking_rows` 全量保留）的实际增量，
并抽样核对「已归档(到货N天)」文案。只读，不写库、不落盘。

    python scripts/probe_shipment_rows.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import inflight_service as svc        # noqa: E402


def main():
    for cat in ("手机膜", "手机壳"):
        data = svc.get_inflight_data(cat, mute_emag_noship=True)
        rows = data["shipmentTracking"]
        # 旧口径 = 只保留「未到货 / 到货 ≤5 天」的票
        old = [r for r in rows
               if r["arr"] is None or r.get("keep") is None or r["keep"] <= 5]
        arch = [r for r in rows if r.get("archived")]
        print(f"[{cat}] 在研 SKU {len(data['items'])} 个 | "
              f"货件行 旧口径 {len(old)} → 新口径 {len(rows)}"
              f"（+{len(rows) - len(old)}，其中已归档 {len(arch)}）")
        for r in arch[:5]:
            print(f"   · {r['sku']} {r['trk']} 到货 {r['arr']} → {r['track']}")
        if not arch:
            print("   （无已归档票）")


if __name__ == "__main__":
    main()
