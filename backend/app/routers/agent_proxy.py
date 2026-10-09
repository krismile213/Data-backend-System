# -*- coding: utf-8 -*-
"""agent_proxy.py — AI 助手 BFF 反向代理（拓扑 B，docs/AI助手嵌入实现方案.md §2.2）

平台后端把 `/api/agent/{path}` 反向代理到用户自研 Agent（`server.py`，默认 `:8765`），
本模块是「**唯一出站收口**」，职责四件事：

  1. 鉴权收口：由**服务端**注入 `Authorization: Bearer <AGENT_TOKEN>`，前端永不接触 token（§8.1）；
  2. 数据出站收口：所有出站内容都经 `_sanitize_outbound()`（分级/脱敏，§9.1，本轮骨架）；
  3. SSE 透传：`GET /api/agent/api/events` 逐块转发 `text/event-stream`，保头、透传 `Last-Event-ID`；
  4. 降级：上游不可达 → 502/503 + JSON，绝不让异常穿成 500。

不变量（§8.2，务必守住）：
  - SSE 响应必须带 `X-Accel-Buffering: no` + `Cache-Control: no-cache`；
  - 用 `httpx` 异步**流式**转发，逐块 `yield`，**禁止** `.read()` 全量缓冲；
  - 平台侧不得有 Gzip/压缩中间件（`main.py` 已确认无，否则会缓冲 SSE）。
"""

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..config import agent_base_url, agent_timeout_seconds, agent_token

router = APIRouter(prefix="/api/agent", tags=["agent"])

#: 逐跳（hop-by-hop）头 + 由本层重建的头：请求/响应两侧都不原样转发。
#: 覆盖 content-length（由 httpx/Starlette 重算）、content-encoding（上游压缩会被 httpx 解码，
#: 若仍带原头会与解码后的实体不一致）、host、authorization（由本层注入）。
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-encoding",
    "host", "authorization", "content-length",
})

#: 支持的方法：覆盖 §4.1 契约里的全部端点的动词。
_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def _sanitize_outbound(body: bytes, headers: dict):
    """出站唯一收口：分级 → 脱敏 → 才转发（T10 / docs …§9.1）。

    本轮（P0）为**骨架：直通**，不做任何改写（先把钩子/收口位置钉死）。

    TODO(T10)：在此实现字段/实体级白名单：
      - 放行：批次号 / SKU / 机型 / 统计值 / 页面名；
      - 剔除或掩码：负责人姓名·手机号 / 内部备注 / 联系方式类字段；
      - 命中「将上云」时回传分级结果，供面板显式提示
        「本次将发送【X 类】数据至云端模型，是否继续」（需用户确认后才发送）。

    为什么钩子落在这里、而不是各调用方：BFF 是所有出站内容
    （P1 的 `X-Plat-Context` 上下文头 + 用户消息 + 工具结果回灌）的唯一必经之路 ——
    脱敏只有落在这唯一收口上才绕不过去。

    :param body:    即将转发给 Agent 的请求体字节（UTF-8）。
    :param headers: 即将转发给 Agent 的请求头（已含注入的 Authorization）。
    :return:        清洗后的 ``(body, headers)``；本轮原样返回。
    """
    return body, headers


def _build_upstream_headers(request: Request) -> dict:
    """用入站请求头构造出站请求头：透传可安全转发的头 + 注入 Bearer。

    - **透传** `Last-Event-ID`（SSE 断线重放，§4.2）等业务头；
    - 过滤逐跳头与 host / authorization / content-length；
    - 强制 `Accept-Encoding: identity`：避免上游压缩把 SSE 块揉在一起；
    - token 已配置时**注入** `Authorization: Bearer <token>`。
    """
    headers = {}
    for key, value in request.headers.items():
        if key.lower() in _HOP_BY_HOP:
            continue
        headers[key] = value
    headers["Accept-Encoding"] = "identity"
    token = agent_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _error_response(detail: str, status_code: int) -> JSONResponse:
    """上游不可达/异常时的统一降级响应：明确的状态码 + JSON（非 500）。"""
    return JSONResponse(
        status_code=status_code,
        content={"error": "Agent 上游不可用", "detail": detail},
    )


