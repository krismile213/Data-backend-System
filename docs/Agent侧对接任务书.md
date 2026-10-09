# Agent 侧对接任务书

> **交付对象**：Agent 项目开发者（`C:\Users\dell\Desktop\Agent`）
> **来源**：平台「AI 助手嵌入」项目已确认方案，从中抽出 **Agent 侧工作**，单独派发。
> **本文档自包含**：可脱离方案原文直接照着写代码。每处涉及 Agent 代码的断言均带 `文件:行号`。
> **决策已定**（无需再讨论）：拓扑=BFF 反代；数据=分级授权+脱敏+告知；权限=P2 全程只读；鉴权=BFF 注入静态 Bearer；会话=按用户一个助手会话、上下文随屏每轮注入。

---

## 1. 一页速览

**背景一句话**：平台（数据后台系统）要在前端加一个「AI 助手」抽屉，把 Agent（你的通用 agent）嵌进去；平台后端作 BFF 反向代理，浏览器**从不直连** Agent 端口。

**调用关系图**

```
浏览器（平台 React 抽屉面板）
   │  同源 fetch，相对路径 /api/agent/*
   ▼
平台 FastAPI :8000  ── /api/agent/* 反向代理，服务端注入 Authorization: Bearer <静态token>
   │  http://127.0.0.1:8765/api/*   （SSE 流式透传，保 X-Accel-Buffering: no）
   ▼
Agent server.py :8765  ── agentcore 引擎；写级工具走审批闭环
```

> 关键：Agent 端口 `127.0.0.1:8765` **只被平台后端访问**，浏览器不接触；因此 **Agent 侧不需要开 CORS**（见 §3.3）。

**本任务书要 Agent 侧交付 3 件事**

| 编号 | 阶段 | 一句话 | 主要产出 |
|------|------|--------|----------|
| **T01** | P0 | 供给面就绪：静态 token + 契约冻结 + 健康检查确认 | `config.json` 改 `auth.tokens`；契约登记 |
| **T04** | P1 | 支持平台上下文注入（读 `X-Plat-Context` 头） | `server.py` + `agentcore.py` 改点；`AGENT.md` |
| **T06** | P2 | 领域只读工具：对接平台后端 API | `plugins/workmain.py` 增 10 个 `platform_*` 工具 |

---

## 2. 契约快照（Agent 侧必须保持稳定、不得破坏）

> 这是平台侧会长期依赖的**对外契约**。你可以**新增**，但**不得删改**已列端点/帧格式/响应头/鉴权方式。

### 2.1 端点清单（须保持向后兼容）

| 方法 | 路径 | 用途 | 请求体/参数 | 响应 | 行号 |
|------|------|------|-------------|------|------|
| POST | `/api/chat` | 发消息（结果走 SSE） | `{session, message, reflect=false, plan=false, stepwise=false}` | `{ok:true}`；400 空/409 运行中/503 并发满 | `server.py:463-510`（body `343-349`） |
| GET | `/api/events` | SSE 事件流 | `?session=` | `text/event-stream` | `server.py:831-865` |
| POST | `/api/approve` | 单条审批 | `{session, id, approve, always=false, text=""}` | `{ok:true}`；404 | `server.py:581-595`（body `351-356`） |
| POST | `/api/approve_batch` | 批量审批 | `{session, ids:[...], approve, text=""}` | `{ok, resolved:[], missing:[]}` | `server.py:605-624` |
| GET | `/api/pending` | 未决审批清单 | `?session=` | `{items:[{id,tool,preview}]}` | `server.py:627-632` |
| POST | `/api/stop` | 停止运行中任务 | `{session}` | `{ok, stopping}` | `server.py:563-578` |
| POST | `/api/enqueue` | 运行中追加指令 | `{session, text}` | `{ok:true}`；400 | `server.py:640-653` |
| GET | `/api/interrupted` | 中断残留（可续跑） | — | `{items:[...]}` | `server.py:521-536` |
| POST | `/api/resume` | 续跑中断任务 | `{session}` | `{ok:true}`；404 | `server.py:543-553` |
| POST | `/api/resume/discard` | 放弃中断任务 | `{session}` | `{ok:true}` | `server.py:556-560` |
| GET | `/api/sessions` | 会话列表 | — | `[{name,size,mtime}]` | `server.py:385-428` |
| GET | `/api/sessions/{name}/history` | 会话历史 | — | `{items:[...],usage,running}` | `server.py:431-456` |
| DELETE | `/api/sessions/{name}` | 删除会话 | — | `{ok:true}`；404/409 | `server.py:772-790` |
| POST | `/api/sessions/{name}/rename` | 重命名会话 | `{new}` | `{ok,name}`；400/404/409 | `server.py:751-769` |
| GET | `/api/health` | 健康检查 | — | `{status:"ok", model, sessions, running}` | `server.py:793-798` |

