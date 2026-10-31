import React from 'react';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Chip,
  Stack,
  Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import BuildCircleIcon from '@mui/icons-material/BuildCircle';

import { SEMANTIC } from '../theme.js';
import ApprovalCard from './ApprovalCard.jsx';

/**
 * AgentMessageList —— 消息/工具/审批 列表
 *
 * item 形状（由 AgentPanel 维护）：
 *   { type:'user',      text }
 *   { type:'assistant', text, streaming }
 *   { type:'tool',      id, name, args, status:'running'|'ok'|'error', result }
 *   { type:'notice',    text }
 *
 * 助手消息本轮**至少支持纯文本**：用 `white-space: pre-wrap` 保换行；
 * Markdown 渲染非本轮必须（后续可接 markdown 渲染器）。
 */

const TOOL_STATUS = {
  running: { color: 'info', label: '运行中' },
  ok: { color: 'success', label: '成功' },
  error: { color: 'error', label: '失败' },
};

/** 单条聊天气泡 */
function Bubble({ role, text, streaming }) {
  const isUser = role === 'user';
  return (
    <Box sx={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start' }}>
      <Box
        sx={{
          maxWidth: '85%',
          px: 1.5,
          py: 1,
          borderRadius: 2,
          border: isUser ? 'none' : `1px solid ${SEMANTIC.border}`,
          bgcolor: isUser ? 'primary.main' : 'background.paper',
          color: isUser ? 'primary.contrastText' : 'text.primary',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          fontSize: 14,
          lineHeight: 1.6,
        }}
      >
        {text}
        {streaming ? '▍' : ''}
      </Box>
    </Box>
  );
}

/** 工具调用事件 → 可折叠小卡片 */
function ToolCard({ item }) {
  const meta = TOOL_STATUS[item.status] || TOOL_STATUS.running;
  return (
    <Accordion
      disableGutters
      elevation={0}
      sx={{
        border: `1px solid ${SEMANTIC.border}`,
        borderRadius: 1,
        '&:before': { display: 'none' },
      }}
    >
      <AccordionSummary expandIcon={<ExpandMoreIcon fontSize="small" />}>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ width: '100%' }}>
          <BuildCircleIcon fontSize="small" sx={{ color: 'text.secondary' }} />
          <Typography variant="body2" sx={{ flexGrow: 1, fontWeight: 600 }}>
            {item.name || '工具'}
          </Typography>
          <Chip size="small" color={meta.color} label={meta.label} />
        </Stack>
      </AccordionSummary>
      <AccordionDetails sx={{ pt: 0 }}>
        <Typography variant="caption" color="text.secondary">参数</Typography>
        <Box
          sx={{
            p: 1, mb: 1, bgcolor: SEMANTIC.codeBg, borderRadius: 1,
            fontFamily: 'monospace', fontSize: 12, whiteSpace: 'pre-wrap',
            wordBreak: 'break-word', maxHeight: 160, overflow: 'auto',
          }}
        >
          {item.args || '（无）'}
        </Box>
        {item.result !== undefined && item.result !== null && item.result !== '' && (
          <>
            <Typography variant="caption" color="text.secondary">结果</Typography>
            <Box
              sx={{
                p: 1, bgcolor: SEMANTIC.codeBg, borderRadius: 1,
                fontFamily: 'monospace', fontSize: 12, whiteSpace: 'pre-wrap',
                wordBreak: 'break-word', maxHeight: 240, overflow: 'auto',
              }}
            >
              {typeof item.result === 'string' ? item.result : JSON.stringify(item.result, null, 2)}
            </Box>
          </>
        )}
      </AccordionDetails>
    </Accordion>
  );
}

export default function AgentMessageList({ items = [], approvals = [], onResolveApproval }) {
  return (
    <Stack spacing={1.25} sx={{ p: 1.5 }}>
      {items.map((it, idx) => {
        if (it.type === 'user' || it.type === 'assistant') {
          return <Bubble key={idx} role={it.type} text={it.text} streaming={it.streaming} />;
        }
        if (it.type === 'tool') {
          return <ToolCard key={it.id || idx} item={it} />;
        }
        if (it.type === 'notice') {
          return (
            <Typography key={idx} variant="caption" color="text.secondary" sx={{ px: 0.5 }}>
              {it.text}
            </Typography>
          );
        }
        return null;
      })}

      {approvals.map((ap) => (
        <ApprovalCard key={ap.id} item={ap} onResolve={onResolveApproval} />
      ))}
    </Stack>
  );
}
