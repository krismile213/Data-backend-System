# AI 助手 · 本轮实现契约（T10 出站脱敏 + P1 上下文注入）

> 上游依据：[`AI助手嵌入实现方案.md`](./AI助手嵌入实现方案.md) §9（决策 2）、§9.1（落地要求）、§10（T10/T05 行）；
> [`Agent侧对接任务书.md`](./Agent侧对接任务书.md) §4.1（上下文字段约定）、§5.3（Agent 侧白名单）。
> 本文件是**实现契约**（可测、可核对），不是方案讨论。

---

## 0. 真实数据流向（先纠一条方案里的说法）

方案 §9.1 写「所有出站内容（上下文头 + 用户消息 + **工具结果回灌**）都经 BFF」——**这一句不成立**，必须先纠正，否则会做出错误的收口：

```
① 平台页面 → 前端 → [BFF] → Agent → 云端模型 z.ai      ← 上下文头 + 用户消息（上行，BFF 收口 ✅）
② 平台 API(:8000) ← Agent 工具(platform_*)              ← 本机，不出网
③ Agent(工具结果) → 云端模型 z.ai                        ← 工具结果回灌（下行，**不经过 BFF** ❌）
④ Agent → [BFF] → 前端浏览器                             ← 同机，无出网风险
```

| 方向 | 内容 | 谁在收口 | 现状 |
|---|---|---|---|
| ① 上行 | 用户消息 + `X-Plat-Context` | **BFF `_sanitize_outbound()`** | 骨架直通 → **本轮实装** |
| ② 本机 | 平台数据 → Agent | 无需（不出网） | — |
| ③ 下行到云端 | 工具结果 | **Agent 侧 `_plat_sanitize()`**（`workmain.py:616`：词典剔键 + 手机号掩码） | ✅ 已实装（T06 §5.3 双保险） |
| ④ 同机 | SSE 流 → 浏览器 | 无需 | — |

**结论**：本轮平台侧只需收口 **① 上行**；**③ 已在 Agent 侧就位，本轮不动 Agent 仓库**。任何"在 BFF 里洗工具结果"的写法都是错的（那段数据根本不经过 BFF）。

---

## 1. 范围

**做**：T10-a BFF 强制脱敏（上行）· T10-b 前端出网告知确认 · P1 前端发 `X-Plat-Context`。
**不做**：不改 Agent 仓库；不做 `selected_sku` 全局注册表；不做会话侧栏（T05 剩余）；不动 T06 工具；**前后端均零新增依赖**。

---

## 2. T10-a 契约：`backend/app/outbound_guard.py`（新，纯函数、零依赖）

### 2.1 公开 API

```python
SENSITIVE_KEY_SUBSTR: tuple[str, ...]   # 与 Agent 侧 workmain.py:_SENS_SUBSTR 对齐（至少为超集）
CONTEXT_ALLOWED_KEYS = ("page", "page_label", "category", "batch", "selected_sku", "filters")
CONTEXT_VALUE_MAX   = 80     # 单个上下文值最大字符数，超出截断
CONTEXT_HEADER_MAX  = 2048   # 编码后头值最大长度，超出整体丢弃

def sanitize_obj(obj) -> tuple[object, dict]:
    """递归脱敏 → (清洗后的对象, stats)。stats = {"dropped_keys":int,"masked_values":int,"changed":bool}"""

def sanitize_context(value: str | None) -> tuple[str | None, dict]:
    """X-Plat-Context 头值 → (可安全转发的已编码头值 | None, info)。info 含 "reason"/stats。"""

def sanitize_request_body(body: bytes, content_type: str) -> tuple[bytes, dict]:
    """出站请求体 → (可安全转发的字节, stats)。仅 application/json 且可解析时才改写。"""
```

**只允许依赖标准库**（`json`/`re`/`urllib.parse`）。**禁止** import 本项目其它模块（防循环依赖，与 `env_loader.py` 同一纪律）。

### 2.2 行为表（逐条可测）