（另有 `/api/login` `server.py:313`、`/api/logout` `326`、`/api/me` `333`、文件类 `664-740`、`/api/metrics` `801-828` —— 平台暂时不用，但请保持可用。）

### 2.2 SSE 帧格式与响应头（**冻结项**）

- 帧格式必须保持：`id: <seq>\ndata: <json>\n\n`（`server.py:841-842`）。
- 文本增量事件 `assistant_delta` **不入重放日志**（`server.py:245-249`）——保持此行为，重连后由完整 `assistant` 事件复原。
- 响应头必须保留：`media_type="text/event-stream"` + `Cache-Control: no-cache` + **`X-Accel-Buffering: no`**（`server.py:863-865`）。
- 必须继续支持 `Last-Event-ID` 重放：读取请求头 → 重放 `evlog` 中 `seq > last_id` 的事件（`server.py:835,849-852`），每会话保留窗口 `EVLOG_MAX=500`（`server.py:47`）。
- `/api/events` 首帧为**无 `id` 的 `hello`**（`server.py:846-848`）。

### 2.3 鉴权（**冻结项**）

- 必须继续支持 `Authorization: Bearer <token>`：命中 `auth.tokens` 集合即视为已登录（`server.py:114-121`）。
- Cookie 登录（HMAC 签名、`httponly, samesite=lax`，`server.py:321-322`）保持不变。
- `users/tokens` 全空 = 本机免登录（`server.py:71`）——**用于平台场景时请务必配 `auth.tokens`**（见 §3.1）。

### 2.4 全部 SSE 事件名 + payload schema（BFF/前端按此解析，**原样搬运**）

**引擎事件（`agentcore.py`）**

| `kind` | payload 字段 | 触发 | 行号 |
|--------|--------------|------|------|
| `task_start` | `{task}` | 任务开始 | `agentcore.py:1275` |
| `assistant_delta` | `{text}` | 流式增量（**不入重放日志**） | `agentcore.py:1307` / `server.py:248` |
| `assistant` | `{text}` | 一段完整助手文本 | `agentcore.py:1317` |
| `tool_call` | `{name, args}`（args=截断 JSON 串） | 发起工具调用 | `agentcore.py:1342` |
| `tool_result` | `{name, result}`（已截断） | 工具返回 | `agentcore.py:1366` |
| `permission_denied` | `{name}` | 策略拒绝 | `agentcore.py:1358` |
| `injection_suspected` | `{name, marker}` | 疑似提示注入 | `agentcore.py:1365` |
| `compact` | `{tokens, dropped}` | 上下文压缩 | `agentcore.py:1100` |
| `instruction_injected` | `{text}` | 追加指令被注入 | `agentcore.py:1217` |
| `reflect` | `{critique}` | 反思结论 | `agentcore.py:1423` |
| `fix_round` | `{critique}` | 反思修正轮 | `agentcore.py:1458` |
| `plan` | `{plan}` | 计划文本 | `agentcore.py:1515` |
| `plan_extra` | `{extra}` | 计划附加要求 | `agentcore.py:1532` |
| `plan_approved` | `{}` | 计划获批 | `agentcore.py:1533` |
| `plan_rejected` | `{plan}` | 计划被拒 | `agentcore.py:1512,1522` |
| `steps` | `{steps}` | 分步列表 | `agentcore.py:1537` |
| `step_done` | `{step, total, text}` | 单步完成 | `agentcore.py:1552` |
| `plan_stopped` | `{at, total}` | 计划中途停止 | `agentcore.py:1541,1561` |
| `task_end` | `{answer, usage}` | 任务结束（usage=累计 token） | `agentcore.py:1296,1326` |
| `cancelled` | `{}` | 被取消 | `agentcore.py:1294,1375` |
| `max_turns` | `{}` | 超最大轮次 | `agentcore.py:1381` |
| `fatal` | `{message}` | 不可重试错误 | `agentcore.py:1311` / `server.py:501` |

**Web 适配器事件（`server.py`）**

