import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  Drawer,
  IconButton,
  Tooltip,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import ViewListIcon from '@mui/icons-material/ViewList';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';

import { SEMANTIC } from '../theme.js';
import useAgentStream from '../hooks/useAgentStream.js';
import { scanSensitiveText } from '../agentContext.js';
import {
  historyItemsToPanelItems,
  makeSessionName,
} from '../agentSession.js';
import AgentMessageList from './AgentMessageList.jsx';
import AgentComposer from './AgentComposer.jsx';
import AgentSessionList from './AgentSessionList.jsx';
import {
  approveAgent,
  deleteAgentSession,
  enqueueAgent,
  fetchAgentHistory,
  fetchAgentPending,
  fetchAgentSessions,
  renameAgentSession,
  sendAgentChat,
  stopAgent,
} from '../api.js';

/**
 * AgentPanel —— AI 助手右侧抽屉（拓扑 B：前端原生 React 面板）
 *
 * - 用 `variant="temporary"`（右侧浮层）**不占布局**，不动现有 20 页；
 * - 事件流走 `useAgentStream`（fetch + ReadableStream 手写 SSE 解析）；
 * - 审批卡见 `ApprovalCard`；工具调用渲染成可折叠小卡片（见 AgentMessageList）；
 * - 会话侧栏见 `AgentSessionList`（列表/切换/新建/重命名/删除）。
 *
 * 会话模型（决策 5 → T05 扩展为多会话）：浏览器当前会话 id 持久化在 localStorage，
 * 用户可在侧栏切换/新建/重命名/删除；切换会话时重放该会话历史并重连其 SSE。
 */

const PANEL_WIDTH = 400;
const SESSION_KEY = 'agent_session_id';
const NOTICE_KEY = 'agent_notice_seen';

/** 稳定的浏览器级会话初始 id（只含 [A-Za-z0-9_-]，与 Agent `_scope()` 清洗一致） */
function getInitialSessionId() {
  try {
    const sid = localStorage.getItem(SESSION_KEY);
    if (sid) return sid;
    const fresh = makeSessionName();
    localStorage.setItem(SESSION_KEY, fresh);
    return fresh;
  } catch (e) {
    return 'web';
  }
}

/** 把当前会话名写回 localStorage（失败静默） */
function persistSessionId(sid) {
  try { localStorage.setItem(SESSION_KEY, sid); } catch (e) { /* 忽略 */ }
}

