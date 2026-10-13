/**
 * agentSession.js —— 会话侧栏的**纯逻辑**（零依赖，便于 node 直接断言）
 *
 * 这里只放不依赖 React / DOM 的纯函数：
 *   1. `historyItemsToPanelItems` —— 把 Agent `/{name}/history` 返回的 items
 *      转成 AgentPanel 的 items 形状（两者结构不同，必须逐条适配）；
 *   2. `cleanSessionName` —— 复刻 Agent 侧 `_scope()` 的名字清洗规则，
 *      用于前端生成/校验会话名（保证前端持有的名字与 Agent 落盘的一致）；
 *   3. `isValidSessionName` —— 名字合法性（前端生成的名字必须只含 [A-Za-z0-9_-]）。
 *
 * ⚠️ 为什么单独成文件：`.jsx` 无法被 node 直接 import。把这些纯逻辑抽到这个
 *    零依赖 `.js`（ESM）里，node 断言脚本即可 import 同一份实现来跑，避免"测试逻辑
 *    与产品逻辑各写一份、改一处漏一处"。
 */

// ---------------------------------------------------------------------------
// 1) history items → AgentPanel items 适配器
// ---------------------------------------------------------------------------

/**
 * 把 Agent `/{name}/history` 的 items 适配成 AgentPanel 的 items。
 *
 * 结构对照：
 *   history                       → panel
 *   {kind:'user', text}           → {type:'user', text}
 *   {kind:'assistant', text}      → {type:'assistant', text, streaming:false}
 *   {kind:'tool_call', name,args} → {type:'tool', id, name, args, status:'running'}
 *   {kind:'tool_result', name,result} → 优先**按 name** 配对（回退 **FIFO**）置 {status:'ok', result}
 *   {kind:'reflect', critique}    → {type:'notice', text}（渲染成提示，不丢信息）
 *   其它 / 非法项                  → 跳过（不崩）
 *
 * ⚠️ tool_result 配对规则（**改自旧版 LIFO，修复多工具调用结果错配**）：
 *   一条 assistant 消息可能同时发起**多个** tool_call。`/history` 重建时会把该消息的
 *   所有 tool_call **先依次列出**，随后各 tool_result **按同一顺序**列出
 *   （server.py:477-483）。因此正确配对是：
 *     1) **优先按 name** —— 与 SSE 直播路径一致（直播事件本就带 name）；
 *        eng-agent 已让 `/history` 的 tool_result 补回 name（tool_call_id → name）。
 *        从**前往后**找**同名**且仍 running 的第一条（同名重复时 = 第一个 call 配第一个 result）。
 *     2) **回退 FIFO** —— name 缺失（旧 Agent 仍落空串）或无匹配时，从前往后找
 *        **第一个**仍 running 的 tool。**为什么是 FIFO 而不是 LIFO**：多 tool_calls
 *        是"call 先全列、result 同序跟随"，第一个 call 对应第一个 result；
 *        旧的 LIFO（找最后一个 running）会把首尾两条结果的归属**对调**。
 *     3) 都无匹配 → 补一条 ok 的 tool（不丢信息）。
 *
 *   为什么「按 name」与「回退 FIFO」都用**从前往后**：两者本质都是"第 k 个 result
 *   配第 k 个尚未配对的 call"。按 name 只是先用名字把候选缩小到同名集合，同一集合内
 *   仍应按出现顺序（FIFO）配。用从后往前会在**同名重复调用**时把结果对调，与地面真值相悖。
 *
 * @param {Array<object>} raw history 原始 items（可能为 undefined/null/非数组）
 * @returns {Array<object>} AgentPanel 形状的 items
 */