| `kind` | payload 字段 | 触发 | 行号 |
|--------|--------------|------|------|
| `hello` | `{kind:"hello", tools:[...], model, root}` | `/api/events` 首帧（**无 id**） | `server.py:846-848` |
| `running` | `{value:true}` / `{value:false, usage}` | 任务起/止 | `server.py:474,504-505` |
| `permission_request` | `{id, tool, level, preview}` **【实现阶段新增 `level`】** | 写级工具待审批（**渲染审批卡**） | `server.py:154-156` |
| `permission_resolved` | `{id, approved}` | 审批被解决 | `server.py:577,594,623` |
| `plan_request` | `{id, plan, level}` **【实现阶段新增 `level`】** | 计划待审批 | `server.py:192-193` |
| `step_request` | `{id, step, total, text, level}` **【实现阶段新增 `level`】** | 分步待确认 | `server.py:215-217` |
| `instruction_queued` | `{text}` | 追加指令入队 | `server.py:652` |

> 新增事件可随时加；但**不要改动上表事件的字段名/语义**，否则前端解析会断。

> **【实现阶段新增 · 契约扩展】** `permission_request` / `plan_request` / `step_request` 三个审批类事件**须补 `level` 字段**，取值 `"read" | "write" | "external"`（来源即引擎里的 `Tool.level`，见 `server.py:164-178`、`workmain.py` 各 `Tool(...)` 的 level 参数）。原因：平台侧 `ApprovalCard` 目前**拿不到工具级别**，只能用「工具名关键字」启发式猜是否 `external`，「本会话总允许」可能给错（平台 `frontend/src/components/ApprovalCard.jsx` 有对应 TODO）。补上后平台会**改为直读 `level`**。这是**平台侧依赖的契约扩展**，请务必落地；发事件处即 `server.py:154-156`、`192-193`、`215-217`（把 `tool.level` / 计划的级别一并放进 payload）。

---

## 3. T01 · 供给面就绪（P0）

### 3.1 `config.json` 增 `auth.tokens`（必做）

- 鉴权开关：`auth.users` / `users_sha256` / `tokens` **任一非空即开启**（`server.py:71`）。
- 平台 BFF 会用**静态 token**经 `Authorization: Bearer` 访问（`server.py:114-121`），因此请配置：
  ```jsonc
  {
    // config.json 顶层新增 a/auth 段（保留你现有的 base_url/api_key/model...）
    "auth": {
      "tokens": ["<请生成一个 32+ 字节随机串，例如 openssl rand -hex 32>"],
      "session_hours": 72,
      "secret": "<可留空；留空则重启后 Cookie 登录态失效，但静态 token 不受影响>"
    }
  }
  ```
  > 说明：`secret` 只影响 Cookie 登录态签名（`server.py:72-73`）；**静态 token 不依赖 secret**，BFF 正好只走静态 token，故 `secret` 可留空。

- **验收**：
  1. 带 `Authorization: Bearer <token>` 调 `GET /api/health` 返回 `200`（不带则 `401`）。
  2. 命令：`curl -H "Authorization: Bearer <token>" http://127.0.0.1:8765/api/health`。

### 3.2 `/api/health` 语义（确认，不改）

- 返回 `{status:"ok", model, sessions, running}`（`server.py:793-798`）；用于平台 BFF 探活与「助手是否在跑」。**保持字段不变**。

### 3.3 是否给 `server.py` 加 CORS（结论：**不需要，但建议加「默认关闭」的可配置白名单**）

- **结论**：平台走 BFF 同源代理，浏览器不直连 `:8765`，**因此本任务不需要 CORS**。
- **建议（可选，稳健性）**：加一个**默认关闭、可配置白名单**的 CORS，仅为将来异域/本地直连调试留后路。写法要点：
  ```python
  # server.py main() 里，仅在配置了非空白名单时才挂载
  allowed = (CFG.get("cors_allow_origins") or [])
  if allowed:
      from fastapi.middleware.cors import CORSMiddleware
      app.add_middleware(CORSMiddleware, allow_origins=allowed,
                         allow_credentials=True, allow_methods=["*"],
                         allow_headers=["*"])
  ```
  - **务必默认不挂载**（`cors_allow_origins` 缺省为空）；否则会改变现有安全姿态。
  - 注意 `AuthGate` 是纯 ASGI 且对 `OPTIONS` 放行但**不返回 CORS 头**（`server.py:288-302`）；若真开 CORS，需确保 `CORSMiddleware` 在 `AuthGate` **外层**（FastAPI `add_middleware` 后加先执行，顺序需实测）。
- **验收**：默认配置下，启动日志与行为与现状一致（无 CORS 头）；配置白名单后，`OPTIONS` 预检返回 200 且带 `Access-Control-Allow-Origin`。

