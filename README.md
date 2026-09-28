# 新品售前流程数据后台系统

本地 Web 后台系统：在浏览器中查看新品售前流程（Part1~Part9 钉钉审批导出数据），
进行**时效分析可视化**与**数据明细查询/异常排查**。所有数据仅在本机处理，不出外网。

## 一、启动方式（一键）

双击项目根目录下的 **`start.bat`**，它会：

1. 检查/安装前端依赖（仅首次）；
2. 启动后端 FastAPI（端口 8000）；
3. 启动前端 Vite dev（端口 5173，`/api` 自动代理到后端）；
4. **轮询确认前后端真正响应之后**，再自动打开浏览器访问 http://127.0.0.1:5173

关闭：直接关闭弹出的 `backend-8000` 和 `frontend-5173` 两个命令行窗口。

### 可选参数

在 cmd 中带参数执行（例如 `start.bat restart`）：

| 参数 | 作用 |
|------|------|
| （无） | 常规启动。若 8000 / 5173 已在监听，则**复用**已有服务，不会重复启动 |
| `restart` | 先结束占用 8000 / 5173 的残留进程，再启动（页面空白时的首选手段） |
| `stop` | 结束两个服务并退出（关闭系统不用再手工找窗口） |
| `status` | 只检查当前状态：端口是否监听 + HTTP 是否响应，不启动任何东西 |
| `clean` | 删除 Vite 依赖缓存 `node_modules\.vite` 后再启动（极端情况兜底） |
| `backup` | 备份 `backend/state/` 与数据库导出后退出（不启动服务） |
| `help` | 打印参数列表后退出 |

> `stop` / `status` 判断服务是否真的停了，靠的是**端口**：从 `netstat` 取占用
> LISTENING 套接字的 PID 再结束它。只结束外层命令行窗口是不够的——子进程仍会
> 占着端口、握着数据库连接，看起来"关了"其实没关。

> **排查前端打不开**：先关掉 `backend-8000` 与 `frontend-5173` 两个窗口，再运行
> `start.bat restart`。前端固定占用 5173（`strictPort: true`），端口被别的东西占用时
> 会明确报错，而不是静默切换到 5174 导致浏览器打开错地址。
>
> 启动过程中**不会**每次删除 Vite 依赖缓存——缓存只在 `clean` 参数或依赖本身变化时才重建，
> 这样冷启动更快、首屏白屏时间更短。

> 说明：`start.bat` 内的提示文字为英文，这是刻意的——Windows cmd 默认按 GBK
> 解析批处理文件，bat 中包含中文会被解析成乱码并报错（「不是内部或外部命令」）。
> 请勿往 start.bat 里添加中文内容；页面内的中文显示不受影响。

## 二、手动启动

### 后端（Python FastAPI）

使用托管版 Python venv（不要全局安装依赖）：

```bat
:: 首次：创建 venv 并安装依赖
C:\Users\dell\.workbuddy\binaries\python\versions\3.13.12\python.exe -m venv C:\Users\dell\.workbuddy\binaries\python\envs\default
C:\Users\dell\.workbuddy\binaries\python\envs\default\Scripts\pip.exe install -r backend\requirements.txt

:: 启动
cd backend
C:\Users\dell\.workbuddy\binaries\python\envs\default\Scripts\python.exe run.py
```

后端地址：http://127.0.0.1:8000 ，接口文档：http://127.0.0.1:8000/docs

### 前端（Vite + React）

使用平台托管版 Node。**版本目录会随平台更新而变化**（实测已从 `22.22.2-2` 变为
`22.22.2-3`，旧目录被直接删除），所以不要写死版本号 —— 平台把当前版本写在
`versions\current` 里：

```bat
cd frontend
set /p NODE_VER=<%USERPROFILE%\.workbuddy\binaries\node\versions\current
set "NPM=%USERPROFILE%\.workbuddy\binaries\node\versions\%NODE_VER%\npm.cmd"

"%NPM%" install
"%NPM%" run dev
```

访问 http://127.0.0.1:5173

> 更省事的做法：直接双击 `start.bat`——它会在运行时自动定位 node 运行时，
> 并在前后端真正就绪后才打开浏览器。

## 三、功能说明

