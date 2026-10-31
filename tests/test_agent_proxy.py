# -*- coding: utf-8 -*-
"""test_agent_proxy.py — BFF 反向代理测试（T02）

覆盖（全部**不依赖真实 Agent 进程**）：
  1. SSE **逐块透传**（能观察到分块，不是一次性全量缓冲）；
  2. `Authorization: Bearer` 被**注入**到上游；
  3. `Last-Event-ID` 被**透传**；
  4. 上游不可达 → 502/503 **JSON**（不是 500 / 不是 HTML）。
  5. 普通 POST JSON 透传（非 SSE 分支）。

为什么用**真实 uvicorn** 而不是 `TestClient`：
  Starlette 1.6 的 `TestClient` 会把整个响应体**缓冲**进 `io.BytesIO`
  （`testclient.py:296,341,365`：先跑完整个 app 再返回），既观察不到分块，
  遇到不结束的 SSE 流还会**永久挂住**。所以要验证「逐块透传」，必须起真实服务 +
  用真实 `httpx` 客户端。stub 上游同样起真实 uvicorn —— 这样链路是真实的
  「httpx 客户端 → 平台代理(uvicorn) → httpx 异步 → stub 上游(uvicorn)」。

⚠️ 本机环境设了 `HTTP_PROXY=http://127.0.0.1:63056`（沙箱代理）。httpx 默认
  `trust_env=True` 会把 127.0.0.1 的请求也丢给它，而它会**缓冲 SSE**、并对不可达
  上游返回自己的 502 —— 既破坏「逐块透传」观测，也掩盖「上游不可达」语义。因此
  ① 代理侧（`agent_proxy.py`）用 `trust_env=False` 直连本地 Agent；
  ② 本测试的 httpx 客户端也一律 `trust_env=False`。
"""

import asyncio
import json
import queue
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from app.main import app as platform_app


# ===========================================================================
# 本地 stub 上游（最小 FastAPI app，模拟 Agent 的 /api/health、/api/chat、/api/events）
# ===========================================================================

class _StubState:
    """stub 上游与测试共享的状态容器。"""

    def __init__(self) -> None:
        #: 每次请求的观测记录：[{path, method, headers}]
        self.received: list = []
        #: SSE 分块的放行闸：未 set 前，生成器产出前两帧后**阻塞**
        self.release = threading.Event()


STUB = _StubState()


