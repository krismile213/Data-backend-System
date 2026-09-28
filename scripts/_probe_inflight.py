# -*- coding: utf-8 -*-
"""手动跑一次 compute_inflight，看集成测为何 skip。"""
import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import inflight_service as svc

try:
    data = svc.compute_inflight(None)
    print("OK computedAt =", data["computedAt"])
    print("inResearchCount =", data["inResearchCount"],
          "overdueSku =", data["overdueSkuCount"],
          "warn =", data["warnCount"], "multiRound =", data["multiRoundCount"])
    for x in data["items"][:5]:
        print("  ", x["sku"], x["status"], x["worstSeg"],
              x["worstOverDays"], "warn:", len(x["warnSegs"]))
    alerts = svc.compute_alerts_today(None, data=data)
    print("newOverdue =", len(alerts["todayNewOverdue"]),
          "ongoing =", len(alerts["ongoingOverdue"]),
          "warnings =", len(alerts["warnings"]))
    if alerts["todayNewOverdue"]:
        print("  new[0]:", alerts["todayNewOverdue"][0])
    if alerts["ongoingOverdue"]:
        print("  ong[0]:", alerts["ongoingOverdue"][0])
except Exception:
    traceback.print_exc()