| # | 输入 | 期望 | 说明 |
|---|---|---|---|
| 1 | dict 含键名命中 `SENSITIVE_KEY_SUBSTR`（如 `当前负责人`、`owner`、`备注`） | **整键剔除** | 大小写不敏感（`str(k).lower()`） |
| 2 | 嵌套 dict / list / list[dict] | 递归同样处理 | 深度不设限 |
| 3 | 字符串值含 11 位手机号 | 掩码为 `138****8000` | 前后不夹数字（`(?<!\d)…(?!\d)`） |
| 4 | 其它类型（int/float/bool/None） | 原样 | 不改数值 |
| 5 | 顶层非 dict（如 list） | 仍递归处理 | — |
| 6 | 上下文头缺失/空串 | 返回 `(None, {"reason":"absent"})`，**不添加该头** | 向后兼容 |
| 7 | 上下文为合法 JSON（ASCII） | 只保留 `CONTEXT_ALLOWED_KEYS` | 未知键计入 `dropped_keys` |
| 8 | 上下文为 percent-encoded JSON | 先 `unquote` 再解析 | 两种都兼容（与 Agent 侧一致） |
| 9 | 上下文值非字符串 | 强转 `str` | 不抛 |
| 10 | 单值长度 > `CONTEXT_VALUE_MAX` | 截断 | 计入 stats |
| 11 | `filters` 非 dict | 丢弃该键 | 只收「扁平标量」`filters`，嵌套一律丢 |
| 12 | 编码后 > `CONTEXT_HEADER_MAX` | 整体返回 `None` | 防止超长头 |
| 13 | 上下文解析失败/畸形 | `(None, {"reason":"malformed"})`，**不抛** | 行为与"没带"一致 |
| 14 | 请求体 `content-type` 非 JSON | **原样返回**，理由记入 stats | 绝不改写非 JSON |
| 15 | 请求体是 JSON 但解析失败 | 原样返回 | 绝不抛 |
| 16 | 请求体 JSON 解析成功 | 走 `sanitize_obj` 后 `ensure_ascii=False` 重序列化为 UTF-8 | 中文不转义 |
| 17 | 编码后头值 | **必须纯 ASCII**（`percent-encoding` 保证） | 否则 httpx 转发会抛 `UnicodeEncodeError` |

> 硬要求：**任何路径都不得抛异常**。异常 = 破坏助手可用性，等于把安全做成了故障源。

### 2.3 接入点：`backend/app/routers/agent_proxy.py`

- `_sanitize_outbound(body, headers)` **签名与返回值不变** `-> (body, headers)`，调用点（第 109 行）不改。
- content-type 从 `headers` 里取（`_build_upstream_headers` 只过滤逐跳头，`content-type` 会被保留）——**不要**改函数签名。
- 顺序：先 `sanitize_request_body(body, ct)`，再 `sanitize_context(headers.get("x-plat-context"))` →
  有值则 `headers["X-Plat-Context"] = <编码值>`，为 `None` 则 `headers.pop("X-Plat-Context", None)`。
- 命中任何脱敏时 `print("[agent-guard] ...", flush=True)` 打一行**摘要**（键名/计数/理由）——**禁止打印明文值**。日志用 `print`（与 `prewarm.py:130` 一致，本项目无 logging 约定）。

---

## 3. P1 契约：前端上下文注入

### 3.1 `frontend/src/agentContext.js`（新，**纯 JS**：无 JSX、不 import MUI）

> 必须能在 node 里直接 `import`（供 `scripts/test_agent_context.mjs` 跑），理由同 `hooks/sseParser.js`。

```js
export const CONTEXT_HEADER = 'X-Plat-Context';
export function buildPlatContext({ page, pageLabel, category, batch, selectedSku }) -> object
export function encodePlatContext(ctx) -> string   // encodeURIComponent(JSON.stringify(ctx))；异常返回 ''
export function scanSensitiveText(text) -> { hit: boolean, kinds: string[] }
```

- `buildPlatContext` 输出形状**严格对齐**任务书 §4.1：
  `{ page, page_label, category, batch, selected_sku, filters: {} }`；缺省一律空串；每值 trim 且 ≤ `CONTEXT_VALUE_MAX`。
- `encodePlatContext` 保证**纯 ASCII**（中文 percent-encode）。**只编码一次**（调用方不得二次 `encodeURIComponent`）。
- `scanSensitiveText`：正则检测 11 位手机号 / 邮箱 / 18 位身份证 / 关键词（`负责人|备注|手机|电话|联系方式`）→ 返回命中种类（用于弹窗文案）。

### 3.2 `frontend/src/api.js`

- `agentRequest(path, { method, body, headers })`：**新增可选 `headers`** 并合并；不传时行为与现状完全一致。
- `sendAgentChat({ session, message, reflect, plan, stepwise, platContext })`：提供 `platContext` 时加
  `[CONTEXT_HEADER]: encodePlatContext(platContext)`。
- `enqueueAgent(session, text, platContext)`：同上（Agent 目前在 `/api/chat` 读该头，带上无害且向前兼容）。

### 3.3 `frontend/src/App.jsx`