### 3.4 【实现阶段新增】审批事件补 `level` 字段（平台侧依赖的契约扩展）

- **背景**：平台侧 `ApprovalCard` 收到 `permission_request` 时**拿不到工具级别**，只能用「工具名关键字」启发式猜是否 `external` ⇒ **「本会话总允许」可能给错**（平台 `frontend/src/components/ApprovalCard.jsx` 有 TODO）。
- **要求**：在 `permission_request`（`server.py:154-156`）、`plan_request`（`server.py:192-193`）、`step_request`（`server.py:215-217`）三个审批事件的 payload 中**补 `level` 字段**，取值 `"read" | "write" | "external"`，来源即引擎里的 `Tool.level`（`server.py:164-178`）。
- **落地后**：平台会把启发式**改为直读 `level`**（不再猜）。
- **契约位置**：同步登记到 §2.4 事件 schema（已标注）。
- **验收**：触发一次写级审批（让助手调用 `run_route_check`），`GET /api/events` 抓到的 `permission_request.data` 含 `level:"write"`；`external` 级工具（如钉钉发送）应为 `level:"external"`。

---

## 4. T04 · 上下文注入能力（P1）

**目标**：让助手「懂当前屏幕」。平台 BFF 在每轮请求带上 `X-Plat-Context` 请求头（JSON），Agent 在**任务起步**时把它注入给模型，且**不污染/不覆盖用户消息**。

### 4.1 注入内容（平台 BFF 下发，字段约定）

```jsonc
// 请求头 X-Plat-Context: <JSON 字符串>
{
  "page": "alerts",          // 平台当前页面 key（见 §4.4 页面字典）
  "page_label": "加急预警",
  "category": "手机膜",       // 品类：手机膜 | 手机壳
  "batch": "20261008",       // 当前批次名
  "selected_sku": "",        // 各页选中的 SKU（可为空）
  "filters": {}              // 该页临时筛选（可为空对象）
}
```

### 4.2 注入位置（**两个具体改点**）

**改点①：`server.py` 的 `/api/chat`（`server.py:463-510`）**
- 读取并解析请求头：
  ```python
  raw = request.headers.get("x-plat-context") or ""
  plat_ctx = {}
  if raw:
      try: plat_ctx = json.loads(raw)
      except Exception: plat_ctx = {}   # 解析失败静默忽略，绝不影响任务
  ```
- 把 `plat_ctx` 作为**新可选参数**传给引擎调用（`core.run_task(...)` / `core.plan_and_run(...)`，`server.py:482-494`），例如 `plat_ctx=plat_ctx`。

**改点②：`agentcore.py` 的 `run_task`（`1266-1282`）/ `plan_and_run`（`1492` 起）**
- 在 `run_task` 签名（`agentcore.py:1266-1269`）**新增可选参数** `plat_ctx: dict | None = None`（放在 `inject` 之后即可，保持向后兼容）。
- 注入位置：**紧接在 `history.append({"role":"user","content":task})`（`agentcore.py:1281`）之前**，插入一条独立 system 消息；即先注入 `[平台上下文]` 的 system，再 append 用户任务消息。`emit("task_start")` 在 `1275`，`write_checkpoint` 在 `1280`，都在其前后，注入逻辑不破坏它们。
- `plan_and_run`（`agentcore.py:1492`）同理传入并注入一次。
- **约束（硬要求）**：
  1. **不得覆盖/改写用户的 `message`**；用户消息文本原样保留。
  2. 上下文以 **system 角色**注入，标识清晰（如 `content` 以 `[平台上下文]` 开头）。
  3. **不得累积污染持久历史**：`SessionState.history` 已有固定 system（`server.py:232-233`）。本轮上下文应**临时注入**（用后即弃），或**每轮替换**同一条「平台上下文」消息；**不要**每轮 append 一条导致历史被上下文塞满。
  4. 上下文缺失/解析失败时**行为与现状完全一致**（向后兼容）。

### 4.3 注入格式（建议渲染模板）

```
[平台上下文] 用户当前在平台的「加急预警」页(page=alerts)；
品类=手机膜；当前批次=20261008；选中SKU=(无)。
回答涉及当前页/当前批次时，默认以此上下文为准；如用户显式给了别的批次/SKU，以用户为准。
```

### 4.4 `AGENT.md` 内容草案（放入 Agent 项目根目录，稳定知识，随版本维护）

> 这是**静态知识**（不随屏幕变）；动态的当前页/批次走 `X-Plat-Context`（§4.1），二者分工。

