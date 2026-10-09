import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Chip,
  Divider,
  Drawer,
  IconButton,
  Tooltip,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import SmartToyIcon from '@mui/icons-material/SmartToy';

import { SEMANTIC } from '../theme.js';
import useAgentStream from '../hooks/useAgentStream.js';
import AgentMessageList from './AgentMessageList.jsx';
import AgentComposer from './AgentComposer.jsx';
import {
  approveAgent,
  enqueueAgent,
  fetchAgentPending,
  sendAgentChat,
  stopAgent,
} from '../api.js';

/**
 * AgentPanel —— AI 助手右侧抽屉（拓扑 B：前端原生 React 面板）
 *
 * - 用 `variant="temporary"`（右侧浮层）**不占布局**，不动现有 20 页；
 * - 事件流走 `useAgentStream`（fetch + ReadableStream 手写 SSE 解析）；
 * - 审批卡见 `ApprovalCard`；工具调用渲染成可折叠小卡片（见 AgentMessageList）。
 *
 * 会话模型（决策 5）：按浏览器一个「助手会话」，id 持久化在 localStorage。
 */

const PANEL_WIDTH = 400;
const SESSION_KEY = 'agent_session_id';
const NOTICE_KEY = 'agent_notice_seen';

/** 稳定的浏览器级会话 id */
function getSessionId() {
  try {
    let sid = localStorage.getItem(SESSION_KEY);
    if (!sid) {
      sid = `web-${Math.random().toString(36).slice(2, 10)}`;
      localStorage.setItem(SESSION_KEY, sid);
    }
    return sid;
  } catch (e) {
    return 'web';
  }
}

