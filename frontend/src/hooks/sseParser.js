/**
 * sseParser.js —— 纯 SSE 分帧解析器（零依赖、不依赖 React，可被 node 直接单测）
 *
 * 帧格式（与 Agent `server.py:841-842` 一致）：`id: <seq>\ndata: <json>\n\n`。
 *
 * ⚠️ 关键坑（F3）：SSE 的帧分隔可能是 `\r\n\r\n`，而网络分块**可能正好切在
 *    `\r\n` 中间**（一个 chunk 以裸 `\r` 结尾，下一个 chunk 以 `\n` 开头）。
 *    若把每个 chunk 各自把 `\r\n` 归一成 `\n` 后再拼接，就会丢掉这条分界线：
 *    实际只解析出 1 个事件而非 2 个。见 `test_sse_parse.mjs` 用例 (a)。
 *
 * 因此本解析器：
 *   1) **结尾裸 `\r` 悬挂（hold）到下一次**，与下一 chunk 一起归一 —— 这是修复 F3 的关键；
 *   2) 归一化只对「本次送入且不含悬挂 `\r` 的部分」做 `\r\n → \n`；
 *   3) 帧分隔按 `\n\n`（归一后即等价于 `/\r?\n\r?\n/`）；
 *   4) 行分隔按 `/\r?\n/`；`data:` 多行按 SSE 规范用 `\n` 拼接；
 *   5) `id:` 记录在事件上，供重连回传 `Last-Event-ID`。
 *
 * 支持行尾：LF、CRLF（本 Agent 实际用法）。**不支持**裸 CR 作为行尾（极罕见，且为
 * 保证「悬挂 `\r`」这一修复点可变异验证而不被掩盖，这里刻意不做裸 CR→LF 的兜底转换）。
 *
 * 用法：
 *   const p = createSseParser();
 *   const events = p.push(chunkText);   // 返回本次析出的完整事件数组（0..N）
 *   const rest = p.flush();             // 流结束时把残留当最后一帧解析
 *   // event = { id: string|null, data: string }
 */

/** 解析单帧文本 → {id, data}；无 `data:` 行返回 null */
function parseFrame(raw) {
  const lines = String(raw).split(/\r?\n/);
  let id = null;
  const dataLines = [];
  for (const line of lines) {
    if (!line || line.startsWith(':')) continue; // 空行 / 注释 / 心跳
    if (line.startsWith('id:')) {
      id = line.slice(3).trim();
    } else if (line.startsWith('data:')) {
      const v = line.slice(5);
      dataLines.push(v.startsWith(' ') ? v.slice(1) : v);
    }
  }
  if (dataLines.length === 0) return null;
  return { id, data: dataLines.join('\n') };
}

export function createSseParser() {
  let buffer = '';      // 已归一化、待提取的累计文本
  let pendingCR = '';   // 上一轮结尾悬挂的裸 `\r`（等待可能的 `\n`）

  return {
    /**
     * 送入一段文本，返回本次析出的完整事件数组。
     * @param {string} chunk
     * @returns {Array<{id: (string|null), data: string}>}
     */
    push(chunk) {
      // 1) 把悬挂的 `\r` 与本次 chunk 拼在一起再归一 —— 跨 chunk 的 `\r\n` 才能拼上
      let text = pendingCR + (chunk == null ? '' : String(chunk));
      pendingCR = '';
      // 2) 结尾若是裸 `\r`，先悬挂（可能在等下一 chunk 的 `\n`）
      if (text.endsWith('\r')) {
        pendingCR = '\r';
        text = text.slice(0, -1);
      }
      // 3) 只归一化非悬挂部分
      text = text.replace(/\r\n/g, '\n');
      buffer += text;

      // 4) 提取所有以 `\n\n` 结尾的完整帧
      const out = [];
      let idx;
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const ev = parseFrame(raw);
        if (ev) out.push(ev);
      }
      return out;
    },

    /** 流结束时调用：把残留缓冲（无结尾空行的最后一帧）解析出来 */
    flush() {
      let rest = pendingCR + buffer;
      pendingCR = '';
      buffer = '';
      // 悬挂的裸 `\r` 若最终没能配上 `\n`（帧分隔被切在 `\r` 处且流就此结束），
      // 它属于一个残缺的分隔符，EOF 时丢弃首/尾的它。
      rest = rest.replace(/\r\n/g, '\n').replace(/^\r/, '').replace(/\r$/, '');
      if (rest.trim() === '') return [];
      const ev = parseFrame(rest);
      return ev ? [ev] : [];
    },
  };
}

export default createSseParser;