```markdown
# AGENT.md — 平台助手工作规范（稳定知识，非动态上下文）

## 角色
你是「新品售前流程数据后台系统」的 AI 助手，嵌入在平台前端抽屉里。

## 平台页面字典（page key → 页面）
- dashboard 时效看板；inflight 事中日报；review 复盘总览；assessment 事后评估
- gantt 流程甘特；scenarios 场景诊断；routes 路线达标；transport 货件运输
- alerts 加急预警；watch 联动提醒；fillins 手工填报；history 趋势周报
- compare 批次对比；deltas 单据增量；skus SKU档案；records 明细查询
- anomalies 异常排查；rules 规则配置；manual 操作手册；dataadmin 数据管理

## 品类与批次
- 品类固定两套：手机膜 / 手机壳；不同品类规则与批次不同，回答必须锁定当前 category。
- 批次由上下文给出；用户未指定时用当前批次。

## 工具使用规范
- 查平台真实数据，一律用 platform_* 工具（直连平台后端，唯一真源）。
- 不要自己读 xlsx 副本、不要自算指标；平台页面/后端数字才是准。

## 口径纪律（重要）
- 复盘两套口径不可混用：action(动作映射) 与 system(系统口径)，引用必须注明口径。
- 分段尺度、时效目标、加急阈值等，一律以平台后端为准，不得自行计算或假设。
- 数据新鲜度：若工具返回带「数据已 N 天未更新」告警，必须原样转达，不得忽略。

## 边界
- 当前权限为**只读**：不得请求或执行任何写平台数据的操作。
- 涉及个人信息（负责人姓名/手机号等）不得外发；工具已做字段白名单。
```

### 4.5 验收（可测）

1. BFF 带 `X-Plat-Context: {"page":"alerts","category":"手机膜","batch":"<真实批次名>"}` 调 `/api/chat` 问「**这一页最急的几单？**」→ 答案**体现手机膜+该批次**（而非泛泛或问「你要哪个批次」）。
2. 同一会话换页（改 header 的 `page/batch`）再问 → 答案**跟随新上下文**（证明是每轮注入而非一次固化）。
3. 不带 `X-Plat-Context` 头调用 → 行为与现状**完全一致**（向后兼容）。
4. 构造非法 JSON 头 → 不报错、正常回答（健壮性）。

---

## 5. T06 · 领域只读工具（P2）

在 `plugins/workmain.py` 增补 **10 个 `platform_*` 工具**（原 8 个工具 `workmain.py:526-582` 保留）。

### 5.1 四条硬性规则

1. **必须直连平台后端 `http://127.0.0.1:8000/api/*`**（唯一真源），**禁止再读 xlsx 副本**（现有 `query_batches/query_sku_route/...` 读 xlsx 的做法**不要**用于 platform_* 工具）。
2. **降级返回文本**：后端未启动 / 超时 / 非 200 / JSON 解析失败，一律**返回一句人类可读的错误文本**（供模型自愈），**不得抛异常中断任务**。建议统一形状：
   `[平台接口不可用] <工具名> 调用失败: <原因>；请稍后重试或改用平台页面确认。`
3. **字段级白名单**：工具返回值**只回必要字段**，**剔除负责人姓名 / 操作人 / 手机号 / 内部备注**——与平台侧 T10 脱敏做**双保险**（平台会脱敏，Agent 再脱一遍）。见 §5.3。
4. **【实现阶段新增 · 必须】绕过本机代理（`trust_env`）**：
   - **实测坑**：本机环境变量设有 **`HTTP_PROXY=http://127.0.0.1:63056`**（另有 `HTTPS_PROXY`）。`requests` / `httpx` **默认读环境变量代理**（`trust_env=True`），于是调 `http://127.0.0.1:8000/api/*` 会被**丢给那个代理** → 表现为**连不上、或被代理解析成它自己的 502**（看起来像「平台没起」，极难排查）。
   - 平台侧 BFF 已因踩此坑强制 `trust_env=False`（见 `backend/app/routers/agent_proxy.py:113-118` 注释），Agent 侧必须照做。
   - **要求**：platform_* 工具调用本机平台 API **必须**满足其一（推荐前两种）：
     - `requests`：`s = requests.Session(); s.trust_env = False`（每次请求用该 session）
     - `httpx`：`httpx.Client(trust_env=False)`
     - 或进程级 `NO_PROXY=127.0.0.1,localhost`（**注**：对已存在的代理配置更脆弱，优先前两种）
   - **自查提醒**：Agent 自身其它**面向本机**的出站调用（若有）也建议一并按同法排查——同一个坑。

