import React from 'react';
import { Box, Paper, Stack, Button, Typography } from '@mui/material';
import { SEMANTIC } from '../theme.js';

/**
 * ApprovalCard —— 审批卡（收到 permission_request / plan_request / step_request 时渲染）
 *
 * 事件 payload 见方案 §4.2：
 *   permission_request {id, tool, preview}
 *   plan_request       {id, plan}
 *   step_request       {id, step, total, text}
 *
 * 按钮规则：
 *   允许 / 本会话总允许 / 拒绝
 *   —— 仅「写级工具审批」给「本会话总允许」；`external` 级（对外发送）**不给**，
 *      避免一次授权把对外发送全部放行（§8.3）。
 *
 * ⚠️ P0 局限：Agent 当前 `permission_request` 事件**不带 level 字段**（server.py:154-156），
 *    因此这里用工具名关键字**启发式**判断是否疑似 external；等 Agent 在事件里补上
 *    `level` 后应改为直接读该字段。TODO(Agent契约)：事件增加 level。
 */

const EXTERNAL_HINTS = ['push', 'send', 'mail', 'dingtalk', 'notify', 'webhook', 'external'];

/** 工具名是否疑似「对外发送」级（启发式，见文件头 TODO） */
function looksExternal(tool) {
  const t = String(tool || '').toLowerCase();
  return EXTERNAL_HINTS.some((k) => t.includes(k));
}

const TITLES = {
  permission: '助手请求执行工具',
  plan: '助手提交计划待确认',
  step: '助手请求确认步骤',
};

/** 展示区块（preview / plan / step 文本）统一外观 */
function Block({ children }) {
  return (
    <Box
      sx={{
        mt: 0.75,
        p: 1,
        bgcolor: SEMANTIC.codeBg,
        borderRadius: 1,
        fontFamily: 'monospace',
        fontSize: 12,
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        maxHeight: 200,
        overflow: 'auto',
      }}
    >
      {children}
    </Box>
  );
}

export default function ApprovalCard({ item, onResolve }) {
  const { id, kind, tool, preview, plan, step, total, text } = item;
  const allowAlways = kind === 'permission' && !looksExternal(tool);

  return (
    <Paper
      sx={{
        p: 1.5,
        border: `1px solid ${SEMANTIC.warning}`,
        borderRadius: 2,
        bgcolor: SEMANTIC.warningBg,
      }}
    >
      <Typography variant="subtitle2" sx={{ fontWeight: 700, color: SEMANTIC.warningDeep }}>
        {TITLES[kind] || '助手请求确认'}
      </Typography>

      {tool && (
        <Typography variant="body2" sx={{ mt: 0.5 }}>
          工具：<b>{tool}</b>
        </Typography>
      )}
      {preview && <Block>{preview}</Block>}
      {kind === 'plan' && plan && <Block>{plan}</Block>}
      {kind === 'step' && (
        <Typography variant="body2" sx={{ mt: 0.5 }}>
          步骤 {step}/{total}：{text}
        </Typography>
      )}

      <Stack direction="row" spacing={1} sx={{ mt: 1.5 }} flexWrap="wrap" useFlexGap>
        <Button size="small" variant="contained" color="success"
                onClick={() => onResolve(id, true, false, kind)}>
          允许
        </Button>
        {allowAlways && (
          <Button size="small" variant="outlined" color="warning"
                  onClick={() => onResolve(id, true, true, kind)}>
            本会话总允许
          </Button>
        )}
        <Button size="small" variant="outlined" color="error"
                onClick={() => onResolve(id, false, false, kind)}>
          拒绝
        </Button>
      </Stack>
    </Paper>
  );
}