export function historyItemsToPanelItems(raw) {
  const out = [];
  const list = Array.isArray(raw) ? raw : [];
  for (let i = 0; i < list.length; i += 1) {
    const it = list[i];
    if (!it || typeof it !== 'object') continue;
    const kind = it.kind;
    if (kind === 'user') {
      out.push({ type: 'user', text: it.text || '' });
    } else if (kind === 'assistant') {
      out.push({ type: 'assistant', text: it.text || '', streaming: false });
    } else if (kind === 'tool_call') {
      out.push({
        type: 'tool',
        id: `h-${i}-${Math.random().toString(36).slice(2, 8)}`,
        name: it.name || '',
        args: it.args || '',
        status: 'running',
      });
    } else if (kind === 'tool_result') {
      const nm = it.name || '';
      let matched = false;

      // 1) 优先按 name 配对（与 SSE 直播路径**结果一致**；同名多条时取**第一个** running）
      //    ⚠️ 这里用**从前往后**（FIFO）而非从后往前：多 tool_calls 是「call 先全列、
      //    result 同序跟随」，同名重复调用时第一个 result 属于第一个 call（见文件头说明）。
      //    直播路径靠 交错到达 天然也是「先配先」，故 FIFO 才真正与直播一致。
      if (nm) {
        for (let j = 0; j < out.length; j += 1) {
          if (out[j].type === 'tool' && out[j].status === 'running' && out[j].name === nm) {
            out[j] = { ...out[j], status: 'ok', result: it.result };
            matched = true;
            break;
          }
        }
      }

      // 2) 回退 FIFO：name 缺失或无匹配时，取**第一个**仍 running 的 tool。
      //    为什么是 FIFO：/history 里一条 assistant 的多个 tool_call 先全列，
      //    各 tool_result 再按同一顺序跟随（server.py:477-483），故首 call 配首 result。
      if (!matched) {
        for (let j = 0; j < out.length; j += 1) {
          if (out[j].type === 'tool' && out[j].status === 'running') {
            out[j] = { ...out[j], status: 'ok', result: it.result };
            matched = true;
            break;
          }
        }
      }

      // 3) 无匹配：补一条 ok 的 tool（保持信息完整，不丢 result）
      if (!matched) {
        out.push({
          type: 'tool',
          id: `h-${i}-${Math.random().toString(36).slice(2, 8)}`,
          name: it.name || '',
          args: '',
          status: 'ok',
          result: it.result,
        });
      }
    } else if (kind === 'reflect') {
      const critique = it.critique || it.text || '';
      if (critique) out.push({ type: 'notice', text: `（反思）${critique}` });
    }
    // 其它 kind（compact/steps/…）忽略，与 AgentPanel 的 default 分支一致
  }
  return out;
}

// ---------------------------------------------------------------------------
// 2) 会话名清洗 / 校验
// ---------------------------------------------------------------------------

/**
 * 复刻 Agent 侧 `_scope()` 的名字清洗（server.py:396）：
 *   `name = "".join(c for c in (name or "").strip() if c.isalnum() or c in "-_") or "web"`
 *
 * 语义要点（逐条对齐 Python）：
 *   - 先 `.strip()`：只剥**首尾**空白（内部空白会被下面的过滤剔掉，效果等同剔除）；
 *   - 保留：`c.isalnum()` 为真（**Unicode 字母/数字，因此中文保留**）或 `-` `_`；
 *   - 其余（含点号 `.`、空格、斜杠）一律剥掉；
 *   - 结果为空 → 回落 `"web"`。
 *
 * JS 用 `\p{L}\p{N}` 近似 Python `str.isalnum()`（Unicode 字母 / 数字类别）。
 * ⚠️ 点号会被剥掉 —— 所以前端**生成**会话名时也别用 `.`，否则前端持有名 vs
 *    Agent 实际落盘名对不上，后续 history/rename/delete 全 404。
 *
 * @param {string} name 原始名字
 * @returns {string} 清洗后的名字（非空）
 */
export function cleanSessionName(name) {
  const s = String(name == null ? '' : name).trim();
  const cleaned = s.replace(/[^\p{L}\p{N}_-]/gu, '');
  return cleaned || 'web';
}

/**
 * 名字是否合法（清洗后与原名一致，且非空）。
 * 前端**生成**的名字通过此校验即为安全：与 Agent 落盘名字一致。
 *
 * 注意：合法集在 `_scope()` 语义下其实含中文，但前端**生成**的名字只取
 * `[A-Za-z0-9_-]` 这个子集（见 `makeSessionName`），最稳妥。
 *
 * @param {string} name 待校验名字
 * @returns {boolean}
 */
export function isValidSessionName(name) {
  if (typeof name !== 'string' || name.length === 0) return false;
  return cleanSessionName(name) === name;
}

/**
 * 生成一个新的、合法的会话名：`web-<8 位小写字母数字>`。
 * 只含 `[A-Za-z0-9_-]`，`cleanSessionName` 清洗不变（见断言）。
 *
 * @returns {string}
 */
export function makeSessionName() {
  return `web-${Math.random().toString(36).slice(2, 10)}`;
}
