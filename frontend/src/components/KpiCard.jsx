import React from 'react';
import { Paper, Box, Typography } from '@mui/material';
import { SEMANTIC, CARD_SX } from '../theme.js';

/**
 * KpiCard —— 全站统一 KPI 数字卡
 *
 * 改造前：同一份实现（连默认色值 #1a2740 都相同）在 7 个页面里各复制了一遍
 *        （Alerts / DataAdmin / Review / Routes / Scenarios / Transport / Watch）。
 * 改造后：只此一份，页面 import 即可。
 *
 * Props:
 *   label   指标名（小字）
 *   value   数值（大字，可传字符串如 "3809 / 3904"）
 *   sub     补充说明（极小的字，可省略）
 *   color   数值颜色，取 SEMANTIC.*（默认正文色）
 *   icon    右侧图标节点（看板卡片用）
 *   accent  顶部强调条颜色（不传则不显示）
 */
export default function KpiCard({
  label,
  value,
  sub,
  color = SEMANTIC.text,
  icon,
  accent,
  sx,
}) {
  return (
    <Paper
      sx={{
        ...CARD_SX,
        p: 2,
        height: '100%',
        ...(accent ? { borderTop: `3px solid ${accent}` } : null),
        ...sx,
      }}
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 1,
        }}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="caption" color="text.secondary">
            {label}
          </Typography>
          <Typography variant="h5" sx={{ fontWeight: 700, color, mt: 0.5 }}>
            {value}
          </Typography>
          {sub && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: 'block' }}
            >
              {sub}
            </Typography>
          )}
        </Box>
        {icon}
      </Box>
    </Paper>
  );
}