### 1. 批次切换（右上角）
下拉选择 `C:\Users\dell\Desktop\work-main\data\手机膜\` 下的批次目录
（如 手机膜8.28 / 8.31 / 9.7），全站数据随之切换。

### 2. 时效看板（核心）
- KPI 卡片：总单据数、已完成/进行中/终止、平均全流程耗时、异常单据数；
- 各 Part 全流程耗时对比柱状图（平均 / 中位数 / P90，已结束单据口径）；
- 单据审批状态分布饼图；
- 各 Part 分段动作平均耗时热力图（定位流程瓶颈环节）；
- 单据创建时间趋势折线（按日合计，支持缩放）。

### 3. 明细查询
- 按批次 / Part（Tab 切换）浏览全部列；
- 审批记录列可**展开查看节点明细**（操作人 / 动作 / 时间 / 备注）；
- 按审批编号、创建人、审批状态筛选 + 全字段关键字搜索；
- 点击列头排序（编号 / 时间 / 状态等）、后端分页；
- 「导出 xlsx」导出当前筛选结果，**审批编号按文本写出**，杜绝 18 位编号精度丢失。

### 4. 异常排查
复用现有时效分析程序口径，自动识别：
- 审批编号缺失 / 重复（跨行比对）
- 审批记录为空
- 终止单据
- 已结束但流程未到终节点
- 长期未完成（停滞）：审批中且超过 max(3天, 该环节已结束单据全流程P90)
- 环节耗时超长：分段耗时 > max(7天, Q3+1.5×IQR)
- 分段耗时异常小（疑似补录）：耗时 < 0.1h 且环节中位数 ≥ 1h
- 流程回退/重做（关键动作多次出现）
- 节点时间乱序
- 已结束单据业务时间列缺失

按类型标签筛选、按 Part 过滤，每条异常附原因说明。

### 5. 场景诊断（跨环节联动）
复用 `流程复盘/流程优化建议.md` 的实证结论（Part5 发货停滞 → 卡住 Part6 素材 → 页面 coming soon），
自动把「待去化」的 SKU 分成三类并给出去化路径：
- **场景 A**：已首上架但素材未完成（最易 coming soon）→ 优先排素材，素材完成 + Part7 上架即可 Ready；
- **场景 B**：素材已完成但最终上架未完成 → 催办 Part7，最短路径；
- **场景 C**：Part5 存在未结束发货单 → 推动发货，建议素材与发货解耦提前启动。

每条记录给出停滞天数（距首次上架 / 距素材完成 / 距最早在途发货单）、头号卡点、判定说明与建议动作；
支持按场景 Tab 切换、SKU/型号搜索、导出 xlsx，页面底部列出可核对的判定口径。

### 6. 路线达标（route_check.py 口径移植）
- **运营路线** = 取数 → max(上架,广告)，≤52优 / ≤60良 / >60差；
- **空运路线** = 取数 → 最晚空运货件到货，≤49优 / ≤60良 / >60差；
- **海运路线** = 取数 → 最晚海运货件到货，≤69优 / >69差（不稳定不列良）；
- **重点复盘口径**：两条路线均 ≤60 天且后勤 < 运营 → 「运营环节拖后腿」；
- 每行展示三条路线徽标 + 最差评级，点击行展开该 SKU 段级明细；评级分布堆叠柱图 + 筛选 + 导出。

### 7. 货件运输
把散落在复盘弹窗、甘特运输段、预警运输明细三处的逐票判定汇总成独立视图：
- KPI：总票数、已到/未到、超时票数及占比、平均/最长运输天数、未发货渠道清单；
- 按渠道 × 运输方式聚合（超时票数、平均天数、最长超目标天数），渠道超时 TOP 柱图；
- 逐票明细表：货件号 / 渠道 / 方式 / 发货 / 到货 / 运输天数 vs 目标，按判定与方式筛选 + 分页 + 导出。

### 8. 联动提醒（跨环节预警·页面内版）
把「Part5 发货停滞 → 卡 Part6 素材 → 页面 coming soon」变成按人推进的行动清单：
- **R1 素材可先行启动**：签样已完成 + 素材未启动 + 发货停滞 ≥ stuck_days（规则可调）；
- **R2 签样缺失**：寄送拍摄已发起但签样未完成，素材无从启动；
- 按责任人（实际执行人 > 默认配置）分组，**一键复制提醒文案**（含 SKU/停滞天数/渠道/建议动作）；
- 后续接通知渠道（钉钉机器人/邮件）后可直接推送，数据口径不变。

### 9. 手工填报（实际时间·免登录单机版）
审批导出缺失/失真的节点时间在这里人工补录，与审批口径自动对比：
- 选择 SKU（优先列出未闭环）→ 逐节点填写实际日期与备注，同节点重复保存即更正；
- 双口径对比：每节点显示审批口径时间（系统列 > audit > 动作）与手工填报的**差异天数**，差异大自动标色；
- 数据存本机 `backend/state/fillins.json`（原子写盘），支持导出 xlsx；填报人姓名记在浏览器本地。

### 10. 趋势周报（KPI 快照）
解决「只有横截面、没有时间纵深」：数据指纹（导入/规则变化）后的首次访问自动记录一张 KPI 快照（存 `backend/state/kpi_snapshots.json`，按指纹去重）：
- 核心指标走势图（总体达标率 / 货件超时率 / 未闭环 SKU）+ 期初 vs 最新对比表；
- 一键生成**周报/月报 markdown**（7/30 天区间：KPI 变化 + 当前看板要点 + 自动解读），周会前可「立即快照」打点。

### 11. 数据管理（批次导入 + 备份）
把原先只能在命令行完成的 ETL 搬进界面，让「更新数据」不再需要离开浏览器：

**批次扫描** —— 对照数据库给出每个批次的导入状态：
- `待首次导入`：磁盘上有、库里还没有；
- `文件已变更`：Excel 更新过，需要重新导入；
- `已是最新`：文件指纹与上次导入一致；
- `数据源缺失`：库里记录过，但磁盘上已找不到该批次目录。

**一键导入** —— 支持单批次导入、`强制重导`（忽略文件指纹，用于内容改了但修改时间没变的场景）
以及「导入全部变更」；未变化的批次自动跳过，结果按批次列出单据数与节点数。

**数据备份** —— 一键备份 `backend/state/`（手工填报 + KPI 快照，**无法从 Excel 重建**）
与数据库导出，落盘到 `backups\时间戳\`（含 `manifest.txt` 记录本次内容），并列出历史备份与大小。

> 命令行等价入口：`start.bat backup`。

### 12. 操作手册（SOP / 交接 / 事中事后）
把新人培训与交接文档放进系统，避免"文档在某人电脑里、人一走就找不到"：
- **新品售前流程 SOP 操作手册**：9 个环节的业务目标、动作链、必填字段、时效标准与常见卡点，
  含新人 30 天上手路径与日常工作节奏建议；
- **岗位职责与交接说明书**：岗位×环节职责矩阵、已离职人员影响面盘点、单点风险清单，
  含可直接复用的交接检查表与招聘 JD 参考；
- **流程事中与事后评估 使用说明**：事中日报页与事后评估页怎么读——段状态机、85% 预警线、
  日报 5 个 sheet、损益三指标口径、手机壳「目标待校准」提示与常见问题。

文档源文件在仓库 `docs/` 目录，**直接编辑 `.md` 文件即可更新**，刷新页面生效；
新增文档只需在 `backend/app/routers/manual.py` 的白名单里登记一行。

## 四、技术栈与结构

- 后端：Python FastAPI + uvicorn + pandas + openpyxl（`backend/`）
  - `app/config.py`：数据目录扫描、Part 环节定义（移植自现有分析程序，口径一致）
  - `app/parser.py`：审批记录解析（操作人|动作|时间|备注，多种异常格式兼容）
  - `app/analysis.py`：分段耗时计算、时效统计（P90/箱线 IQR）、异常识别、批次缓存
  - `app/prewarm.py`：**空闲预热**。全后端只有三处进程内缓存（`analysis._cache` 按批次目录
    指纹、`review._cache` 与 `alerts._cache` 按 DB 指纹 + 规则版本），冷启动代价为
    overview 2.4s / alerts 2.5s，热缓存 0.03s。预热在后台线程里把这三处提前算好，
    但**只在连续 5s 无人访问时才动手**（启动后另等 10s，覆盖「浏览器已开、前端仍在加载
    模块」这段后端看不到请求的空窗）。`PREWARM=0` 关闭，`PREWARM_DELAY` 调等待秒数。

    > ⚠️ **别改回「启动后立刻预热」**——实测那样会把首个访问从 **2.4s 拖到 5.4s**：
    > 单进程 pandas 预热与用户的首次请求抢 CPU（GIL），而 `get_batch_data` 的按批次锁
    > 让用户请求只能排队等预热算完同一批次。礼让式预热不可能比不做更差，立即预热会。
    > 收益落在「用户没打开过的批次」上：切换批次合计 **3.43s → 0.11s**。
  - `app/routers/`：18 组接口 —— batches / overview / timeliness / anomalies / records /
    compare / skus / review / scenarios / routes / transport / alerts / watch /
    fillins / history / rules / dataadmin / manual
- 前端：Vite + React + MUI + ECharts（`frontend/`）
  - 页面按需懒加载（`React.lazy`），首屏只加载当前页面，不必为 17 个页面（含 2.6MB ECharts）买单；
  - 页面与批次同步到 URL（`?page=transport&batch=手机膜9.7`）：刷新保持、链接可分享、支持浏览器前进后退；
  - **视觉单一来源** `src/theme.js`：所有颜色先定义语义名再被引用
    （`SEMANTIC.danger` 超时/未达标、`warning` 进行中、`success` 达标、`info`、`neutral` 终止，
    另有 `STATUS_COLORS` / `PIE_PALETTE` / `HEAT_RANGE` 图表色板与 `severityOfDays()` 停滞分级）。
    改一处颜色即全站生效，也是后续做深色模式的前提；
  - **公共组件** `src/components/`：`KpiCard`（指标数字卡）、`PageHeader`（页面标题栏）、
    `StateViews`（`LoadingState` / `ErrorState` / `EmptyState` 三态）、`SectionCard`（带标题卡片容器）；
  - **数据请求** `src/hooks/useApi.js`：统一 `data / error / loading / refetch`，
    内置**请求序号防竞态**——切换批次或筛选时，先发出的旧请求不会覆盖新结果；
    **17 个页面全部接入**，页内仅剩"选中项变化后清空详情"这类本地状态同步的 `useEffect`。
  - 样式统一走 MUI 的 `sx`：**Tailwind 已整体移除**（配置、依赖、`@tailwind` 指令）。
    它原先只被 9 处工具类用到，却要多养一套样式体系并承担 preflight 与 MUI 互相覆盖的风险；
    页面内已无 `className`，`index.css` 只保留挂载所需的最小重置，字体与背景由
    `theme.js` + `CssBaseline` 提供。
  - **页面冒烟脚本** `frontend/scripts/ssr_smoke.mjs`：用 Vite 的 SSR 能力把 19 个页面
    逐个真实渲染一遍，专抓"能编译、但一跑就报错"的残留（例如重构后仍引用已删除的
    state/setter）——esbuild 不做未定义变量检查，dev server 显示"编译通过"并不代表页面
    跑得起来。在 `frontend/` 目录下执行 `node scripts/ssr_smoke.mjs` 即可（只渲染初始态，
    不依赖后端是否在跑）。另附 4 个公共组件 + `CategorySelector` 的**收起态标记**断言
    （顶栏组件不在页面清单里，且手机壳的「目标待校准」标记原先只在展开的菜单项中可见）。

    > 脚本会在 `transform` 阶段把 `@mui/icons-material/<X>` 的 import 就地换成占位组件。
    > 原因：这些图标是 CJS 包，SSR 走 Node 原生 ESM/CJS 互操作时 `import X from '<cjs>'`
    > 拿到的是整个 `module.exports` 对象，React 报 `Element type is invalid` —— 那是 harness
    > 的假阳性（浏览器端 Vite 会生成 `__esModule ? .default : mod` 正确解包），会让含图标的
    > 页面**永久误报**，反而掩盖页面里真正的错误。必须用 `transform` 而非 `resolveId`：
    > SSR 下 Vite 把 node_modules 依赖标为 external，会绕过 `resolveId`/`load`。
- 数据根目录：`C:\Users\dell\Desktop\work-main\data\手机膜\`
  （优先级：环境变量 / `.env` 的 `DATA_ROOT` > `backend/app/config.py` 内置默认值）
- 文档：`docs/` —— SOP 操作手册、岗位职责与交接说明书（系统「操作手册」页直接读取）

## 五、接口一览

| 接口 | 说明 |
| --- | --- |
| GET /api/batches | 列出可用批次 |
| GET /api/overview/{batch} | 总览 KPI + 状态分布 |
| GET /api/timeliness/{batch} | 各 Part 分段时效统计（均值/中位/P90/箱线） |
| GET /api/anomalies/{batch}?type=&part= | 异常单据列表 |
| GET /api/records/{batch}/{part} | 明细分页 + 筛选 + 搜索 + 排序 |
| GET /api/records/{batch}/{part}/export | 导出当前筛选结果 xlsx |
| GET /api/compare?batches=A,B | 批次对比（KPI/Part耗时/状态分布，2~6 批次） |
| GET /api/skus | SKU 跨批次聚合清单（119 个 SKU） |
| GET /api/skus/{code} | SKU 档案：跨批次单据 + 审批节点时间线 |
| GET /api/review/overview | 大流程复盘总览：KPI/分布/卡点TOP/环节达标率/人员达成/全量SKU行 |
| GET /api/review/sku/{code} | SKU 复盘档案：各 Part 实际vs目标、贡献、分段耗时（含起止时间）、三部门终节点 |
| GET /api/review/sku/{code}/report | 单 SKU 复盘报告（markdown 下载，自动生成） |
| GET /api/scenarios | 跨环节联动诊断：场景 A/B/C 分组清单 + KPI + 去化路径 + 判定口径 |
| GET /api/scenarios/export?code=A | 导出跨环节场景诊断 xlsx（可按场景 A/B/C 过滤，默认全量） |
| GET /api/routes | 路线达标看板：运营/空运/海运三条路线 SKU 级评级（优/良/差）+ 分布 + 重点复盘标记 |
| GET /api/routes/export | 导出路线达标 xlsx（每 SKU 三行路线） |
| GET /api/transport | 货件运输看板：KPI + 按渠道/运输方式聚合 + 逐票判定明细 |
| GET /api/transport/export | 导出货件运输明细 xlsx |
| GET /api/alerts | 加急预警：P0/P1/P2 未闭环明细 + 盯数清单 + 数据新鲜度（route_check_sys 口径） |
| GET /api/alerts/export?priority=P0 | 导出加急预警清单 xlsx（可按优先级过滤） |
| GET /api/review/overview/export | 导出大流程复盘总览 xlsx（117 SKU 全量行） |
| GET /api/watch | 联动提醒（页面内预警）：R1 素材可先行启动 / R2 签样缺失 + 按责任人提醒文案 |
| GET /api/fillins | 手工填报：节点清单 + 未闭环 SKU + 全部记录 |
| GET /api/fillins/sku/{code} | 单 SKU 节点档案：审批口径 vs 手工填报（含差异天数） |
| POST /api/fillins | 新增/更正一条填报（同 SKU 同节点覆盖；非法节点/日期返回 422） |
| DELETE /api/fillins/{id} | 删除一条填报记录 |
| GET /api/fillins/export | 导出手工填报 xlsx（含双口径对比列） |
| GET /api/history | KPI 快照列表（访问即补拍） |
| POST /api/history/snapshot | 立即补拍快照（force=True 同指纹强制记录） |
| GET /api/history/report?days=7 | 生成周报/月报 markdown 下载 |
| GET /api/rules | 时效规则配置（当前值 + 默认值 + 说明） |
| PUT /api/rules | 合并保存规则（合法键/范围校验），保存后复盘与预警立即重算 |
| POST /api/rules/reset | 恢复代码内置默认规则 |
| GET /api/etl/scan | 扫描数据目录，对照数据库返回各批次导入状态（待导入 / 已变更 / 已是最新 / 数据源缺失） |
| POST /api/etl/import | 触发导入（batch 为空则处理全部，未变化的自动跳过；force=true 强制重导） |
| GET /api/etl/backup | 列出已有备份（按时间倒序，含大小与文件清单） |
| POST /api/etl/backup | 立即备份：`backend/state/` + 数据库导出 → `backups/时间戳/` |
| GET /api/manual | 列出已收录的操作手册文档（标题 / 说明 / 更新时间） |
| GET /api/manual/{key} | 取指定手册的 markdown 原文（白名单校验，未收录返回 404） |

批次不存在返回 404；空数据返回空结构而非 500。

## 六、MySQL 数据层

数据库：本机 MySQL 8.0，库 `data_admin`（utf8mb4），四张表：
`batch`（批次与文件指纹）/ `part_doc`（单据，含 raw JSON 原始列）/
`approval_node`（审批节点）/ `rule_config`（时效规则）。

### 连接配置：两套环境

| 环境 | Python 解释器 | 配置文件 | 启动方式 |
| --- | --- | --- | --- |
| 开发（当前仓库） | `C:\Users\dell\.workbuddy\binaries\python\envs\default` | `<仓库根>\.env`（可选，缺省用内置默认值） | `start.bat` |
| 生产部署 | `D:\apps\data-admin\venv` | `D:\apps\data-admin\.env` ← **当前生效的一份** | `D:\apps\data-admin\start-backend.bat` |

`backend/app/db.py` 按 `DATA_ADMIN_ENV_FILE` → `D:\apps\data-admin\.env` → `<仓库根>\.env`
→ `<仓库>\backend\.env` 的顺序加载，**先命中者生效**。复制 `.env.example` 即可得到模板。

- ETL 导入（覆盖式 + 文件指纹增量跳过）—— 界面入口见「功能说明 11. 数据管理」，命令行入口：
  `python backend\etl\import_excel.py [--batch 手机膜9.7] [--force]`
- 备份：`start.bat backup`，或界面「数据管理 → 立即备份」；输出到 `backups\时间戳\`
  （含 `manifest.txt`，记录本次包含的文件与数据库导出状态）；
- SKU 维度接口：`GET /api/skus`（跨批次聚合）、`GET /api/skus/{code}`（档案 + 时间线）。
