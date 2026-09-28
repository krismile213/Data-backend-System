/**
 * api.js — 后端接口 fetch 封装
 * 所有请求走 /api 前缀（dev 模式由 vite 代理到 127.0.0.1:8000）
 *
 * 品类（手机膜/手机壳）：自 2026-09-22 起后端按品类分发规则与批次，
 * 这里**统一注入** `category`，页面就不用各自记着带（漏一个就是一份错口径）。
 * 后端对不认识的参数会忽略，因此对「与品类无关」的接口（如文件目录类）无副作用。
 */

import { appendCategory, getCategory } from './category';

const BASE = '/api';

async function request(path, params = {}) {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, v);
  });
  if (!qs.has('category')) qs.set('category', getCategory());
  const url = `${BASE}${path}${qs.toString() ? `?${qs.toString()}` : ''}`;
  const res = await fetch(url);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && body.detail) detail = body.detail;
    } catch (e) {
      /* 忽略解析失败 */
    }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

/** 列出可用批次 */
export function fetchBatches() {
  return request('/batches');
}

/** 批次总览 KPI */
export function fetchOverview(batch) {
  return request(`/overview/${encodeURIComponent(batch)}`);
}

/** 各 Part 分段时效统计 */
export function fetchTimeliness(batch) {
  return request(`/timeliness/${encodeURIComponent(batch)}`);
}

/** 批次对比（2~6 个批次） */
export function fetchCompare(batches) {
  return request('/compare', { batches: batches.join(',') });
}

/** 异常单据列表 */
export function fetchAnomalies(batch, { type, part } = {}) {
  return request(`/anomalies/${encodeURIComponent(batch)}`, { type, part });
}

/** 导出异常清单 xlsx（浏览器直接下载，与页面筛选一致） */
export function buildAnomalyExportUrl(batch, { type, part } = {}) {
  const qs = new URLSearchParams();
  if (type) qs.set('type', type);
  if (part) qs.set('part', part);
  return `${BASE}/anomalies/${encodeURIComponent(batch)}/export${qs.toString() ? `?${qs.toString()}` : ''}`;
}

/** 明细分页查询 */
export function fetchRecords(batch, part, opts = {}) {
  const { page, pageSize, sortField, sortOrder, approvalNo, creator, status, keyword } = opts;
  return request(`/records/${encodeURIComponent(batch)}/${encodeURIComponent(part)}`, {
    page,
    pageSize,
    sortField,
    sortOrder,
    approvalNo,
    creator,
    status,
    keyword,
  });
}

/** SKU 清单（跨批次聚合，MySQL 数据层） */
export function fetchSkus() {
  return request('/skus');
}

/** 大流程复盘总览（KPI/分布/卡点TOP/环节统计/人员/全量SKU行） */
export function fetchReviewOverview() {
  return request('/review/overview');
}

/** 单个 SKU 的复盘档案（各 Part 实际vs目标、分段、三部门终节点） */
export function fetchReviewSku(code) {
  return request(`/review/sku/${encodeURIComponent(code)}`);
}

/** 跨环节联动诊断：场景 A/B/C 分组清单 + KPI + 去化路径 */
export function fetchScenarios() {
  return request('/scenarios');
}

/** 导出跨环节场景诊断 xlsx（可选场景 A/B/C，默认全量） */
export function buildScenariosExportUrl(code) {
  const qs = code ? `?code=${encodeURIComponent(code)}` : '';
  return appendCategory(`${BASE}/scenarios/export${qs}`);
}

/** 路线达标看板：运营/空运/海运三条路线的 SKU 级评级 + 分布 */
export function fetchRoutes() {
  return request('/routes');
}

/** 导出路线达标 xlsx */
export function buildRoutesExportUrl() {
  return appendCategory(`${BASE}/routes/export`);
}

/** 货件运输看板：逐票判定 + 渠道/运输方式聚合 */
export function fetchTransport() {
  return request('/transport');
}

/** 导出货件运输明细 xlsx */
export function buildTransportExportUrl() {
  return appendCategory(`${BASE}/transport/export`);
}

/** 加急预警（P0/P1/P2 明细 + 盯数清单 + 数据新鲜度，route_check_sys 口径） */
export function fetchAlerts() {
  return request('/alerts');
}

/** 读取时效规则配置（当前值 + 默认值 + 说明） */
export function fetchRules() {
  return request('/rules');
}

/** 规则配置保存（PUT /api/rules，合并式更新） */
export async function putRules(patch) {
  const res = await fetch(`${BASE}/rules`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ patch }),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && body.detail) detail = body.detail;
    } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

