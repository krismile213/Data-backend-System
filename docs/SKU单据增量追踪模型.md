# 未闭环 SKU 单据增量追踪模型（T10）

> 交付日期：2026-09-28 ｜ 页面：`单据增量` ｜ 接口：`/api/deltas`
> 核心实现：`backend/app/doc_delta_service.py`（模型）+ `backend/app/routers/deltas.py`（接口）

---

## 1. 为什么要做（原「批次对比」结构性失效）

用户原话：

> 「现在最新的一批有 4027 个单据，对比上周，我得到的感知较弱；刚好现在批次对比中，
>   以后只有一个批次了，我需要定义一个模型，追踪未闭环 SKU 新增单据。」

失效的根因是**批次不再滚动**：

| | 旧模式 | 现行模式 |
|---|---|---|
| 批次目录名 | 带日期，如 `手机膜8.28` / `手机膜9.7` | **固定**：`data\手机膜\手机膜\` |
| 批次文件 | 每次导出新增一批 | `PartN_名称-时间戳.xlsx` **反复覆盖**（只留最新） |
| 批次表 | 多批次可横向比 | **永远只有 1 个批次在长** |

于是 `/api/compare`（批次对比）比的是"批次之间的 KPI / partBar / statusPie"——
**没有第二个批次可对比**，只剩 `4027` 这种总量大数，推不出「哪些未闭环 SKU 又动了、动了哪张单」。

**结论**：对比的维度必须从「批次 vs 批次」换成「**同一批未闭环 SKU 的单据集合，今天 vs 上一次同步**」。

---

## 2. 模型定义

把「未闭环 SKU 的单据集合」当成一个**随时间演化的状态**，只在数据指纹变化时拍一次，
持久化**最新基线 + 增量事件流**：

```
state[品类] = { at, fingerprint, skuCount, docCount, docs: { 单据键 → 单据指纹 } }
events[]    = 每次指纹变化产生的一条增量事件（含本次全部增量明细 + 汇总）
```

### 2.1 单据键（稳定标识）

```
单据键 = SKU | Part | 审批编号
```

审批编号为空时退化为 `SKU|Part|c:创建时间`。

> ⚠️ **绝不能用 `part_doc.id`**：增量导入是"整批删掉重建"，自增 id 每次都变，
> 拿它做基线会让**每张单看起来都是新的**。源文件里同一张单的创建时间不会变，
> 所以退化方案仍然稳定。回归用例：`tests/test_doc_delta.py::test_doc_key_has_no_id`。

### 2.2 单据指纹（能捕捉"单据内新动作"）

```
单据指纹 = 审批状态 + [ 节点指纹 ... ]
节点指纹 = 动作 | 时间 | 操作人          （不存 remark：体积大，对增量判断无用）
```

时间优先级的解析**不在本模块**——直接消费 `review._load_sku_docs` 已解析好的节点，
沿用复盘口径：`系统业务时间列 > audit 上报 > 审批动作时间`。

这样「既有单据上又推进一步」也会被 diff 抓出来，正是用户要的
**「单据内新动作也算增量」**。

### 2.3 追踪范围：只跟未闭环 SKU

```
未闭环 = review.get_review_data(cat)["rows"] 中 valid=False
```

与复盘总览 / 预警页**同源**，不另立标准。已闭环的 SKU 不会再推进，留在基线里
只会让状态文件无谓变大——实测未闭环单据只占全部单据约 **1/8**。

单据集合本身取全系统统一的那份：`review._load_sku_docs` + `review.dedupe_docs`
（含已结束 / 终止单据；多批次重复单据只留最新一条）。

---

## 3. 五类增量事件

| 字段 | 含义 | 说明 |
|---|---|---|
| `newDocs` | **新增单据** | 本基线有、上一基线没有的单据 |
| `newNodes` | **单据新动作** | **既有单据**上新增的节点动作 |
| `statusChanges` | **审批状态变化** | 既有单据 `审批中→已结束` 等 |
| `newSkus` | **新进入未闭环** | 新进入「未闭环」集合的 SKU（新品 / 刚有数据） |
| `closedSkus` | **离开未闭环** | 该 SKU 已闭环，从跟踪集合退出 |

### ⚠️ 关键约定：新单据的节点**不**重复计入 `newNodes`

否则「一张新单带 5 个节点」会被数成 `1 张单据 + 5 个新动作`，把"新增动作"这个指标灌水。
信息不丢：新增单据行里直接显示该单的 `nodeCount`。

回归用例：`test_new_doc_nodes_not_double_counted`。

---

## 4. 三条幂等 / 防噪规则（都真机验证过）

### 4.1 指纹没变 → 不产生事件
自动管线一天跑 2 次（09:20 / 14:50），页面访问还会补拍。指纹没变**直接返回
`recorded=False`**，不落新事件——否则一天长出 10 条空事件，走势图全是 0。

### 4.2 首次拍只建基线，**不产生事件**
否则一上来会把当时全部未闭环单据报成「新增」（实测 **504 条**），那是噪声不是增量。
判定依据是「**该品类有没有建过基线**」（state 条目存在与否），**不是 docs 是否为空**：
全部未闭环 SKU 都闭环时 docs 会正常变空，之后再有新 SKU 进来，那批单据是真新增，
不能被当成"首次建基线"吞掉。

### 4.3 没有活批次 → 跳过拍快照
批次目录缺失时（`review._live_batch_ids(cat)` 为空）**不拍**。
否则会把基线冲成空表，下一轮真实数据回来时**全部报成新增**。

### 4.4 suspect 标记：疑似基线重算（只标记，不吞数据）

```
新增单据数 > 当前单据总数 × 0.6  且  总数 ≥ 50   →  suspect = True
```

场景：源单据换了号段、基线文件被清过。**宁可让业务看到"这轮 480 张单都算新增"
并起疑，也不要静默丢增量**。页面上该次快照带 `suspect` 标记。

---

## 5. 为什么不是"每天存一张全量快照"

| 方案 | 稳态占用 | 历史趋势 |
|---|---|---|
| 每日全量快照 | ~100KB/品类/次 × 2 次/天 × 365 ≈ **100MB** | 能画 |
| **基线 + 事件流（选用）** | **~200KB** | 能画（事件本身就是历史，按日聚合即可） |

事件流还额外直接回答了「近 7 天新增了多少张单」。

**保留策略**：`EVENT_RETENTION_DAYS = 400` 天 + `EVENT_MAX = 2000` 条（保留最新）。

---

## 6. 锁纪律（本项目有死锁前科，务必遵守）

历史事故：`get_review_data → record_snapshot → _build_kpi → … → get_review_data` 环形重入，
非重入锁自撞，`history._lock` **永不释放**（表现为接口永不返回、不报错、不 500）。

本模块的写法：

```
锁内：只做 load / diff / save（纯读写 json，毫秒级）
锁外：build_docs()（会触发 review 重算，可能几秒）
并发：双重检查 —— 重算前后各查一次指纹，避免并发重复提交
```

`_lock` 用 `RLock`；`build_docs` 的 docstring 明确写了「调用方必须在锁外调用它」。

---

## 7. 接入点

| 位置 | 行为 |
|---|---|
| `backend/etl/auto_pipeline.py` | `_step_snapshots_and_reports()` 中，**紧跟导入之后**调 `maybe_doc_snapshot(cat)` |
| `GET /api/deltas` | 首次访问自动补拍（指纹变化才真落一张），与 history / inflight 同思路 |
| `POST /api/deltas/snapshot` | 页面「立即拍快照」按钮；`?force=true` 同指纹也重拍（排障/演示） |
| `GET /api/deltas/export` | 导出 xlsx（5 sheet） |

所有接口支持 `?category=`（手机膜 / 手机壳），缺省 = 默认品类；未知品类回退默认品类不抛异常。

### 导出 xlsx 的 5 个 Sheet

1. 新增单据
2. 单据新动作
3. 状态变化
4. 未闭环 SKU 汇总
5. 口径说明

---

## 8. 状态文件

```
<STATE_DIR>/doc_delta_state.json      # STATE_DIR 见 backend/app/config.py
{
  "version": 1,
  "state": { "<品类>": { at, fingerprint, skuCount, docCount, docs:{...} } },
  "events": [ { date, at, category, fingerprint, baseline, suspect,
                newDocs[], newNodes[], statusChanges[], newSkus[], closedSkus[],
                summary{...} }, ... ]
}
```

写入用临时文件 + `os.replace`（原子替换，避免半截文件）。

---

## 9. 接口响应体形状

```jsonc
{
  "computedAt": "2026-09-28 11:20",
  "category": "手机膜",
  "scope": "未闭环 SKU（复盘口径 valid=False）",
  "sku": "",
  "exists": true,                       // 是否已有基线
  "state": { "at": "...", "fingerprint": "...", "skuCount": 25, "docCount": 504 },
  "last":   { "at": "...", "baseline": false, "suspect": false,
              "summary": {...}, "newDocs": [...], "newNodes": [...],
              "statusChanges": [...], "newSkus": [...], "closedSkus": [...] },
  "eventCount": 12,
  "window": { "days": 30, "since": "2026-08-29", "summary": {...},
              "byDay": [...], "bySku": [...] },
  "events": [ ... ],                    // 快照流水（新→旧，最多 30 条）
  "settings": { "retentionDays": 400, "eventMax": 2000, "suspectRatio": 0.6 }
}
```

> ⚠️ `window_rows()` 里，`newNodes` 行原有 `at` 是**动作时间**，会被事件时刻覆盖——
> 代码先把动作时间挪到 `actionAt`，避免两种时间在同一字段上打架。

---

## 10. 验证

| 层 | 命令 / 文件 | 断言 |
|---|---|---|
| 单元 | `pytest tests/test_doc_delta.py -q`（23 例） | 键不含 id / 新单据不重复计 / 未闭环集合与复盘一致 / 无活批次跳过 / suspect 标记 / 窗口聚合自洽（SKU 维度求和 == 汇总） |
| 自检 | `python start.py delta`（= `scripts/verify_doc_delta.py --record`） | 契约断言 + 拍快照 + 导出 |
| 在线 | `start.bat verify`（离线四件套 + 在线四步） | 20 页渲染 / 34 接口 / 导出魔数 PK |

> ⚠️ 验收前先 `start.bat restart`：本机常有用户自启的 8000 旧实例，
> 检查脚本会打到它身上（旧代码 → `/api/deltas` 404）。
> 若确实不能打扰用户正在用的实例，可另起隔离端口（后端 8001 + 前端 5174，
> `vite.config.js` 支持 `API_TARGET` 环境变量），检查脚本均支持 `--base`。

---

## 11. 如何回退

本模块是**纯新增**：新增文件 + 新增路由 + 管线里新增一行调用 + 新增一个导航项。
回退只需：

1. 从 `backend/app/main.py` 移除 `deltas` 路由的 import 与 `include_router`；
2. 从 `backend/etl/auto_pipeline.py` 的 `_step_snapshots_and_reports()` 移除 `maybe_doc_snapshot(cat)`；
3. 删除 `<STATE_DIR>/doc_delta_state.json`（避免残留基线被将来的版本误用）。

前端页面与 `start.py delta` 子命令留着不影响任何既有功能。
