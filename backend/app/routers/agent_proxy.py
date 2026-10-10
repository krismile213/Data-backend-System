# -*- coding: utf-8 -*-
"""agent_proxy.py — AI 助手 BFF 反向代理（拓扑 B，docs/AI助手嵌入实现方案.md §2.2）

平台后端把 `/api/agent/{path}` 反向代理到用户自研 Agent（`server.py`，默认 `:8765`），
本模块是「**唯一出站收口**」，职责四件事：

  1. 鉴权收口：由**服务端**注入 `Authorization: Bearer <AGENT_TOKEN>`，前端永不接触 token（§8.1）；
  2. **上行**出站收口：**上行内容**（用户消息体 + `X-Plat-Context` 上下文头）都经
     `_sanitize_outbound()` 脱敏（T10-a，实装于 `app/outbound_guard.py`）。
     ⚠️ 工具结果是 Agent 自己调平台 `:8000` 拿的，**不经过 BFF**，其下行脱敏由 Agent 侧
     `plugins/workmain.py::_plat_sanitize` 负责——本层**不做**也**做不到**「洗工具结果」；
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
from ..outbound_guard import sanitize_context, sanitize_request_body

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


def _header_get(headers: dict, name: str):
    """大小写不敏感地取头值（入站头名大小写不保证）。缺失返回 ``None``。"""
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def _header_del(headers: dict, name: str) -> None:
    """大小写不敏感地删除全部同名头（就地修改）。"""
    target = name.lower()
    for key in [k for k in headers if k.lower() == target]:
        headers.pop(key, None)


def _sanitize_outbound(body: bytes, headers: dict):
    """出站唯一收口：脱敏 → 才转发（T10-a / docs/AI助手T10-P1实现契约.md §2）。

    收口的是**上行**：请求体（用户消息）+ `X-Plat-Context` 上下文头。

    为什么钩子落在这里、而不是各调用方：BFF 是**上行**内容（P1 的 `X-Plat-Context` 上下文头 +
    用户消息体）的唯一必经之路。⚠️ 工具结果回灌**不经过 BFF**（Agent 自己调平台 `:8000` 拿数据后
    直接发云端模型），其脱敏由 Agent 侧 `plugins/workmain.py::_plat_sanitize` 负责——本层只收上行。

    具体行为：
      - `sanitize_request_body(body, ct)`：仅 `application/json` 且可解析时才改写（剔敏感键 + 掩手机号）；
        ct 从 `headers` 里取（`_build_upstream_headers` 只过滤逐跳头，`content-type` 会被保留）；
      - `sanitize_context(headers.get("x-plat-context"))`：白名单 6 键 + 掩码 + 重新 percent-encode；
        有值写回 `headers["X-Plat-Context"]`，为 `None` 则 `pop` 掉该头（含「没带」的情形）；
      - 命中任何脱敏时 `print("[agent-guard] ...")` 打一行**摘要**（只打计数/理由，**禁止明文值**）。

    安全兜底：脱敏失败一律「保可用」——`outbound_guard` 任何路径都不抛异常，解析失败即原样转发。

    ⚠️ 前端 `scanSensitiveText` 只是 UX 提醒层，真正的强制保障是这里（`_sanitize_outbound`）
    与 Agent 侧 `_plat_sanitize`。

    :param body:    即将转发给 Agent 的请求体字节（UTF-8）。
    :param headers: 即将转发给 Agent 的请求头（已含注入的 Authorization）。
    :return:        清洗后的 ``(body, headers)``。
    """
    content_type = _header_get(headers, "content-type")
    new_body, body_stats = sanitize_request_body(body, content_type)

    raw_ctx = _header_get(headers, "x-plat-context")
    new_ctx, ctx_info = sanitize_context(raw_ctx)
    if new_ctx is not None:
        _header_del(headers, "x-plat-context")
        headers["X-Plat-Context"] = new_ctx
    else:
        _header_del(headers, "x-plat-context")

    # 摘要日志（本项目无 logging 约定，沿用 prewarm.py 的 print；**只打计数/理由，禁止明文值**）。
    # 何时打：请求体真的被改写、或上下文真的被改写、或上下文被拒（畸形/超长，值得留痕）。
    if (body_stats.get("changed") or ctx_info.get("changed")
            or ctx_info.get("reason") in ("malformed", "toolong")):
        print(
            "[agent-guard] outbound sanitize: "
            f"body{{dropped={body_stats.get('dropped_keys', 0)},"
            f"masked={body_stats.get('masked_values', 0)},"
            f"reason={body_stats.get('reason', 'ok')}}} "
            f"ctx{{dropped={ctx_info.get('dropped_keys', 0)},"
            f"masked={ctx_info.get('masked_values', 0)},"
            f"reason={ctx_info.get('reason', 'ok')}}}",
            flush=True,
        )
    return new_body, headers


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


def _error_response(detail: str, status_code: int,
                    error: str = "Agent 上游不可用") -> JSONResponse:
    """上游不可达/异常时的统一降级响应：明确的状态码 + JSON（非 500）。"""
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "detail": detail},
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
    except httpx.InvalidURL as exc:
        # ⚠️ httpx.InvalidURL **不继承** RequestError/HTTPError（直接继承 Exception，
        #    见 `python -c "import httpx; httpx.InvalidURL.__mro__"`）—— 非法路径会在
        #    build_request() 处抛它；若漏捕会穿透成 500（违反「绝不穿成 500」）。
        #    客户端请求路径非法 → 400 + JSON。
        await client.aclose()
        return _error_response(
            f"请求路径非法（{exc.__class__.__name__}）", 400,
            error="请求路径非法")
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
    except httpx.HTTPError as exc:
        # 兜底：RequestError 之外的 HTTPError 子类（防未来 httpx 版本新增异常类型穿透成 500）
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
