/**
 * test_sse_parse.mjs —— SSE 解析器纯 JS 回归测试（零依赖，node 直接跑）
 *
 * 运行命令：
 *   cd frontend && E:/tools/nodejs/node.exe scripts/test_sse_parse.mjs
 *
 * 覆盖 F3：
 *   (a) `\r\n` **跨 chunk 边界** → 2 个事件（F3 的正身；变异「去掉结尾裸 \r 悬挂」必须变红）
 *   (b) 纯 LF 常规
 *   (c) 多行 `data:` 拼接（SSE 规范用 \n）
 *   (d) `id:` 捕获（供 Last-Event-ID 重连）
 *   (e) `\r\n` 与 `\n` 混用
 *   (f) 一个 chunk 内 2 帧 + 结尾残缺分隔符（flush 兜底）
 */

import { createSseParser } from '../src/hooks/sseParser.js';

let failed = 0;
function check(name, ok, detail) {
  if (ok) {
    console.log(`  PASS  ${name}`);
  } else {
    failed += 1;
    console.log(`  FAIL  ${name}${detail ? `  -> ${detail}` : ''}`);
  }
}

/** 依次喂入 chunks，再 flush，返回全部事件 */
function feed(chunks) {
  const p = createSseParser();
  let evs = [];
  for (const c of chunks) evs = evs.concat(p.push(c));
  evs = evs.concat(p.flush());
  return evs;
}
const datas = (evs) => evs.map((e) => e.data);
const eqArr = (a, b) => JSON.stringify(a) === JSON.stringify(b);

console.log('sseParser tests:');

// (a) 关键：\r\n 跨 chunk 边界
{
  const evs = feed(['data: a\r\n\r', '\ndata: b\r\n\r\n']);
  check('(a) CRLF 跨 chunk 边界 → 2 事件', eqArr(datas(evs), ['a', 'b']),
        `got ${JSON.stringify(datas(evs))}`);
}

// (b) 纯 LF
{
  const evs = feed(['data: a\n\n']);
  check('(b) 纯 LF → 1 事件', eqArr(datas(evs), ['a']), `got ${JSON.stringify(datas(evs))}`);
}

// (c) 多行 data 拼接
{
  const evs = feed(['data: l1\ndata: l2\n\n']);
  check('(c) 多行 data 拼接', eqArr(datas(evs), ['l1\nl2']), `got ${JSON.stringify(datas(evs))}`);
}

// (d) id 捕获
{
  const evs = feed(['id: 7\ndata: x\n\n']);
  const ok = evs.length === 1 && evs[0].id === '7' && evs[0].data === 'x';
  check('(d) id 捕获', ok, `got ${JSON.stringify(evs)}`);
}

// (e) \r\n 与 \n 混用
{
  const evs = feed(['data: a\r\n\r\ndata: b\n\n']);
  check('(e) CRLF 与 LF 混用 → 2 事件', eqArr(datas(evs), ['a', 'b']),
        `got ${JSON.stringify(datas(evs))}`);
}

// (f) 单 chunk 2 帧 + 结尾残缺分隔符（flush 兜底）
{
  const evs = feed(['data: a\r\n\r\ndata: b\r\n\r']);
  check('(f) 单 chunk 2 帧 + 残缺分隔符', eqArr(datas(evs), ['a', 'b']),
        `got ${JSON.stringify(datas(evs))}`);
}

// (g) 跨 chunk 多帧逐段送达（真流式：每 chunk 一个完整帧）
{
  const evs = feed(['data: one\n\n', 'data: two\n\n']);
  check('(g) 逐段送达多帧', eqArr(datas(evs), ['one', 'two']),
        `got ${JSON.stringify(datas(evs))}`);
}

if (failed > 0) {
  console.log(`\n${failed} test(s) FAILED`);
  process.exit(1);
}
console.log('\nALL PASS');
process.exit(0);
