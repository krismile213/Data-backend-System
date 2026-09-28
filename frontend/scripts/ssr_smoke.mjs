/**
 * 一次性冒烟测试：用 Vite 的 SSR 模块加载能力把每个页面真实渲染一遍。
 *
 * 为什么需要它：机械替换（手写 useState+useEffect -> useApi）最容易犯的错是
 * 「引用了已删除的 state/setter」。esbuild 编译不做未定义变量检查，
 * 所以 dev server 显示"编译通过"并不代表页面能跑起来。
 * renderToString 会在初始渲染路径上真实执行组件体，能抓到 ReferenceError。
 *
 * 只测初始渲染：页面初始为 loading 态，不发请求、不碰 DOM。
 */
import { createServer } from 'vite';
import React from 'react';
import { renderToString } from 'react-dom/server';

/**
 * 把第三方图标 import 就地换成占位组件。
 *
 * 为什么需要：`@mui/icons-material/*` 是 CJS 包，SSR 走 Node 原生 ESM/CJS 互操作时，
 * `import SearchIcon from '@mui/icons-material/Search'` 拿到的是整个 `module.exports`
 * （形如 `{ __esModule: true, default: <组件> }`），React 于是报
 *   "Element type is invalid ... expected a string or a class/function but got: object"
 * 这是 harness 的**假阳性**，不是页面代码的问题 —— 浏览器端 Vite 会生成
 *   `const SearchIcon = mod.__esModule ? mod.default : mod;`
 * 正确解包（实测预构建 chunk 尾部就是 `export default require_Search();`）。
 *
 * 本脚本只关心「页面自身代码能不能渲染」，图标是第三方实现、长什么样无关紧要，
 * 所以直接换成占位组件，把这层噪声隔离掉 —— 否则含图标的页面会被这个假阳性永远卡住，
 * 页面里**真正**的错误反而被掩盖。
 *
 * 必须走 `transform`（改页面源码文本），不能走 `resolveId`/`load`：
 * SSR 下 Vite 把 node_modules 依赖标为 external，**直接绕过插件的 resolveId/load**，
 * 拦不住。而 src/ 下的页面一定会经过 transform 管线。
 *
 * 走过四条弯路，都无效，别再试：
 *   1. `ssr.optimizeDeps.include` —— SSR 下依然拿到模块对象，误报照旧；
 *   2. 自写 `resolveId` 拦成虚拟模块、再 `import` 真包 —— **无限递归**，进程卡死；
 *   3. `ssr.noExternal` —— Vite 把 CJS 当 ESM 处理，`require is not defined`，15 页全挂；
 *   4. `resolveId`/`load` 返回纯占位组件（不引用真包）—— 不被调用，静默无效。
 */
const iconStub = {
  name: 'icon-stub',
  enforce: 'pre',
  transform(code, id) {
    if (!id.includes('/src/') || !code.includes('@mui/icons-material/')) return null;
    const out = code.replace(
      /import\s+([A-Za-z_$][\w$]*)\s+from\s+['"]@mui\/icons-material\/[A-Za-z0-9]+['"];?/g,
      (_, local) => `const ${local} = (props) => React.createElement('svg', props);`,
    );
    return out === code ? null : { code: out, map: null };
  },
};



// 页面里会直接读 localStorage（如 Fillins 的填报人），SSR 下需要桩
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
  clear: () => store.clear(),
};

const PAGES = [
  'Alerts', 'Anomalies', 'Assessment', 'Compare', 'Dashboard', 'DataAdmin',
  'Fillins', 'Gantt', 'History', 'InflightDaily', 'Manual', 'Records',
  'Review', 'Routes', 'Rules', 'Scenarios', 'Skus', 'Transport', 'Watch',
];

const PROPS = {
  batch: '手机膜9.7',
  batches: [
    { name: '手机膜8.28' }, { name: '手机膜9.7' },
  ],
};

const server = await createServer({
  server: { middlewareMode: true },
  appType: 'custom',
  logLevel: 'error',
  plugins: [iconStub],
});


let ok = 0;
const failed = [];

for (const name of PAGES) {
  try {
    const mod = await server.ssrLoadModule(`/src/pages/${name}.jsx`);
    const Comp = mod.default;
    if (typeof Comp !== 'function') throw new Error('default 导出不是组件');
    const html = renderToString(React.createElement(Comp, PROPS));
    if (!html) throw new Error('渲染结果为空');
    console.log(`  OK    ${name.padEnd(12)} ${html.length} 字节`);
    ok += 1;
  } catch (e) {
    const msg = String(e && e.message ? e.message : e).split('\n')[0];
    console.log(`  FAIL  ${name.padEnd(12)} ${msg}`);
    failed.push(`${name}: ${msg}`);
  }
}

// 公共层也顺带渲染一次
for (const rel of [
  '/src/components/KpiCard.jsx', '/src/components/PageHeader.jsx',
  '/src/components/SectionCard.jsx', '/src/components/StateViews.jsx',
]) {
  try {
    const mod = await server.ssrLoadModule(rel);
    const keys = Object.keys(mod).filter((k) => k !== 'default');
    ok += 1;
    console.log(`  OK    ${rel.replace('/src/', '').padEnd(12)} 导出 ${keys.join(', ')}`);
  } catch (e) {
    failed.push(`${rel}: ${e.message}`);
    console.log(`  FAIL  ${rel} ${e.message}`);
  }
}

/**
 * CategorySelector 必须真渲染（它是顶栏组件，App.jsx 不在这份清单里）。
 *
 * 为什么单独测：手机壳的目标天/评级线是「继承名义值、待业务校准」，标记原先只挂在
 * **展开后的菜单项**上——收起状态完全看不出来，而顶栏恰恰是每一页都看得见的地方。
 * 现在把标记放到收起态的 renderValue 上，这里就钉住「收起态必须能看见」。
 */
try {
  const mod = await server.ssrLoadModule('/src/components/CategorySelector.jsx');
  const Comp = mod.default;
  if (typeof Comp !== 'function') throw new Error('default 导出不是组件');
  const CATS = [
    { category: '手机膜', categoryLabel: '手机膜', targetsProvisional: false },
    { category: '手机壳', categoryLabel: '手机壳', targetsProvisional: true },
  ];
  const render = (cat) => renderToString(
    React.createElement(Comp, { categories: CATS, category: cat, onChange: () => {} }));

  const shellHtml = render('手机壳');
  if (!shellHtml.includes('待校准')) {
    throw new Error('手机壳（provisional）收起态没有「待校准」标记');
  }
  const memHtml = render('手机膜');
  if (memHtml.includes('待校准')) {
    throw new Error('手机膜误挂了「待校准」标记（假阳性）');
  }
  // categories 还没拉到时也不能崩（初始渲染只有单元素兜底列表）
  renderToString(React.createElement(Comp, { categories: [], category: '手机膜', onChange: () => {} }));

  ok += 1;
  console.log('  OK    CategorySelector 收起态标记（手机壳有 / 手机膜无）');
} catch (e) {
  const msg = String(e && e.message ? e.message : e).split('\n')[0];
  failed.push(`CategorySelector: ${msg}`);
  console.log(`  FAIL  CategorySelector ${msg}`);
}

await server.close();

console.log(`\n结果：${ok} 通过 / ${failed.length} 失败`);
if (failed.length) {
  console.log('\n失败详情：');
  failed.forEach((f) => console.log('  - ' + f));
  process.exit(1);
}
