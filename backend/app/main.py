# -*- coding: utf-8 -*-
"""main.py — FastAPI 入口 + CORS + 路由注册"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import (alerts, anomalies, batches, compare, fillins, history,
                      overview, records, review, routes, rules, scenarios,
                      skus, timeliness, transport, watch)

app = FastAPI(title="新品售前流程数据后台系统", version="1.0.0")

# CORS：本地前后端分离（vite dev 5173 / preview 4173 -> 后端 8000），只放行本机来源
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173",
                   "http://127.0.0.1:4173", "http://localhost:4173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(batches.router)
app.include_router(overview.router)
app.include_router(timeliness.router)
app.include_router(anomalies.router)
app.include_router(records.router)
app.include_router(compare.router)
app.include_router(skus.router)
app.include_router(review.router)
app.include_router(scenarios.router)
app.include_router(routes.router)
app.include_router(transport.router)
app.include_router(alerts.router)
app.include_router(watch.router)
app.include_router(fillins.router)
app.include_router(history.router)
app.include_router(rules.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
