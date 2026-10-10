/**
 * agentContext.js — 平台上下文（`X-Plat-Context`）构造 与 出网敏感文本的 UX 提示
 * ---------------------------------------------------------------------------
 * 定位：这是**纯 JS 模块**（无 JSX、不 import React/MUI），以便在 node 里被
 * `scripts/test_agent_context.mjs` 直接 `import` 跑回归（同 `hooks/sseParser.js` 的理由）。
 *
 * 两个职责：
 *   1. `buildPlatContext` / `encodePlatContext`：把「当前所在页面/品类/批次」等拼成
 *      `X-Plat-Context` 头（形状严格对齐《Agent侧对接任务书》§4.1）。BFF 会再脱敏白名单。
 *   2. `scanSensitiveText`：发送前扫一遍用户输入，命中手机号/邮箱/身份证/关键词时**弹确认框**。
 *
 * ⚠️ 前端 `scanSensitiveText` 只是 UX 提醒层，真正的强制保障是 BFF `_sanitize_outbound()`
 *    与 Agent 侧 `_plat_sanitize()`。前端检测只影响「是否多问一句」，挡不住任何东西，
 *    **不要**把它当成安全边界。
 *
 * 契约：docs/AI助手T10-P1实现契约.md §3.1。
 */

/** 上下文请求头名（BFF、Agent 两侧都按大小写不敏感读取；这里统一大写书写）。 */
export const CONTEXT_HEADER = 'X-Plat-Context';

/** 单个上下文值最大字符数（与后端 outbound_guard.CONTEXT_VALUE_MAX 对齐）。 */
export const CONTEXT_VALUE_MAX = 80;

/** 关键词类敏感线索（命中即提示，用于弹窗文案）。 */
const SENSITIVE_KEYWORDS = ['负责人', '备注', '手机', '电话', '联系方式'];

/** 11 位手机号（前后不夹数字，避免误伤长数字串）。 */
const PHONE_RE = /(?<!\d)1[3-9]\d{9}(?!\d)/;
/** 常见邮箱。 */
const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/;
/** 18 位身份证号（末位可为 X/x）。 */
const IDCARD_RE = /(?<!\d)\d{17}[\dXx](?!\d)/;

/** 归一化单个上下文值：null/undefined → ''；转字符串；trim；超长截断。 */
function normValue(value) {
  if (value === undefined || value === null) return '';
  let s = String(value).trim();
  if (s.length > CONTEXT_VALUE_MAX) s = s.slice(0, CONTEXT_VALUE_MAX);
  return s;
}

/**
 * 构造平台上下文对象（形状严格对齐任务书 §4.1）。
 *
 * @param {object} [input]
 * @param {string} [input.page]         页面 key（如 'dashboard'）
 * @param {string} [input.pageLabel]    页面对应中文标签（取自 App.jsx 既有 NAV_ITEMS，勿另建表）
 * @param {string} [input.category]     当前品类（如 '手机膜'）
 * @param {string} [input.batch]        当前批次
 * @param {string} [input.selectedSku]  当前选中 SKU（本轮恒为空，见契约 §6）
 * @returns {{page:string, page_label:string, category:string, batch:string,
 *            selected_sku:string, filters:object}} 缺省一律空串，filters 本轮恒为 {}。
 */
export function buildPlatContext({ page, pageLabel, category, batch, selectedSku } = {}) {
  return {
    page: normValue(page),
    page_label: normValue(pageLabel),
    category: normValue(category),
    batch: normValue(batch),
    selected_sku: normValue(selectedSku),
    filters: {},
  };
}

/**
 * 把上下文对象编码成**纯 ASCII** 的头值（中文 percent-encode）。
 *
 * ⚠️ 只编码**一次**：调用方拿到后直接放进请求头，**不得**再 `encodeURIComponent`。
 *
 * @param {object} ctx 上下文对象（通常来自 buildPlatContext）。
 * @returns {string} percent-encoded 字符串；编码失败（如含 BigInt）返回 ''。
 */
export function encodePlatContext(ctx) {
  try {
    return encodeURIComponent(JSON.stringify(ctx || {}));
  } catch (e) {
    return '';
  }
}

/**
 * 扫描文本里的敏感线索（仅用于**是否弹确认框**的 UX 判断，不做任何脱敏）。
 *
 * @param {string} text 用户输入。
 * @returns {{hit:boolean, kinds:string[]}} hit 为是否有命中；kinds 为命中的种类文案。
 */
export function scanSensitiveText(text) {
  const kinds = [];
  const s = text === undefined || text === null ? '' : String(text);
  try {
    if (PHONE_RE.test(s)) kinds.push('手机号');
    if (EMAIL_RE.test(s)) kinds.push('邮箱');
    if (IDCARD_RE.test(s)) kinds.push('身份证号');
    const hitKeywords = SENSITIVE_KEYWORDS.filter((k) => s.includes(k));
    if (hitKeywords.length) kinds.push(`敏感词（${hitKeywords.join('、')}）`);
  } catch (e) {
    return { hit: false, kinds: [] };
  }
  return { hit: kinds.length > 0, kinds };
}
