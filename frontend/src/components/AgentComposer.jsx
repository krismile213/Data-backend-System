import React, { useState } from 'react';
import { Box, Button, Stack, TextField } from '@mui/material';
import SendIcon from '@mui/icons-material/Send';
import StopCircleIcon from '@mui/icons-material/StopCircle';

/**
 * AgentComposer —— 输入框 + 发送 + 停止
 *
 * 交互：
 *   Enter 发送、Shift+Enter 换行；
 *   任务运行中「发送」= 追加指令（走 /api/enqueue，不打断执行）；
 *   运行中额外显示「停止」按钮。
 */
export default function AgentComposer({ onSend, onStop, running, disabled }) {
  const [text, setText] = useState('');

  const doSend = () => {
    const t = text.trim();
    if (!t) return;
    onSend(t);
    setText('');
  };

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      doSend();
    }
  };

  return (
    <Box sx={{ p: 1.5 }}>
      <TextField
        multiline
        minRows={2}
        maxRows={6}
        fullWidth
        size="small"
        placeholder={running ? '追加指令…（Enter 发送，Shift+Enter 换行）' : '问点什么…（Enter 发送，Shift+Enter 换行）'}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
      />
      <Stack direction="row" spacing={1} sx={{ mt: 1, justifyContent: 'flex-end' }}>
        {running && (
          <Button size="small" variant="outlined" color="error"
                  startIcon={<StopCircleIcon />} onClick={onStop}>
            停止
          </Button>
        )}
        <Button size="small" variant="contained" endIcon={<SendIcon />}
                onClick={doSend} disabled={disabled || !text.trim()}>
          {running ? '追加' : '发送'}
        </Button>
      </Stack>
    </Box>
  );
}