export default function AgentPanel({ open, onClose }) {
  const sessionRef = useRef(null);
  if (sessionRef.current === null) sessionRef.current = getSessionId();
  const session = sessionRef.current;

  const [items, setItems] = useState([]);
  const [approvals, setApprovals] = useState([]);
  const [running, setRunning] = useState(false);
  const [hello, setHello] = useState(null);
  const [usage, setUsage] = useState(null);
  const [error, setError] = useState('');
  const [showNotice, setShowNotice] = useState(false);
  const uidRef = useRef(0);

  // 首次打开给一条「数据出网告知」（T10）
  useEffect(() => {
    try {
      if (!localStorage.getItem(NOTICE_KEY)) setShowNotice(true);
    } catch (e) { /* 忽略 */ }
  }, []);

  const dismissNotice = () => {
    setShowNotice(false);
    try { localStorage.setItem(NOTICE_KEY, '1'); } catch (e) { /* 忽略 */ }
  };

  // ---- 事件 → 状态（助手流式/工具/审批 的分发） ----
  const handleEvent = useCallback((ev) => {
    if (!ev || !ev.kind) return;
    switch (ev.kind) {
      case 'hello':
        setHello(ev);
        break;
      case 'running':
        setRunning(Boolean(ev.value));
        if (ev.usage) setUsage(ev.usage);
        break;
      case 'assistant_delta':
        appendAssistant(setItems, ev.text || '', true);
        break;
      case 'assistant':
        appendAssistant(setItems, ev.text || '', false);
        break;
      case 'task_end':
        setRunning(false);
        if (ev.usage) setUsage(ev.usage);
        // 完整答案兜底：仅当此前**没有任何**助手文本时才补一条（否则会与流式/assistant 重复）
        if (ev.answer) {
          setItems((prev) => (
            prev.some((it) => it.type === 'assistant' && it.text)
              ? prev
              : [...prev, { type: 'assistant', text: ev.answer, streaming: false }]
          ));
        }
        break;
      case 'tool_call':
        setItems((prev) => [
          ...prev,
          { type: 'tool', id: `t${(uidRef.current += 1)}`, name: ev.name,
            args: ev.args, status: 'running' },
        ]);
        break;
      case 'tool_result':
        setItems((prev) => markTool(prev, ev.name, 'ok', ev.result));
        break;
      case 'permission_denied':
        setItems((prev) => markTool(prev, ev.name, 'error', '策略拒绝（permission_denied）'));
        break;
      case 'permission_request':
        setApprovals((prev) => [...prev, { id: ev.id, kind: 'permission',
                                           tool: ev.tool, preview: ev.preview }]);
        break;
      case 'plan_request':
        setApprovals((prev) => [...prev, { id: ev.id, kind: 'plan', plan: ev.plan }]);
        break;
      case 'step_request':
        setApprovals((prev) => [...prev, { id: ev.id, kind: 'step', step: ev.step,
                                          total: ev.total, text: ev.text }]);
        break;
      case 'permission_resolved':
        setApprovals((prev) => prev.filter((a) => a.id !== ev.id));
        break;
      case 'instruction_queued':
        setItems((prev) => [...prev, { type: 'notice', text: `已追加指令：${ev.text}` }]);
        break;
      case 'fatal':
        setRunning(false);
        setError(ev.message || '助手运行出错');
        break;
      default:
        break; // 其余引擎事件（compact/reflect/steps…）本轮不渲染
    }
  }, []);

  const onStreamError = useCallback((err) => {
    setError(err?.message || String(err) || '助手事件流异常');
  }, []);

  const { start, stop, connected } = useAgentStream({
    session,
    onEvent: handleEvent,
    onError: onStreamError,
  });

  // 打开即连（并尝试重建未决审批）；关闭即断
  useEffect(() => {
    if (!open) {
      stop();
      return undefined;
    }
    setError('');
    start();
    fetchAgentPending(session)
      .then((data) => {
        const pending = (data && data.items) || [];
        if (pending.length) {
          setApprovals((prev) => {
            const known = new Set(prev.map((a) => a.id));
            const extra = pending
              .filter((p) => !known.has(p.id))
              .map((p) => ({ id: p.id, kind: 'permission', tool: p.tool, preview: p.preview }));
            return [...prev, ...extra];
          });
        }
      })
      .catch(() => { /* 上游未起时静默：连不上会在事件流错误里提示 */ });
    return undefined;
  }, [open, start, stop, session]);

  const handleSend = useCallback(async (text) => {
    setError('');
    if (running) {
      try {
        await enqueueAgent(session, text);
      } catch (err) {
        setError(err?.message || String(err));
      }
      return;
    }
    setItems((prev) => [...prev, { type: 'user', text }]);
    try {
      await sendAgentChat({ session, message: text });
    } catch (err) {
      setError(err?.message || String(err));
      setItems((prev) => [...prev, { type: 'notice', text: `发送失败：${err?.message || err}` }]);
    }
  }, [running, session]);

  const handleStop = useCallback(async () => {
    try {
      await stopAgent(session);
    } catch (err) {
      setError(err?.message || String(err));
    }
  }, [session]);

  const handleResolveApproval = useCallback(async (id, approve, always) => {
    try {
      await approveAgent({ session, id, approve, always });
      setApprovals((prev) => prev.filter((a) => a.id !== id)); // 乐观移除
    } catch (err) {
      setError(err?.message || String(err));
    }
  }, [session]);

  const statusChip = connected
    ? <Chip size="small" color="success" label="已连接" />
    : <Chip size="small" color="default" label="未连接" />;

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      ModalProps={{ keepMounted: true }}
      sx={{
        // 临时抽屉默认 zIndex = theme.zIndex.drawer(1200)，会被 App.jsx 的
        // AppBar（drawer+1 = 1201）压住顶部；提到 modal(1300) 保证标题与关闭按钮可点。
        zIndex: (t) => t.zIndex.modal,
        '& .MuiDrawer-paper': { width: PANEL_WIDTH, maxWidth: '100vw' },
      }}
    >
      <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1.5, py: 1 }}>
          <SmartToyIcon sx={{ color: 'primary.main' }} />
          <Typography variant="h6" sx={{ flexGrow: 1, fontWeight: 700, fontSize: 16 }}>
            AI 助手
          </Typography>
          <Tooltip title={connected ? '事件流已连接' : '事件流未连接'}>
            <span>{statusChip}</span>
          </Tooltip>
          <IconButton size="small" onClick={onClose} aria-label="关闭助手">
            <CloseIcon fontSize="small" />
          </IconButton>
        </Box>
        <Divider />

        <Box sx={{ flexGrow: 1, overflow: 'auto' }}>
          {showNotice && (
            <Alert severity="info" onClose={dismissNotice} sx={{ m: 1.5, mb: 0 }}>
              回答由云端模型生成，助手可能收到当前页面信息；敏感字段将被屏蔽。
            </Alert>
          )}
          {error && (
            <Alert severity="error" onClose={() => setError('')} sx={{ m: 1.5, mb: 0 }}>
              {error}
            </Alert>
          )}
          {items.length === 0 && approvals.length === 0 && !error && (
            <Box sx={{ p: 3, textAlign: 'center' }}>
              <Typography variant="body2" color="text.secondary">
                {hello
                  ? `已就绪（模型：${hello.model || '未知'}）。在下方提问开始。`
                  : '正在连接助手…'}
              </Typography>
            </Box>
          )}
          <AgentMessageList
            items={items}
            approvals={approvals}
            onResolveApproval={handleResolveApproval}
          />
        </Box>

        <Divider />
        <AgentComposer
          onSend={handleSend}
          onStop={handleStop}
          running={running}
          disabled={!connected && !running}
        />
        <Box sx={{ px: 1.5, py: 0.75, bgcolor: SEMANTIC.neutralBg }}>
          <Typography variant="caption" color="text.secondary">
            回答由云端模型生成，助手可能收到当前页面信息；敏感字段将被屏蔽。
            {usage ? ` · 用量 ${JSON.stringify(usage)}` : ''}
          </Typography>
        </Box>
      </Box>
    </Drawer>
  );
}

// ---------------------------------------------------------------------------
// 纯函数：把事件落到 items（保持 AgentPanel 主体精简）
// ---------------------------------------------------------------------------

/** 追加助手文本：streaming=true 时并入最近一条「流式助手消息」；false 则用完整文本收尾同一条 */
function appendAssistant(setItems, text, streaming) {
  if (!text) return;
  setItems((prev) => {
    const last = prev[prev.length - 1];
    if (streaming) {
      if (last && last.type === 'assistant' && last.streaming) {
        const copy = prev.slice();
        copy[copy.length - 1] = { ...last, text: last.text + text };
        return copy;
      }
      return [...prev, { type: 'assistant', text, streaming: true }];
    }
    // 非流式完整文本：若当前正在流式，则用完整文本收尾**同一条**气泡，避免重复
    if (last && last.type === 'assistant' && last.streaming) {
      const copy = prev.slice();
      copy[copy.length - 1] = { ...last, text, streaming: false };
      return copy;
    }
    return [...prev, { type: 'assistant', text, streaming: false }];
  });
}

/** 把最近一个「运行中」的同名工具标记为最终状态 */
function markTool(prev, name, status, result) {
  for (let i = prev.length - 1; i >= 0; i -= 1) {
    const it = prev[i];
    if (it.type === 'tool' && it.name === name && it.status === 'running') {
      const copy = prev.slice();
      copy[i] = { ...it, status, result };
      return copy;
    }
  }
  return [...prev, { type: 'tool', id: `t-${name}`, name, args: '', status, result }];
}