export default function AgentPanel({ open, onClose, getPlatContext }) {
  // session 必须是 state（而非 ref）：侧栏切换会话需要它参与渲染与依赖数组。
  const [session, setSession] = useState(getInitialSessionId);

  const [items, setItems] = useState([]);
  const [approvals, setApprovals] = useState([]);
  const [running, setRunning] = useState(false);
  const [hello, setHello] = useState(null);
  const [usage, setUsage] = useState(null);
  const [error, setError] = useState('');
  const [showNotice, setShowNotice] = useState(false);
  //: 出网敏感内容确认框的状态：{ text, kinds } | null（null = 不显示）
  const [riskDialog, setRiskDialog] = useState(null);
  //: 会话侧栏 { sessions, loading, visible }
  const [sessions, setSessions] = useState([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
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

  // ---- 会话列表刷新 ----
  const refreshSessions = useCallback(async () => {
    setSessionsLoading(true);
    try {
      const data = await fetchAgentSessions();
      setSessions(Array.isArray(data) ? data : []);
    } catch (e) {
      // 上游未起时静默（不阻塞主面板）；列表保持旧值
    } finally {
      setSessionsLoading(false);
    }
  }, []);

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
        // 有新消息会改变 size/mtime → 刷新会话列表
        refreshSessions();
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
        // level 契约：Agent 事件已下发 level（'external' / 'write' / …）；
        // 直读传给 ApprovalCard，用于判定是否给「本会话总允许」（旧 Agent 可能不带 → undefined）。
        setApprovals((prev) => [...prev, { id: ev.id, kind: 'permission',
                                           tool: ev.tool, preview: ev.preview,
                                           level: ev.level }]);
        break;
      case 'plan_request':
        setApprovals((prev) => [...prev, { id: ev.id, kind: 'plan', plan: ev.plan,
                                           level: ev.level }]);
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
  }, [refreshSessions]);

  const onStreamError = useCallback((err) => {
    setError(err?.message || String(err) || '助手事件流异常');
  }, []);

  const { start, stop, connected } = useAgentStream({
    session,
    onEvent: handleEvent,
    onError: onStreamError,
  });

  /**
   * 载入某会话：清空 → 重放历史（→ 之后由调用方 start → 重建审批卡）。
   *
   * `isCurrent()` 由调用方注入：异步返回后若已切到别的会话，则丢弃本次结果，
   * 避免「快速连续切换」时旧请求覆盖新会话 UI（经典竞态）。
   *
   * @param {string} sid 会话名
   * @param {Function} isCurrent 无参谓词：当前是否仍在该会话
   */
  const loadSession = useCallback(async (sid, isCurrent) => {
    const alive = typeof isCurrent === 'function' ? isCurrent : () => true;
    setItems([]);
    setApprovals([]);
    setRunning(false);
    setError('');
    try {
      const hist = await fetchAgentHistory(sid);
      if (!alive()) return;
      const raw = hist && Array.isArray(hist.items) ? hist.items : [];
      setItems(historyItemsToPanelItems(raw));
      if (hist && hist.running) setRunning(true);
      if (hist && hist.usage) setUsage(hist.usage);
    } catch (e) {
      // 会话不存在/上游未起：history 正常返回 200 空；真出错则静默留空
    }
  }, []);

  /** 重建某会话的未决审批卡（与历史载入拆开，供「历史重放后、SSE 已连」时调用） */
  const loadPending = useCallback(async (sid, isCurrent) => {
    const alive = typeof isCurrent === 'function' ? isCurrent : () => true;
    try {
      const data = await fetchAgentPending(sid);
      if (!alive()) return;
      const pending = (data && data.items) || [];
      if (pending.length) {
        setApprovals((prev) => {
          const known = new Set(prev.map((a) => a.id));
          const extra = pending
            .filter((p) => !known.has(p.id))
            .map((p) => ({ id: p.id, kind: 'permission', tool: p.tool,
                           preview: p.preview, level: p.level }));
          return [...prev, ...extra];
        });
      }
    } catch (e) { /* 上游未起时静默：连不上会在事件流错误里提示 */ }
  }, []);

  /**
   * 打开 / 切换会话的统一装配。
   *
   * 严格按序（切换会话最易踩的 SSE 时序）：
   *   stop()（先断旧流，防 lastId 串会话）→ 清空 → history 重放 → start()（连新会话）
   *   → fetchAgentPending（重建审批卡）。
   * 用 `cancelled` 标志守住竞态：若本 effect 已被后续切换取代，则中止后续步骤。
   */
  useEffect(() => {
    if (!open) {
      stop();
      return undefined;
    }
    let cancelled = false;
    const isCurrent = () => !cancelled;

    stop();                    // 先断（可能是旧会话的流）
    setError('');
    start();                   // 连新会话的 SSE（session 已是最新）
    refreshSessions();
    loadSession(session, isCurrent).then(() => {
      if (cancelled) return;
      return loadPending(session, isCurrent);
    });

    return () => { cancelled = true; };
  }, [open, start, stop, session, loadSession, loadPending, refreshSessions]);

  /** 切换会话：先 `stop()` 断旧流，再改 `session`。
   *  session 变化会触发上方主 effect，按序执行「清空 → history 重放 → start → pending」。 */
  const switchSession = useCallback((nextSession) => {
    if (!nextSession || nextSession === session) {
      setSidebarOpen(false);
      return;
    }
    stop();
    setSession(nextSession);
    persistSessionId(nextSession);
    setSidebarOpen(false);
  }, [session, stop]);

  /** 新建会话：生成合法名 → 切过去（首次发消息时 Agent 自动落盘） */
  const createSession = useCallback(() => {
    let name = makeSessionName();
    // 极小概率与现有会话重名（名字空间大，但稳妥起见重试几次）
    const existing = new Set(sessions.map((s) => s && s.name));
    let guard = 0;
    while (existing.has(name) && guard < 10) {
      name = makeSessionName();
      guard += 1;
    }
    stop();
    setSession(name);
    persistSessionId(name);
    setSidebarOpen(false);
  }, [sessions, stop]);

  const doRenameSession = useCallback(async (oldName, newName) => {
    setError('');
    try {
      const res = await renameAgentSession(oldName, newName);
      const finalName = (res && res.name) || newName;
      if (oldName === session) {
        // 当前会话被重命名：同步本地会话名并续上 SSE
        stop();
        setSession(finalName);
        persistSessionId(finalName);
      }
      refreshSessions();
    } catch (err) {
      setError(err?.message || String(err));
    }
  }, [session, stop, refreshSessions]);

  const doDeleteSession = useCallback(async (name) => {
    setError('');
    try {
      await deleteAgentSession(name);
      if (name === session) {
        // 删除的是当前会话 → 自动切到一个新会话
        stop();
        const fresh = makeSessionName();
        setSession(fresh);
        persistSessionId(fresh);
      }
      refreshSessions();
    } catch (err) {
      setError(err?.message || String(err));
    }
  }, [session, stop, refreshSessions]);

  /**
   * 真正执行发送/追加（已通过敏感内容确认，或本就没命中）。
   *
   * P1：随请求带上 `X-Plat-Context`（从 App 注入的 getPlatContext 取）。
   */
  const doSend = useCallback(async (text) => {
    const platContext = getPlatContext ? getPlatContext() : undefined;
    setError('');
    if (running) {
      try {
        await enqueueAgent(session, text, platContext);
      } catch (err) {
        setError(err?.message || String(err));
      }
      return;
    }
    setItems((prev) => [...prev, { type: 'user', text }]);
    try {
      await sendAgentChat({ session, message: text, platContext });
    } catch (err) {
      setError(err?.message || String(err));
      setItems((prev) => [...prev, { type: 'notice', text: `发送失败：${err?.message || err}` }]);
    }
  }, [running, session, getPlatContext]);

  /**
   * 发送入口：先做**UX 层**敏感内容提示（T10-b）。
   *
   * ⚠️ 前端 `scanSensitiveText` 只是 UX 提醒层，真正的强制保障是 BFF `_sanitize_outbound()`
   * 与 Agent 侧 `_plat_sanitize()`——前端检测挡不住任何东西，命中只影响「是否多问一句」。
   * 命中 → 弹确认框（取消=不发送）；未命中 → 直接发送，不打扰。
   */
  const handleSend = useCallback((text) => {
    const scan = scanSensitiveText(text);
    if (scan.hit) {
      setRiskDialog({ text, kinds: scan.kinds });
      return;
    }
    doSend(text);
  }, [doSend]);

  const confirmRiskSend = useCallback(() => {
    const pending = riskDialog;
    setRiskDialog(null);
    if (pending) doSend(pending.text);
  }, [riskDialog, doSend]);

  const cancelRiskSend = useCallback(() => setRiskDialog(null), []);

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
          {sidebarOpen ? (
            <IconButton size="small" onClick={() => setSidebarOpen(false)} aria-label="返回对话">
              <ArrowBackIcon fontSize="small" />
            </IconButton>
          ) : (
            <Tooltip title="会话列表">
              <IconButton size="small" onClick={() => setSidebarOpen(true)} aria-label="会话列表">
                <ViewListIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
          <SmartToyIcon sx={{ color: 'primary.main' }} />
          <Typography
            variant="h6"
            sx={{ flexGrow: 1, fontWeight: 700, fontSize: 16, overflow: 'hidden',
                  textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            title={sidebarOpen ? '会话' : session}
          >
            {sidebarOpen ? '会话' : 'AI 助手'}
          </Typography>
          {!sidebarOpen && (
            <Tooltip title={connected ? '事件流已连接' : '事件流未连接'}>
              <span>{statusChip}</span>
            </Tooltip>
          )}
          <IconButton size="small" onClick={onClose} aria-label="关闭助手">
            <CloseIcon fontSize="small" />
          </IconButton>
        </Box>
        <Divider />

        {sidebarOpen ? (
          <Box sx={{ flexGrow: 1, overflow: 'auto' }}>
            <AgentSessionList
              sessions={sessions}
              current={session}
              loading={sessionsLoading}
              onSelect={switchSession}
              onCreate={createSession}
              onRename={doRenameSession}
              onDelete={doDeleteSession}
            />
          </Box>
        ) : (
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
        )}

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

        {/* T10-b：出网敏感内容确认框（UX 层提示，非安全边界 —— 见 handleSend 注释） */}
        <Dialog open={Boolean(riskDialog)} onClose={cancelRiskSend}>
          <DialogTitle>内容可能包含个人信息</DialogTitle>
          <DialogContent>
            <DialogContentText>
              检测到：{riskDialog ? riskDialog.kinds.join('、') : ''}。本次消息将发送至
              <strong>云端模型</strong>（z.ai）处理。是否继续？
            </DialogContentText>
          </DialogContent>
          <DialogActions>
            <Button onClick={cancelRiskSend}>取消</Button>
            <Button color="warning" variant="contained" onClick={confirmRiskSend}>
              仍然发送
            </Button>
          </DialogActions>
        </Dialog>
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