### 5.2 工具完整 JSON schema（10 个）

> 统一约定：`level="read"`；参数 `category ∈ {"手机膜","手机壳"}`，缺省用上下文里的当前品类；全部 `required: []`（品类可空，缺省=上下文或手机膜）。
> 「返回形状」= 工具 `fn` 返回的**文本**应呈现给模型的**内容结构**（建议用键值/列表，别把整个 JSON 原样倾倒）。

```jsonc
// 1) platform_batches —— 列批次
{
  "name": "platform_batches",
  "description": "列出平台当前品类的所有数据批次（唯一真源）。问题涉及‘有哪些批次/换批次’时用。",
  "params": {"type":"object","properties":{
    "category":{"type":"string","enum":["手机膜","手机壳"]}
  },"required":[]},
  "level": "read",
  "backend": "GET http://127.0.0.1:8000/api/batches?category=<category>",
  "return_shape": "批次名列表（每行：批次名）；空则返回‘平台未返回批次’"
}

// 2) platform_overview —— 批次总览 KPI
{
  "name": "platform_overview",
  "description": "某批次的平台总览 KPI（单量/时效/异常概览）。‘这个批次整体怎么样’时用。",
  "params": {"type":"object","properties":{
    "batch":{"type":"string"},
    "category":{"type":"string","enum":["手机膜","手机壳"]}
  },"required":["batch"]},
  "level": "read",
  "backend": "GET http://127.0.0.1:8000/api/overview/{batch}?category=<category>",
  "return_shape": "KPI 键值对（数值 + 单位），按平台返回字段名列出"
}

// 3) platform_sku_route —— 单 SKU 路线/复盘现状
{
  "name": "platform_sku_route",
  "description": "某 SKU 在平台的路线达标/复盘现状（各 Part 实际vs目标、卡点、三部门终节点）。用户问‘某SKU卡在哪/进度如何’时用。",
  "params": {"type":"object","properties":{
    "sku":{"type":"string"},
    "caliber":{"type":"string","enum":["action","system"],"description":"复盘口径，默认 action"}
  },"required":["sku"]},
  "level": "read",
  "backend": "GET http://127.0.0.1:8000/api/review/sku/{sku}?category=<category>  (或 GET /api/routes)",
  "return_shape": "SKU + 口径 + 各段耗时/判定 + 卡点Part + 下一步（不返回负责人姓名）"
}

// 4) platform_alerts —— 加急预警
{
  "name": "platform_alerts",
  "description": "平台加急预警明细（P0/P1/P2 + 盯数清单 + 数据新鲜度）。用户问‘今天加急/最急的单’时用。",
  "params": {"type":"object","properties":{
    "category":{"type":"string","enum":["手机膜","手机壳"]},
    "priority":{"type":"string","enum":["P0","P1","P2"],"description":"可选，按优先级过滤"}
  },"required":[]},
  "level": "read",
  "backend": "GET http://127.0.0.1:8000/api/alerts?category=<category>[&priority=<priority>]",
  "return_shape": "按优先级分组：SKU / 型号 / 超期天数 / 卡点Part / 数据截至时间；不含负责人姓名"
}

// 5) platform_transport —— 货件运输
{
  "name": "platform_transport",
  "description": "平台货件运输看板（逐票判定 + 渠道/运输方式聚合）。运输闭环/海运空运相关问答用。",
  "params": {"type":"object","properties":{
    "category":{"type":"string","enum":["手机膜","手机壳"]}
  },"required":[]},
  "level": "read",
  "backend": "GET http://127.0.0.1:8000/api/transport?category=<category>",
  "return_shape": "运输方式/渠道聚合数 + 异常票数；逐票明细剔除内部备注"
}

// 6) platform_records —— 明细分页查询
{
  "name": "platform_records",
  "description": "平台明细分页查询（按批次+Part，可按审批号/创建人/状态/关键词过滤）。‘找某张单/某状态单据’时用。",
  "params": {"type":"object","properties":{
    "batch":{"type":"string"},"part":{"type":"string"},
    "page":{"type":"integer","minimum":1},"pageSize":{"type":"integer","minimum":1,"maximum":200},
    "approvalNo":{"type":"string"},"status":{"type":"string"},"keyword":{"type":"string"}
  },"required":["batch","part"]},
  "level": "read",
  "backend": "GET http://127.0.0.1:8000/api/records/{batch}/{part}?page=&pageSize=&approvalNo=&status=&keyword=&category=<category>",
  "return_shape": "分页头(总数/页码) + 行(SKU/审批号/状态/Part/更新时间)；**创建人/责任人姓名剔除**"
}

// 7) platform_inflight_overview —— 事中总览
{
  "name": "platform_inflight_overview",
  "description": "平台事中总览：在研 SKU 清单 + KPI。‘在研有哪些/事中整体情况’时用。",
  "params": {"type":"object","properties":{
    "category":{"type":"string","enum":["手机膜","手机壳"]}
  },"required":[]},
  "level": "read",
  "backend": "GET http://127.0.0.1:8000/api/inflight?category=<category>",
  "return_shape": "在研 SKU 清单 + KPI 键值（负责人姓名剔除）"
}

// 8) platform_inflight_alerts —— 事中风险预警
{
  "name": "platform_inflight_alerts",
  "description": "平台事中风险预警：当日新超期/持续超期/临近超期(85%)。用户问‘哪些在研要超期’时用。",
  "params": {"type":"object","properties":{
    "category":{"type":"string","enum":["手机膜","手机壳"]}
  },"required":[]},
  "level": "read",
  "backend": "GET http://127.0.0.1:8000/api/inflight/alerts?category=<category>",
  "return_shape": "三类分组：SKU / 型号 / 超期或临近天数；不含负责人姓名"
}

// 9) platform_daily_report —— 事中日报（只读列表）
{
  "name": "platform_daily_report",
  "description": "列出平台已生成的事中日报（按日期）。只读；不生成、不下载写操作。",
  "params": {"type":"object","properties":{
    "date":{"type":"string","description":"YYYY-MM-DD，可选；缺省返回最近若干份"}
  },"required":[]},
  "level": "read",
  "backend": "GET http://127.0.0.1:8000/api/inflight/daily-report?category=<category>",
  "return_shape": "日报日期列表；**不调用生成/下载接口**"
}

// 10) platform_deltas —— 单据增量
{
  "name": "platform_deltas",
  "description": "平台单据增量（未闭环 SKU 新增单据追踪）。用户问‘这周多了哪些单据/最近变化’时用。",
  "params": {"type":"object","properties":{
    "days":{"type":"integer","minimum":1,"description":"统计区间天数，默认30"},
    "sku":{"type":"string","description":"可选，限定某 SKU"}
  },"required":[]},
  "level": "read",
  "backend": "GET http://127.0.0.1:8000/api/deltas?days=&sku=&category=<category>",
  "return_shape": "新增单据/新动作/状态变化 摘要 + 未闭环 SKU 汇总（不含负责人姓名）"
}
```

