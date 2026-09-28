import React from 'react';
import { Box, Typography } from '@mui/material';
import { SEMANTIC } from '../theme.js';

/**
 * PageHeader —— 页面标题栏
 *
 * 改造前：8 个页面各写一遍「h6 + fontWeight 700 + caption 副标题 + 右侧按钮」的
 *         flex 布局，间距与对齐细节还不完全一致。
 *
 * Props:
 *   title     页面主标题
 *   subtitle  副标题（灰色小字）
 *   actions   右侧操作区（按钮 / 下拉等），不传则标题占满整行
 *   dense     紧凑模式（用于卡片内的小标题栏）
 */
export default function PageHeader({ title, subtitle, actions, dense = false, sx }) {
  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: dense ? 'baseline' : 'center',
        justifyContent: actions ? 'space-between' : 'flex-start',
        flexWrap: 'wrap',
        gap: 1,
        mb: dense ? 1 : 2,
        ...sx,
      }}
    >
      <Box sx={{ minWidth: 0 }}>
        <Typography
          variant={dense ? 'subtitle1' : 'h6'}
          sx={{ fontWeight: 700, color: SEMANTIC.text }}
        >
          {title}
        </Typography>
        {subtitle && (
          <Typography variant="caption" color="text.secondary">
            {subtitle}
          </Typography>
        )}
      </Box>
      {actions && <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>{actions}</Box>}
    </Box>
  );
}
