/**
 * category.js — 当前品类的全局单例（手机膜 / 手机壳）
 * ==========================================================
 * 后端自 2026-09-22 起按品类分发规则与批次：同一批接口带 `?category=` 就切换
 * 口径（默认品类 = 手机膜，不传时行为与改造前一致）。
 *
 * 前端零散地给每个页面加参数太容易漏，所以这里做成**一个来源**：
 *   · api.js 的 request() 统一注入（后端对不认识的接口会忽略该参数）；
 *   · 导出 URL 用 appendCategory() 补上；
 *   · 取值优先级 URL > localStorage > 默认，保证刷新/分享链接口径一致。
 */

export const DEFAULT_CATEGORY = '手机膜';

const STORE_KEY = 'wb.category';

function fromUrl() {
  try {
    return new URLSearchParams(window.location.search).get('category') || '';
  } catch (e) {
    return '';
  }
}

function fromStore() {
  try {
    return window.localStorage.getItem(STORE_KEY) || '';
  } catch (e) {
    return '';
  }
}

/** 当前品类（URL 优先，便于把带品类的链接直接发给同事） */
export function getCategory() {
  return fromUrl() || fromStore() || DEFAULT_CATEGORY;
}

/** 记住当前品类（下一次打开或刷新后仍然生效） */
export function setCategory(name) {
  try {
    if (name) window.localStorage.setItem(STORE_KEY, name);
    else window.localStorage.removeItem(STORE_KEY);
  } catch (e) {
    /* 隐私模式下 localStorage 可能不可用 —— 退化为仅 URL 生效 */
  }
}

/** 给已拼好的 URL（相对或绝对）追加 category；已有则原样返回 */
export function appendCategory(url, category) {
  const cat = category || getCategory();
  if (!cat || /[?&]category=/.test(url)) return url;
  return `${url}${url.includes('?') ? '&' : '?'}category=${encodeURIComponent(cat)}`;
}