/** 恢复规则默认值 */
export async function resetRules() {
  const res = await fetch(`${BASE}/rules/reset`, { method: 'POST' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** 导出加急预警清单 xlsx（可按优先级过滤） */
export function buildAlertsExportUrl(priority) {
  const qs = priority ? `?priority=${encodeURIComponent(priority)}` : '';
  return appendCategory(`${BASE}/alerts/export${qs}`);
}

/** 导出大流程复盘总览 xlsx */
export function buildReviewExportUrl() {
  return appendCategory(`${BASE}/review/overview/export`);
}

/** 单 SKU 核查报告 xlsx：甘特（按部门归块）+ 段级明细 + 末尾「人工核对」空白列（P0-6） */
export function buildCheckReportUrl(code) {
  return appendCategory(`${BASE}/review/sku/${encodeURIComponent(code)}/checkreport`);
}

/** 手工填报：元数据（节点清单 + 未闭环 SKU + 全部记录） */
export function fetchFillins() {
  return request('/fillins');
}

/** 手工填报：单 SKU 节点档案（审批口径 vs 手工 + 差异） */
export function fetchFillinsSku(code) {
  return request(`/fillins/sku/${encodeURIComponent(code)}`);
}

/** 手工填报：新增/更正一条（同 SKU 同节点覆盖） */
export async function saveFillin(payload) {
  const res = await fetch(`${BASE}/fillins`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && body.detail) detail = body.detail;
    } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

/** 手工填报：删除一条记录 */
export async function deleteFillin(id) {
  const res = await fetch(`${BASE}/fillins/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** 导出手工填报记录 xlsx（含双口径对比列） */
export function buildFillinsExportUrl() {
  return appendCategory(`${BASE}/fillins/export`);
}

/** 跨环节联动预警（页面内版）：清单 + 按责任人提醒文案 */
export function fetchWatch() {
  return request('/watch');
}

/** KPI 快照列表（访问即补拍） */
export function fetchHistory() {
  return request('/history');
}

/** 立即补拍一张 KPI 快照 */
export async function triggerSnapshot(force = false) {
  const res = await fetch(`${BASE}/history/snapshot?force=${force}`, { method: 'POST' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** 生成/下载周报 markdown（days=统计区间天数） */
export function buildHistoryReportUrl(days = 7) {
  return appendCategory(`${BASE}/history/report?days=${days}`);
}

/** 单 SKU 复盘报告 markdown 下载 */
export function buildSkuReportUrl(code) {
  return appendCategory(`${BASE}/review/sku/${encodeURIComponent(code)}/report`);
}

/** SKU 档案：跨批次单据 + 审批节点时间线 */
export function fetchSkuDetail(code) {
  return request(`/skus/${encodeURIComponent(code)}`);
}

/** 导出当前筛选结果 xlsx（浏览器直接下载） */
export function buildExportUrl(batch, part, opts = {}) {
  const { approvalNo, creator, status, keyword } = opts;
  const qs = new URLSearchParams();
  Object.entries({ approvalNo, creator, status, keyword }).forEach(([k, v]) => {
    if (v) qs.set(k, v);
  });
  return `${BASE}/records/${encodeURIComponent(batch)}/${encodeURIComponent(part)}/export${qs.toString() ? `?${qs.toString()}` : ''}`;
}

/* ------------------ 数据管理：批次扫描 / 导入 / 备份 ------------------ */

async function postJson(path, body) {
  const res = await fetch(appendCategory(`${BASE}${path}`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const resBody = await res.json();
      if (resBody && resBody.detail) detail = resBody.detail;
    } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

/** 扫描数据根目录，对照数据库返回各批次的导入状态 */
export function fetchEtlScan() {
  return request('/etl/scan');
}

/** 触发导入：batch 为空则处理全部批次（文件未变化的会自动跳过） */
export function runEtlImport(batch, force = false) {
  return postJson('/etl/import', { batch: batch || null, force });
}

/** 已有备份列表 */
export function fetchBackups() {
  return request('/etl/backup');
}

/** 立即备份（state 目录 + 数据库导出） */
export function runBackup() {
  return postJson('/etl/backup');
}

/* ---------------------------- 操作手册 ---------------------------- */

/** 已收录的手册文档列表 */
export function fetchManuals() {
  return request('/manual');
}

/** 取某份手册的 markdown 原文 */
export function fetchManual(key) {
  return request(`/manual/${encodeURIComponent(key)}`);
}

/* ------------------ 事中管理：在研总览 / 预警 / 日报 ------------------ */

/** 事中总览：在研 SKU 清单 + KPI（T03） */
export function fetchInflightOverview() {
  return request('/inflight');
}

/** 单 SKU 事中档案：段级时间轴 / 因果链 / 预警明细 */
export function fetchInflightSku(code) {
  return request(`/inflight/sku/${encodeURIComponent(code)}`);
}

/** 风险预警：当日新超期 / 持续超期 / 临近超期（85%） */
export function fetchInflightAlerts() {
  return request('/inflight/alerts');
}

/** 7 天进度变化（快照 diff） */
export function fetchInflightProgress(sku = null, days = 7) {
  const qs = new URLSearchParams({ days: String(days) });
  if (sku) qs.set('sku', sku);
  return request(`/inflight/progress?${qs.toString()}`);
}

/** 生成当日事中日报 xlsx（同日重跑覆盖） */
export function runDailyReport(date = null) {
  return postJson('/inflight/daily-report', date ? { date } : {});
}

/** 已生成的事中日报列表 */
export function fetchDailyReports() {
  return request('/inflight/daily-report');
}

/** 事中日报 xlsx 下载（浏览器直接下载） */
export function buildDailyReportUrl(date) {
  return appendCategory(
    `${BASE}/inflight/daily-report/download?date=${encodeURIComponent(date)}`);
}

/** 手动触发自动管线（导入→快照→日报→邮件，T05） */
export function runPipeline(skipMail = true) {
  return postJson('/inflight/pipeline/run', { skipMail });
}

/* ------------------ 事后评估：损益三指标（P1-3/P1-4） ------------------ */

/** 事后评估：时效 / 销量 / 金额三指标 + 汇总 */
export function fetchAssessment() {
  return request('/review/assessment');
}

/** 事后评估三指标 xlsx 下载（时效 / 销量 / 毛利 三 sheet） */
export function buildAssessmentExportUrl() {
  return appendCategory(`${BASE}/review/assessment/export`);
}
