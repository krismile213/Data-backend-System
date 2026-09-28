import React from 'react';
import { Box, Alert, CircularProgress, Typography } from '@mui/material';

/**
 * StateViews —— 加载 / 错误 / 空态三种视图
 *
 * 改造前：`<Box sx={{ display:'flex', justifyContent:'center', mt:8 }}><CircularProgress /></Box>`
 *         这行代码在 17 个页面里抄了 41 次；`Alert severity="error"` 各写各的文案前缀 42 处。
 * 改造后：三种状态各一个组件，文案与间距统一。
 *
 * 用法：
 *   if (loading) return <LoadingState />;
 *   if (error)   return <ErrorState prefix="场景诊断加载失败" error={error} />;
 *   if (!data)   return <EmptyState>暂无数据</EmptyState>;
 */

/** 加载中：居中转圈，可带文案；size 用于表格内联小转圈 */
export function LoadingState({ label, size, height, sx }) {
  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 1.5,
        mt: height ? 0 : 8,
        minHeight: height,
        ...sx,
      }}
    >
      <CircularProgress size={size} />
      {label && (
        <Typography variant="caption" color="text.secondary">
          {label}
        </Typography>
      )}
    </Box>
  );
}

/**
 * 错误态：红色提示条
 * prefix 为文案前缀，默认「加载失败」，最终渲染为 `{prefix}：{error}`
 * error 可为字符串或 Error
 */
export function ErrorState({ prefix = '加载失败', error, sx, children }) {
  const text = error && ((error.message ?? error).toString().trim());
  return (
    <Alert severity="error" sx={sx}>
      {children || (text ? `${prefix}：${text}` : prefix)}
    </Alert>
  );
}

/** 空态 / 提示态：默认 info 蓝色条，severity 可改 'info' | 'warning' | 'success' */
export function EmptyState({ severity = 'info', children, sx }) {
  return (
    <Alert severity={severity} sx={sx}>
      {children}
    </Alert>
  );
}