def _make_stub_app() -> FastAPI:
    """构造 stub 上游 app。"""
    stub = FastAPI()

    def _record(path: str, request: Request) -> None:
        STUB.received.append(
            {"path": path, "method": request.method,
             "headers": dict(request.headers)})

    @stub.get("/api/health")
    async def health(request: Request):  # noqa: ANN202
        _record("/api/health", request)
        return {"status": "ok", "model": "stub", "sessions": 0, "running": False}

    @stub.post("/api/chat")
    async def chat(request: Request):  # noqa: ANN202
        _record("/api/chat", request)
        return {"ok": True}

    @stub.get("/api/events")
    async def events(request: Request):  # noqa: ANN202
        _record("/api/events", request)

        async def gen():
            # 帧格式与 server.py:841-842 一致：`id: <seq>\ndata: <json(ensure_ascii=False)>\n\n`
            yield "data: %s\n\n" % json.dumps(
                {"kind": "hello", "tools": ["stub"], "model": "stub"},
                ensure_ascii=False)
            # 第一帧增量（id=6）
            yield "id: 6\ndata: %s\n\n" % json.dumps(
                {"kind": "assistant_delta", "text": "你"}, ensure_ascii=False)
            # 阻塞：release 未 set 前不再产出 —— 若代理「全量缓冲」，
            # 客户端在 release 前将收不到任何字节（测试据此断言分块透传）。
            while not STUB.release.is_set():
                await asyncio.sleep(0.02)
            # 第二帧增量（id=7）+ 结束事件（id=8）
            yield "id: 7\ndata: %s\n\n" % json.dumps(
                {"kind": "assistant_delta", "text": "好"}, ensure_ascii=False)
            yield "id: 8\ndata: %s\n\n" % json.dumps(
                {"kind": "task_end", "answer": "你好", "usage": {}},
                ensure_ascii=False)

        return StreamingResponse(
            gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return stub


# ===========================================================================
# 起服务工具
# ===========================================================================

def _free_port() -> int:
    """取一个当前空闲的本地端口（随后立即释放，交给 uvicorn 绑定）。"""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _serve(asgi_app, *, ready_timeout: float = 20.0):
    """在守护线程里起一个 uvicorn，返回 (base_url, server, thread)。"""
    port = _free_port()
    config = uvicorn.Config(asgi_app, host="127.0.0.1", port=port,
                            log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + ready_timeout
    while time.time() < deadline and not server.started:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("uvicorn 未能在 %.0fs 内就绪" % ready_timeout)
    return f"http://127.0.0.1:{port}", server, thread


def _shutdown(server, thread) -> None:
    server.should_exit = True
    thread.join(timeout=10)


# ===========================================================================
# fixtures
# ===========================================================================

@pytest.fixture(scope="module")
def stub_server():
    """stub 上游（真实 uvicorn）。"""
    url, server, thread = _serve(_make_stub_app())
    yield url
    STUB.release.set()  # 兜底放行，避免残留生成器吊住
    _shutdown(server, thread)


@pytest.fixture(scope="module")
def proxy_server():
    """平台 BFF 代理（真实 uvicorn，跑**真实** app.main：含中间件）。

    用 lifespan="off" 跳过预热/快照，保持测试轻量；代理逻辑不依赖 lifespan。
    """
    url, server, thread = _serve(platform_app)
    yield url
    _shutdown(server, thread)


@pytest.fixture(autouse=True)
def _reset_stub():
    """每个用例前后重置 stub 状态：release 先清空（默认阻塞），收尾再放行。"""
    STUB.release.clear()
    STUB.received.clear()
    yield
    STUB.release.set()


# ===========================================================================
# 用例
# ===========================================================================

def test_bearer_injected_on_get(proxy_server, stub_server, monkeypatch):
    """注入 Authorization: Bearer <AGENT_TOKEN>，并把非 SSE 响应透传回客户端。"""
    monkeypatch.setenv("AGENT_BASE_URL", stub_server)
    monkeypatch.setenv("AGENT_TOKEN", "tok-abc-123")

    resp = httpx.get(f"{proxy_server}/api/agent/api/health", timeout=10,
                     trust_env=False)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    rec = [r for r in STUB.received if r["path"] == "/api/health"]
    assert rec, "stub 上游未收到 /api/health（代理未转发）"
    assert rec[-1]["headers"].get("authorization") == "Bearer tok-abc-123"


def test_bearer_injected_on_post_json(proxy_server, stub_server, monkeypatch):
    """非 SSE 分支：POST JSON 透传 + Bearer 注入。"""
    monkeypatch.setenv("AGENT_BASE_URL", stub_server)
    monkeypatch.setenv("AGENT_TOKEN", "tok-abc-123")

    resp = httpx.post(f"{proxy_server}/api/agent/api/chat",
                      json={"session": "s1", "message": "hi"}, timeout=10,
                      trust_env=False)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    rec = [r for r in STUB.received if r["path"] == "/api/chat"]
    assert rec
    assert rec[-1]["headers"].get("authorization") == "Bearer tok-abc-123"
    assert rec[-1]["headers"].get("content-type", "").startswith("application/json")


def test_last_event_id_passthrough(proxy_server, stub_server, monkeypatch):
    """透传 Last-Event-ID 请求头（SSE 断线重放，server.py:835）。"""
    monkeypatch.setenv("AGENT_BASE_URL", stub_server)
    monkeypatch.setenv("AGENT_TOKEN", "tok-abc-123")
    STUB.release.set()  # 本用例不测分块，直接放行让流尽快结束

    with httpx.stream("GET", f"{proxy_server}/api/agent/api/events?session=s1",
                      headers={"Last-Event-ID": "5"}, timeout=10,
                      trust_env=False) as resp:
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("text/event-stream")
        for _ in resp.iter_lines():
            break  # 读到首个分块即可，随后关闭

    rec = [r for r in STUB.received if r["path"] == "/api/events"]
    assert rec, "stub 上游未收到 /api/events"
    assert rec[-1]["headers"].get("last-event-id") == "5"


def test_sse_chunked_passthrough(proxy_server, stub_server, monkeypatch):
    """核心：SSE 逐块透传（release 前必须能收到分块）+ 关键响应头齐全。"""
    monkeypatch.setenv("AGENT_BASE_URL", stub_server)
    monkeypatch.setenv("AGENT_TOKEN", "tok-abc-123")
    STUB.release.clear()  # 明确阻塞：release 前代理只可能收到前两帧

    q: "queue.Queue" = queue.Queue()

    def reader():
        try:
            with httpx.stream(
                    "GET",
                    f"{proxy_server}/api/agent/api/events?session=s1",
                    headers={"Last-Event-ID": "5"}, timeout=30,
                    trust_env=False) as resp:
                q.put(("status", resp.status_code))
                q.put(("ctype", resp.headers.get("content-type", "")))
                q.put(("xab", resp.headers.get("x-accel-buffering", "")))
                q.put(("cc", resp.headers.get("cache-control", "")))
                for line in resp.iter_lines():
                    q.put(("line", line))
        except Exception as exc:  # noqa: BLE001
            q.put(("error", repr(exc)))
        finally:
            q.put(("end", None))

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    # 1) 头部元信息
    meta = {}
    for key in ("status", "ctype", "xab", "cc"):
        got_key, got_val = q.get(timeout=15)
        assert got_key == key, f"期望 {key}，实到 {got_key}:{got_val!r}"
        meta[key] = got_val
    assert meta["status"] == 200
    assert meta["ctype"].startswith("text/event-stream")
    assert meta["xab"] == "no", "SSE 必须带 X-Accel-Buffering: no"
    assert meta["cc"] == "no-cache", "SSE 必须带 Cache-Control: no-cache"

    # 2) **release 之前**必须收到分块（证明不是「全量缓冲」）
    collected: list = []
    seen_before_release = False
    deadline = time.time() + 12
    while time.time() < deadline:
        try:
            got_key, got_val = q.get(timeout=2)
        except queue.Empty:
            continue
        if got_key == "end":
            break
        if got_key == "error":
            pytest.fail(f"客户端读流出错: {got_val}")
        collected.append(got_val)
        if isinstance(got_val, str) and "assistant_delta" in got_val:
            seen_before_release = True
            break
    assert seen_before_release, (
        "release 前未收到任何分块 → 代理疑似全量缓冲（SSE 未逐块透传）")

    # 3) 放行剩余分块，收全
    STUB.release.set()
    while True:
        got_key, got_val = q.get(timeout=15)
        if got_key == "end":
            break
        if got_key == "error":
            pytest.fail(f"客户端读流出错: {got_val}")
        collected.append(got_val)
    thread.join(timeout=10)

    joined = "\n".join(str(x) for x in collected)
    assert "id: 6" in joined and "id: 7" in joined and "id: 8" in joined, joined
    assert "task_end" in joined and "你好" in joined, joined
    # 上游 Bearer 也应在本次 SSE 请求上被注入
    rec = [r for r in STUB.received if r["path"] == "/api/events"]
    assert rec and rec[-1]["headers"].get("authorization") == "Bearer tok-abc-123"
    assert rec[-1]["headers"].get("last-event-id") == "5"


def test_upstream_unreachable_returns_json_502_503(proxy_server, monkeypatch):
    """上游不可达 → 明确 502/503 + JSON（不是 500，也不是 HTML/traceback）。"""
    dead = f"http://127.0.0.1:{_free_port()}"  # 立即关闭的空闲端口 ⇒ 连接被拒
    monkeypatch.setenv("AGENT_BASE_URL", dead)
    monkeypatch.setenv("AGENT_TOKEN", "tok-abc-123")

    resp = httpx.get(f"{proxy_server}/api/agent/api/health", timeout=15,
                     trust_env=False)
    assert resp.status_code in (502, 503), f"实际 {resp.status_code}"
    assert resp.headers.get("content-type", "").startswith("application/json")
    body = resp.json()
    assert "error" in body and "detail" in body


def test_invalid_upstream_path_is_json_not_500(proxy_server, stub_server, monkeypatch):
    """非法路径（含 CR/LF，%0d%0a）→ JSON 400/502，**绝不 500**（F1）。

    根因：httpx.InvalidURL 不继承 RequestError/HTTPError，非法路径在 build_request()
    处抛出后若不显式捕获，会穿透成 500（违反「绝不穿成 500」）。
    """
    monkeypatch.setenv("AGENT_BASE_URL", stub_server)
    monkeypatch.setenv("AGENT_TOKEN", "tok-abc-123")

    resp = httpx.get(f"{proxy_server}/api/agent/foo%0d%0a", timeout=15,
                     trust_env=False)
    assert resp.status_code != 500, f"不得穿成 500（实际 {resp.status_code}）"
    assert resp.status_code in (400, 502), f"实际 {resp.status_code}"
    assert resp.headers.get("content-type", "").startswith("application/json"), \
        f"必须是 JSON，实际 {resp.headers.get('content-type')!r}"
    body = resp.json()
    assert "error" in body and "detail" in body


# ===========================================================================
# T10-a：出站脱敏（走**真实 router**，观测上游实际收到什么）
# ---------------------------------------------------------------------------
# 说明：为了**不改动**既有 stub（module 级 `_make_stub_app`），这里另起一个
# 自包含的 stub，它会**记录请求体**（既有 stub 只记头）。测试用 monkeypatch 把
# `AGENT_BASE_URL` 指向本 stub，复用既有 module 级 `proxy_server` fixture。
# ===========================================================================

from urllib.parse import quote, unquote  # noqa: E402  （追加段专用，置底不影响既有用例）


class _CtxStubState:
    """记录上游收到的请求（含**原始请求体**）。"""

    def __init__(self) -> None:
        self.received: list = []


CTX_STUB = _CtxStubState()


def _make_ctx_stub_app() -> FastAPI:
    """自包含 stub：只提供 POST /api/chat，记录小写化头 + 原始 body。"""
    stub = FastAPI()

    @stub.post("/api/chat")
    async def chat(request: Request):  # noqa: ANN202
        raw = await request.body()
        CTX_STUB.received.append({
            "path": "/api/chat",
            "method": request.method,
            "headers": {k.lower(): v for k, v in request.headers.items()},
            "body": raw,
        })
        return {"ok": True}

    return stub


@pytest.fixture(scope="module")
def ctx_stub_server():
    """记录请求体的 stub 上游（真实 uvicorn）。"""
    url, server, thread = _serve(_make_ctx_stub_app())
    yield url
    _shutdown(server, thread)


@pytest.fixture(autouse=True)
def _reset_ctx_stub():
    """每个用例前后清空 CTX_STUB 记录。"""
    CTX_STUB.received.clear()
    yield


def test_plat_context_sanitized_whitelist_and_ascii(proxy_server, ctx_stub_server,
                                                    monkeypatch):
    """① 带 `X-Plat-Context`（含未知键 + 手机号）→ 上游收到的是白名单 6 键 + 掩码 + 纯 ASCII。

    模拟前端的 percent-encode 头；上游读到的应是 BFF 重新编码后的白名单 JSON。
    """
    monkeypatch.setenv("AGENT_BASE_URL", ctx_stub_server)
    monkeypatch.setenv("AGENT_TOKEN", "tok-abc-123")

    ctx = {
        "page": "alerts",
        "page_label": "加急预警",
        "category": "手机膜",
        "batch": "x",
        "owner": "张三",              # 未知键（且敏感）→ 应被剔除
        "phone": "13812345678",       # 未知键 → 应被剔除
        "unknown": "y",               # 未知键 → 应被剔除
        "filters": {"a": 1, "nest": {"z": 2}},   # 只保留扁平标量
    }
    raw_header = json.dumps(ctx, ensure_ascii=False)
    enc_header = quote(raw_header, safe="")       # 与前端 encodePlatContext 等价

    resp = httpx.post(
        f"{proxy_server}/api/agent/api/chat",
        headers={"X-Plat-Context": enc_header, "Content-Type": "application/json"},
        content=json.dumps({"session": "s1", "message": "hi"}).encode("utf-8"),
        timeout=10, trust_env=False)
    assert resp.status_code == 200

    rec = CTX_STUB.received[-1]
    got = rec["headers"].get("x-plat-context")
    assert got is not None, "上游未收到 X-Plat-Context"
    assert got.isascii(), f"头值必须纯 ASCII，实际含非 ASCII: {got!r}"

    obj = json.loads(unquote(got))
    assert set(obj.keys()) <= {"page", "page_label", "category", "batch",
                               "selected_sku", "filters"}, obj
    assert "owner" not in obj and "phone" not in obj and "unknown" not in obj
    assert obj["page"] == "alerts"
    assert obj["page_label"] == "加急预警"
    assert obj["category"] == "手机膜"
    assert obj["filters"] == {"a": "1"}            # 嵌套 nest 被丢


def test_plat_context_phone_in_whitelisted_value_masked(proxy_server,
                                                        ctx_stub_server, monkeypatch):
    """上下文里**白名单键的值**含手机号 → 掩码（证明值级掩码生效）。"""
    monkeypatch.setenv("AGENT_BASE_URL", ctx_stub_server)
    monkeypatch.setenv("AGENT_TOKEN", "tok-abc-123")

    raw_header = json.dumps({"page": "records", "batch": "13812348000"},
                            ensure_ascii=False)
    resp = httpx.post(
        f"{proxy_server}/api/agent/api/chat",
        headers={"X-Plat-Context": quote(raw_header, safe=""),
                 "Content-Type": "application/json"},
        content=json.dumps({"session": "s1", "message": "hi"}).encode("utf-8"),
        timeout=10, trust_env=False)
    assert resp.status_code == 200

    got = CTX_STUB.received[-1]["headers"].get("x-plat-context")
    obj = json.loads(unquote(got))
    assert obj["batch"] == "138****8000"


def test_absent_plat_context_not_forwarded(proxy_server, ctx_stub_server, monkeypatch):
    """不带 `X-Plat-Context` → 上游**不应**收到该头（向后兼容，不凭空造头）。"""
    monkeypatch.setenv("AGENT_BASE_URL", ctx_stub_server)
    monkeypatch.setenv("AGENT_TOKEN", "tok-abc-123")

    resp = httpx.post(f"{proxy_server}/api/agent/api/chat",
                      json={"session": "s1", "message": "hi"}, timeout=10,
                      trust_env=False)
    assert resp.status_code == 200
    assert "x-plat-context" not in CTX_STUB.received[-1]["headers"]


def test_chat_body_phone_masked_at_bff(proxy_server, ctx_stub_server, monkeypatch):
    """② `/api/chat` body 里的手机号经 BFF 后被掩码（上游收到的是掩码后的 body）。"""
    monkeypatch.setenv("AGENT_BASE_URL", ctx_stub_server)
    monkeypatch.setenv("AGENT_TOKEN", "tok-abc-123")

    payload = {"session": "s1", "message": "联系我 13812348000 谢谢"}
    resp = httpx.post(f"{proxy_server}/api/agent/api/chat",
                      headers={"Content-Type": "application/json"},
                      content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      timeout=10, trust_env=False)
    assert resp.status_code == 200

    body = json.loads(CTX_STUB.received[-1]["body"].decode("utf-8"))
    assert body["session"] == "s1"
    assert "13812348000" not in body["message"]
    assert "138****8000" in body["message"]


def test_chat_body_sensitive_key_dropped(proxy_server, ctx_stub_server, monkeypatch):
    """body 里命中敏感词典的键（如 `owner`）被整键剔除。"""
    monkeypatch.setenv("AGENT_BASE_URL", ctx_stub_server)
    monkeypatch.setenv("AGENT_TOKEN", "tok-abc-123")

    payload = {"session": "s1", "message": "hi", "owner": "张三", "sku": "S1"}
    resp = httpx.post(f"{proxy_server}/api/agent/api/chat",
                      headers={"Content-Type": "application/json"},
                      content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      timeout=10, trust_env=False)
    assert resp.status_code == 200

    body = json.loads(CTX_STUB.received[-1]["body"].decode("utf-8"))
    assert "owner" not in body
    assert body["sku"] == "S1"


def test_non_json_body_passthrough_at_bff(proxy_server, ctx_stub_server, monkeypatch):
    """③ 非 JSON body → 原样转发（绝不改写非 JSON）。"""
    monkeypatch.setenv("AGENT_BASE_URL", ctx_stub_server)
    monkeypatch.setenv("AGENT_TOKEN", "tok-abc-123")

    raw = "手机号 13812345678".encode("utf-8")
    resp = httpx.post(f"{proxy_server}/api/agent/api/chat",
                      headers={"Content-Type": "text/plain"},
                      content=raw, timeout=10, trust_env=False)
    assert resp.status_code == 200
    assert CTX_STUB.received[-1]["body"] == raw


