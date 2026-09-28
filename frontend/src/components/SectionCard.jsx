import React from 'react';
import { Paper, Box, Divider } from '@mui/material';
import { CARD_SX } from '../theme.js';

/**
 * SectionCard —— 统一卡片容器（Paper + 可选标题栏）
 *
 * 改造前：`<Paper sx={{ p: 2 }}>` 出现 31 次、`<Card>` 28 次，内边距与标题写法各不相同。
 * 改造后：需要「带标题的卡片」时用这个；只需要一个白底盒子时用 CARD_SX。
 *
 * Props:
 *   title     卡片标题（可省略）
 *   subtitle  标题下方小字
 *   actions   标题右侧操作区
 *   divider   标题与内容之间是否加分割线
 *   bodySx    内容区内边距覆盖
 */
export default function SectionCard({
  title,
  subtitle,
  actions,
  divider = false,
  children,
  sx,
  bodySx,
}) {
  const hasHeader = Boolean(title || subtitle || actions);
  return (
    <Paper sx={{ ...CARD_SX, p: 2, ...sx }}>
      {hasHeader && (
        <>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: actions ? 'space-between' : 'flex-start',
              flexWrap: 'wrap',
              gap: 1,
            }}
          >
            <Box sx={{ minWidth: 0 }}>
              {title && (
                <Box component="div" sx={{ fontWeight: 700, fontSize: 16, lineHeight: 1.5 }}>
                  {title}
                </Box>
              )}
              {subtitle && (
                <Box component="div" sx={{ fontSize: 12, color: 'text.secondary' }}>
                  {subtitle}
                </Box>
              )}
            </Box>
            {actions && <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>{actions}</Box>}
          </Box>
          {divider && <Divider sx={{ my: 1.5 }} />}
        </>
      )}
      <Box sx={bodySx}>{children}</Box>
    </Paper>
  );
}
