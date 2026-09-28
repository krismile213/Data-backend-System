# -*- coding: utf-8 -*-
"""main.py — FastAPI 入口 + CORS + 路由注册"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import (alerts, anomalies, batches, compare, dataadmin, diag,
                      fillins, history, inflight, manual, overview, records,
                      review, routes, rules, scenarios, skus, timeliness,
                      transport, watch)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动后择机空闲预热：把三处进程内缓存提前算好，少撞「第一个访问者」的冷启动。

    实测冷启动代价：overview 2.39s / alerts 2.55s，热缓存 0.03s。
    预热跑在 daemon 线程里，**不阻塞服务就绪**，且**只在没人访问时才动手**
    （见 prewarm 模块头部：立即预热经 A/B 实测会把首个访问从 2.39s 拖到 5.45s）。
    关闭方式：环境变量 PREWARM=0；测试环境自动跳过（见 prewarm._enabled）。
    """
    from .prewarm import warm_async
    warm_async("启动")
    yield


app = FastAPI(title="新品售前流程数据后台系统", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def track_activity(request, call_next):
    """记录请求时刻，供空闲预热判断「现在有没有人在用」。仅打一个时间戳，无其他开销。"""
    from .prewarm import note_activity
    note_activity()
    return await call_next(request)

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
app.include_router(inflight.router)
app.include_router(rules.router)
app.include_router(dataadmin.router)
app.include_router(manual.router)
app.include_router(diag.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