> **注册方式**：沿用现有 `registry.register(Tool(name, description, params, "read", fn))`（参见 `workmain.py:526-582`）。建议新增一个内部模块或函数块（如 `_platform_tools()`）统一注册，便于维护。
> **HTTP 调用**：用 `requests`（内核已依赖，`agentcore.py:42-45`），设短超时（建议 `timeout=(3, 30)`）；后端地址**硬编码或读 `config.json` 的 `platform_root`（默认 `http://127.0.0.1:8000`）**。**必须 `trust_env=False`**（见 §5.1 规则 4，否则被本机代理劫持）——建议全局建一个 `s = requests.Session(); s.trust_env = False` 复用。

### 5.3 字段白名单（**双保险，必做**）

平台响应里可能出现 **负责人/操作人姓名、手机号、内部备注**（例如现有 `workmain.py:266-290` 的 `_latest_node` 会输出「操作人」）。platform_* 工具返回给模型前，**必须**：
- 剔除字段名含 `负责人 / 操作人 / 创建人 / 姓名 / 手机 / 电话 / 联系方式 / 备注 / remark / owner / creator / operator / phone / mobile` 的键；
- 对自由文本里形如 11 位手机号的片段做**掩码**（如 `138****8000`）；
- 只回 §5.2 各工具 `return_shape` 声明的字段。

> 理由：平台侧会脱敏，但 Agent 是数据出境（上云）的最后一跳，必须**再脱一遍**，防止平台侧漏项。

### 5.4 验收（可测）