def _is_event_stream(content_type: str) -> bool:
    """判断上游响应是否为 SSE（兼容 `text/event-stream; charset=...`）。"""
    return (content_type or "").lower().startswith("text/event-stream")


async def agent_proxy(path: str, request: Request):
    """把 `/api/agent/{path}` 转发到 Agent 上游同名路径（含查询串）。

    - SSE 响应：`StreamingResponse` 逐块透传，保 `X-Accel-Buffering` / `Cache-Control`；
    - 普通响应：整读后按原状态码/头返回；
    - 上游不可达：503（连接类）/ 502（其他），JSON body。
    """
    base = agent_base_url()
    upstream_url = f"{base}/{path.lstrip('/')}"
    if request.url.query:
        upstream_url = f"{upstream_url}?{request.url.query}"

    in_headers = _build_upstream_headers(request)
    raw_body = await request.body()
    body, out_headers = _sanitize_outbound(raw_body, in_headers)

    # connect 超时单独给短值（上游没起就要快速失败，别把面板吊住）；
    # read 超时用配置值（足够长以支撑 SSE 长连接）。
    #
    # ⚠️ trust_env=False：**必须**。默认 httpx 会读取环境变量里的 HTTP(S)_PROXY 并
    #   把本地 Agent 请求也丢给企业代理 —— 那会（a）缓冲 SSE 破坏逐字流式、（b）上游
    #   没起时代理返回自己的 502 掩盖成「已连通」。BFF 与 Agent 同机，直连即可。
    timeout = httpx.Timeout(agent_timeout_seconds(), connect=10.0)
    client = httpx.AsyncClient(timeout=timeout, follow_redirects=False,
                               trust_env=False)
    try:
        upstream_req = client.build_request(
            request.method, upstream_url, headers=out_headers,
            content=body if body else None,
        )
        # stream=True：不预读正文；连接类错误在此处抛出，可被捕获成 JSON 降级。
        upstream_resp = await client.send(upstream_req, stream=True)
    except httpx.ConnectError as exc:
        await client.aclose()
        return _error_response(f"Agent 未启动（{exc.__class__.__name__}）", 503)
    except httpx.ConnectTimeout as exc:
        await client.aclose()
        return _error_response(f"连接 Agent 超时（{exc.__class__.__name__}）", 503)
    except httpx.RequestError as exc:
        await client.aclose()
        return _error_response(
            f"转发 Agent 失败（{exc.__class__.__name__}: {exc}）", 502)

    content_type = upstream_resp.headers.get("content-type", "")

    if _is_event_stream(content_type):
        # SSE：逐块原样透传（aiter_raw 不做二次解码），结束时释放连接与客户端。
        async def stream():
            try:
                async for chunk in upstream_resp.aiter_raw():
                    yield chunk
            finally:
                await upstream_resp.aclose()
                await client.aclose()

        # 关键头：禁用各级缓冲，保证逐字流式（§8.2）。
        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # 普通响应：整读后按原状态码/头返回（去掉逐跳头，Starlette 重算 content-length）。
    try:
        content = await upstream_resp.aread()
    finally:
        await upstream_resp.aclose()
        await client.aclose()

    resp_headers = {
        key: value for key, value in upstream_resp.headers.items()
        if key.lower() not in _HOP_BY_HOP
    }
    return Response(content=content, status_code=upstream_resp.status_code,
                    headers=resp_headers)


# 逐方法注册（而非一个 `api_route(methods=[...])`）：
# 单个多方法路由会让 FastAPI 为每个方法复用同一个 `unique_id`，进而在生成
# /openapi.json 时报 "Duplicate Operation ID" 告警（会污染测试输出）。逐方法注册
# 各给一个唯一 operation_id，彻底消除该告警。
for _method in _METHODS:
    router.add_api_route(
        "/{path:path}",
        agent_proxy,
        methods=[_method],
        name=f"agent_proxy_{_method.lower()}",
        operation_id=f"agent_proxy_{_method.lower()}",
    )
