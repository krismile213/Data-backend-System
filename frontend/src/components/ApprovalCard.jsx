import React from 'react';
import { Box, Paper, Stack, Button, Typography } from '@mui/material';
import { SEMANTIC } from '../theme.js';

/**
 * ApprovalCard —— 审批卡（收到 permission_request / plan_request / step_request 时渲染）
 *
 * 事件 payload 见方案 §4.2：
 *   permission_request {id, tool, preview, level}
 *   plan_request       {id, plan, level}
 *   step_request       {id, step, total, text}
 *
 * 按钮规则：
 *   允许 / 本会话总允许 / 拒绝
 *   —— 仅「写级工具审批」给「本会话总允许」；`external` 级（对外发送）**不给**，
 *      避免一次授权把对外发送全部放行（§8.3）。
 *
 * level 契约（已落地）：Agent 现已在上游事件 `permission_request` / `plan_request`
 *   与 BFF `/api/pending` 中**都下发 `level`**（如 'external' / 'write'）。
 *   因此这里**优先直读 `item.level`**：有 level 就只信 level（external 不给总允许，
 *   write 给）；**缺 level 时**才回退 `looksExternal()` 启发式，仅用于兼容旧版
 *   Agent（未下发 level），属**向后兼容兜底路径，不可删**。
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
  // level 优先（Agent ≥ 某版本起事件与 /api/pending 均带 level）；
  // 缺 level（旧 Agent）时回退启发式，向后兼容，不可删。
  const level = item.level;
  const allowAlways = kind === 'permission'
    && (level ? level !== 'external' : !looksExternal(tool));

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