1. 后端在跑时：问「**手机膜有哪些批次**」→ 返回的批次名与平台「时效看板」批次下拉**完全一致**。
2. 问「**G531 现在卡在哪**」→ 命中 `platform_sku_route`，卡点Part/耗时与平台「SKU 档案 / 路线达标」页一致。
3. 问「**手机膜今天最急的几单**」→ 命中 `platform_alerts`，与「加急预警」页一致。
4. **降级**：临时停掉平台后端（或改错端口），再问任一 platform_* 问题 → 得到「[平台接口不可用]...」文本，**任务不中断**（模型能继续对话，而非 `fatal`）。
5. **脱敏**：任一 platform_* 返回文本中**不含**完整人名/手机号（可 grep 校验）。
6. **只读**：全过程无 `permission_request`（无写工具触发）。
7. **【实现阶段新增】绕过本机代理**：在**保留** `HTTP_PROXY=http://127.0.0.1:63056` 环境变量的前提下，platform_* 工具仍能直连 `127.0.0.1:8000` 成功取数（即 `trust_env=False` 生效）；对照实验：临时去掉 `trust_env=False` 应复现「连不上/502」失败，加回则恢复。

---

## 6. 交付后如何自测（Agent 侧）

> 使用 Agent 项目既有测试体系（`scripts/run_all.py`、`eval/run_eval.py`，见 `C:\Users\dell\Desktop\Agent\scripts`、`eval`）。

| 项 | 命令/方式 |
|----|-----------|
| 回归（不破坏现状） | `python scripts/run_all.py`（`run_all.py` 汇总各 `test_*.py`） |
| 金标准评测 | `python eval/run_eval.py` |
| Web 相关 | 参考 `scripts/test_web_features.py`、`scripts/smoke_web.py`、`scripts/test_auth.py`、`scripts/test_human_intervention.py` |
| 建议**新增**用例 | ① `test_plat_context.py`：`/api/chat` 带/不带/非法 `X-Plat-Context` 的注入与向后兼容；② `test_platform_tools.py`：10 个 platform_* 工具的**降级返回**（后端不可用时返回文本不抛异常）+ **字段白名单/脱敏**断言（结果不含人名/手机号）；③ SSE 契约：帧格式 `id:`、`X-Accel-Buffering: no`、`Last-Event-ID` 重放（可扩 `test_web_features.py`）。 |
| 手动冒烟 | 起 `python server.py` → `curl -N -H "Authorization: Bearer <token>" -H "X-Plat-Context: {...}" "http://127.0.0.1:8765/api/events?session=s1"` 看首帧 `hello`；另开 `curl -X POST .../api/chat`。 |

---

## 7. 不要做什么（边界）

- ❌ **不改平台代码**（平台侧改动由前端团队负责；你只动 Agent 项目）。
- ❌ **不开写工具**：P2 全程只读；platform_* 全 `level="read"`；不接平台写接口（`PUT /api/rules`、`POST /api/fillins`、`POST /api/etl/import`、`POST /api/inflight/daily-report` 生成）。
- ❌ **不把 Agent token 下发到浏览器**：token 只在平台后端 ↔ Agent 之间；Agent 不做任何"给前端发 token"的事。
- ❌ **不要求平台侧开 CORS**：拓扑是 BFF 同源代理，CORS 与 Agent 无关。
- ❌ **不改 §2 冻结契约**：端点/帧格式/响应头/`Bearer` 鉴权只能新增、不能破坏。
- ❌ **不让 platform_* 工具读 xlsx 副本**：唯一真源是平台后端 API。

---

## 附：Agent 侧改动一览（便于排期）

| 编号 | 文件 | 改动 |
|------|------|------|
| T01 | `config.json` | 新增 `auth.tokens`（§3.1 示例） |
| T01 | `server.py`（可选） | 默认关闭、可配置白名单的 `CORSMiddleware`（§3.3） |
| T01 | `server.py` | **【实现阶段新增】** 审批事件 `permission_request`/`plan_request`/`step_request` 补 `level` 字段（`154-156`/`192-193`/`215-217`）（§3.4） |
| T04 | `server.py` | `/api/chat`（`463-510`）读 `X-Plat-Context` 并透传给引擎（§4.2 改点①） |
| T04 | `agentcore.py` | `run_task`（`1266-1282`）新增 `plat_ctx` 参数，在用户消息 append（`1281`）**之前**注入平台上下文 system 消息；`plan_and_run`（`1492`）同（§4.2 改点②） |
| T04 | `AGENT.md`（新） | 页面字典/工具规范/口径纪律（§4.4 草案） |
| T06 | `plugins/workmain.py` | 新增 10 个 `platform_*` 只读工具（§5.2 schema），含降级返回 + 字段白名单（§5.1/§5.3） |
| T06 | `plugins/workmain.py` | **【实现阶段新增】** platform_* 调用本机平台 API 必须 `trust_env=False`（绕过本机代理）（§5.1 规则 4） |
| 自测 | `scripts/`、`eval/` | 新增 `test_plat_context.py` / `test_platform_tools.py`（§6） |