- 新增 `getPlatContext()`（用 `useCallback`）：取当前 `page / category / batch` 三个 state + 从本文件既有的 `PAGES` 取 `page_label`（**不得另建一份标签表**——那就是口径漂移）+ 读 `window.location.search` 的 `?sku=` 作为 `selected_sku`（现在恒为空，见 §6）。
- 传给 `<AgentPanel open onClose getPlatContext={getPlatContext} />`。
- ⚠️ 切品类会整页 reload（既有行为）→ 上下文天然跟随。

### 3.4 `frontend/src/components/AgentPanel.jsx`

- 发送路径（`handleSend` 与 enqueue 分支）取 `getPlatContext?.()` → 传给 `sendAgentChat` / `enqueueAgent`。

---

## 4. T10-b 契约：前端出网告知确认（**UX 层，不是安全边界**）

> 必须在代码注释与文档里写死这句话，否则会被误读成"前端检测能防泄漏"。真正的强制保障 = §2 BFF 脱敏 + Agent 侧白名单。

- 保留既有首次打开横幅（`showNotice` / `NOTICE_KEY`，文案「回答由云端模型生成，助手可能收到当前页面信息；敏感字段将被屏蔽。」）。
- 新增：`handleSend` 发送前 `scanSensitiveText(text)` 命中 → 弹 MUI `Dialog`：
  标题「内容可能包含个人信息」；正文「检测到：{kinds}。本次消息将发送至**云端模型**（z.ai）处理。是否继续？」
  按钮：`取消`（默认，丢弃不发）/ `仍然发送`。
- 未命中 → 直接发送，**不打扰**（不做每轮拦截）。
- 用户点「仍然发送」后可考虑**记住本次会话**（可选，不做也行）。

---

## 5. 测试要求（**必须贴原始输出**）

| 文件 | 内容 |
|---|---|
| `tests/test_outbound_guard.py`（新） | §2.2 行为表 **逐条**至少一例；含反例（畸形 JSON / 非 JSON body / 超长头 / 非 dict 顶层）与"不抛异常"断言 |
| `tests/test_agent_proxy.py`（**增补**，不改既有用例） | 走真实 router：① 带 `X-Plat-Context`（含未知键 + 手机号）→ 上游收到的头是白名单 + 掩码 + 纯 ASCII；② `/api/chat` body 里手机号被掩码；③ 非 JSON body 原样 |
| `frontend/scripts/test_agent_context.mjs`（新，零依赖 node 跑） | ≥8 例：未知键不进头 / 中文正确 percent-encode 且 `decodeURIComponent` 能还原 / 值截断 / `page_label` 与 App 一致 / `scanSensitiveText` 命中与不命中 |
| `backend/app/outbound_guard.py` 自身 | 仅标准库；无本项目 import（`grep` 自证） |

**回归硬指标**：`pytest tests/ -q` **不得低于本机基线 444 passed / 2 skipped**（新增用例后总数应更高）。
跑测试前设 `TMPDIR/TEMP/TMP`；解释器用受管 venv（`C:/Users/dell/.workbuddy/binaries/python/envs/default/Scripts/python.exe`）。
前端语法自查：esbuild `transformSync(loader:'jsx')` 过一遍改动文件（**本项目无 JS 测试框架，禁 `npm run build`**）。

---

## 6. 已知缺口（本轮明确不做，写进 memory 免得下轮忘）

- **`selected_sku` 恒为空**：全前端无"全局选中 SKU"状态，各页把选中项留在组件局部 state（如 `InflightDaily.selSku`），**没有任何页面把 sku 写进 URL**。要真正填上需建「选中 SKU 注册表」并让各页 publish —— 单独立项（T05b）。
  ⇒ 本轮上下文**实际生效的是 `page / page_label / category / batch`**，这已覆盖 P1 验收标准 ①。
- `filters` 恒为空对象（同上，属 T05b）。
- Agent 侧 `permission_request` 事件已带 `level`，`ApprovalCard` 仍在用启发式判写级 → 小项，另轮。

---

## 7. 验收判据（做完应当能演示）

1. `pytest tests/ -q` ≥ 444 passed / 2 skipped；`node scripts/test_agent_context.mjs` 全 PASS。
2. 真机：起 `:8000` + Agent `:8765`，前端 dev 打开抽屉问「我现在在哪个页面、当前品类是什么？」
   → 答案反映**真实当前页**（不是我在探针里手塞的值）——**这是 T04 全链路首次真实生效**。
3. 真机：把 `X-Plat-Context` 手工塞成含 `{"page":"alerts","page_label":"加急预警","category":"手机膜","batch":"x","owner":"张三","phone":"13812345678"}`，
   经 BFF 后上游收到的是「白名单 6 键 + `owner` 已剔除 + 手机号已掩码」。
4. 在面板输入含 11 位手机号的文本 → 弹确认框；取消则不发送。
