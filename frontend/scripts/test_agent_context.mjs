/**
 * test_agent_context.mjs —— agentContext.js 纯 JS 回归测试（零依赖，node 直接跑）
 *
 * 运行命令：
 *   cd frontend && E:/tools/nodejs/node.exe scripts/test_agent_context.mjs
 *
 * 覆盖（契约 docs/AI助手T10-P1实现契约.md §3.1 / §5）：
 *   1) buildPlatContext 输出形状（6 键，缺省空串，filters 恒 {}）
 *   2) **未知键不进头**（只出白名单字段）
 *   3) 值 trim + 超长截断
 *   4) 中文 percent-encode 且 decodeURIComponent 能还原
 *   5) 编码结果纯 ASCII
 *   6) encodePlatContext 异常（BigInt）→ 返回 ''
 *   7) page_label 与 App.jsx 既有 NAV_ITEMS **一致**（从源文件提取，杜绝另建标签表）
 *   8) scanSensitiveText 命中（手机号/邮箱/身份证/关键词）与不命中
 */

import { readFileSync } from 'node:fs';

import {
  CONTEXT_HEADER,
  CONTEXT_VALUE_MAX,
  buildPlatContext,
  encodePlatContext,
  scanSensitiveText,
} from '../src/agentContext.js';

let failed = 0;
function check(name, ok, detail) {
  if (ok) {
    console.log(`  PASS  ${name}`);
  } else {
    failed += 1;
    console.log(`  FAIL  ${name}${detail ? `  -> ${detail}` : ''}`);
  }
}
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

console.log('agentContext tests:');

// (1) 输出形状：6 键 + 缺省空串 + filters 恒 {}
{
  const ctx = buildPlatContext({});
  check('(1) 缺省 → 6 键全空串 + filters {}',
        eq(ctx, { page: '', page_label: '', category: '', batch: '',
                  selected_sku: '', filters: {} }),
        JSON.stringify(ctx));
  check('(1b) 键集合 = 白名单 6 键',
        eq(Object.keys(ctx).sort(),
           ['batch', 'category', 'filters', 'page', 'page_label', 'selected_sku']),
        JSON.stringify(Object.keys(ctx)));
}

// (2) 未知键不进头（buildPlatContext 只映射白名单字段）
{
  const ctx = buildPlatContext({ page: 'dashboard', evil: 'x', owner: '张三' });
  check('(2) 未知键不进上下文',
        !('evil' in ctx) && !('owner' in ctx) && ctx.page === 'dashboard',
        JSON.stringify(ctx));
}

// (3) trim + 截断
{
  const long = 'a'.repeat(CONTEXT_VALUE_MAX + 30);
  const ctx = buildPlatContext({ batch: '  B1  ', category: long });
  check('(3) 值 trim', ctx.batch === 'B1', JSON.stringify(ctx.batch));
  check('(3b) 值截断到 CONTEXT_VALUE_MAX', ctx.category.length === CONTEXT_VALUE_MAX,
        `len=${ctx.category.length}`);
}

// (4) 中文 percent-encode 且可还原
{
  const ctx = buildPlatContext({ page: 'dashboard', pageLabel: '时效看板',
                                 category: '手机膜', batch: 'B1' });
  const enc = encodePlatContext(ctx);
  const back = JSON.parse(decodeURIComponent(enc));
  check('(4) 中文可 decodeURIComponent 还原',
        eq(back, ctx), JSON.stringify(back));
  check('(4b) 编码结果纯 ASCII', enc === Buffer.from(enc, 'ascii').toString('ascii')
        && !/[^\x00-\x7F]/.test(enc), enc);
  check('(4c) 中文确实被 percent-encode',
        enc.includes('%E6%97%B6%E6%95%88%E7%9C%8B%E6%9D%BF'), enc);
}

// (5) CONTEXT_HEADER 常量
check('(5) CONTEXT_HEADER 常量正确', CONTEXT_HEADER === 'X-Plat-Context', CONTEXT_HEADER);

// (6) encodePlatContext 异常 → ''
{
  const enc = encodePlatContext({ a: 1n });   // BigInt 不可 JSON.stringify
  check('(6) 异常输入 → 返回空串', enc === '', JSON.stringify(enc));
  const encNull = encodePlatContext(null);
  check('(6b) null → 合法空对象编码', encNull === encodeURIComponent('{}'), encNull);
}

// (7) page_label 与 App.jsx 既有 NAV_ITEMS 一致（从源文件提取，避免口径漂移）
{
  const appSrc = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');
  const labelMap = {};
  for (const m of appSrc.matchAll(/\{\s*key:\s*'([^']+)',\s*label:\s*'([^']+)'/g)) {
    labelMap[m[1]] = m[2];
  }
  const sample = ['dashboard', 'alerts', 'inflight'];
  const ok = sample.every((k) => {
    if (!labelMap[k]) return false;
    const ctx = buildPlatContext({ page: k, pageLabel: labelMap[k] });
    return ctx.page_label === labelMap[k];
  });
  check('(7) page_label 与 App NAV_ITEMS 一致',
        ok && Object.keys(labelMap).length >= 15,
        `labels=${Object.keys(labelMap).length}, ${JSON.stringify(labelMap)}`);
}

// (8) scanSensitiveText 命中与不命中
{
  const c1 = scanSensitiveText('客户电话 13812345678');
  check('(8) 命中手机号', c1.hit && c1.kinds.includes('手机号'),
        JSON.stringify(c1));

  const c2 = scanSensitiveText('邮箱 foo.bar@example.com');
  check('(8b) 命中邮箱', c2.hit && c2.kinds.includes('邮箱'), JSON.stringify(c2));

  const c3 = scanSensitiveText('身份证 11010119900307721X');
  check('(8c) 命中身份证', c3.hit && c3.kinds.includes('身份证号'), JSON.stringify(c3));

  const c4 = scanSensitiveText('这条记录谁负责？有个备注要补');
  check('(8d) 命中敏感词', c4.hit && c4.kinds.some((k) => k.includes('敏感词')),
        JSON.stringify(c4));

  const c5 = scanSensitiveText('这个批次的时效达标率是多少');
  check('(8e) 普通文本不命中', c5.hit === false && c5.kinds.length === 0,
        JSON.stringify(c5));

  const c6 = scanSensitiveText('编号 138123456789 无需掩码处理');
  check('(8f) 前后夹数字不误判', c6.hit === false, JSON.stringify(c6));
}

if (failed > 0) {
  console.log(`\n${failed} test(s) FAILED`);
  process.exit(1);
}
console.log('\nALL PASS');
process.exit(0);
