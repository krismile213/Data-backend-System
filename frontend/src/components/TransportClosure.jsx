import React from 'react';
import { Box, Chip, Tooltip } from '@mui/material';
import { SEMANTIC } from '../theme.js';

/**
 * TransportClosure —— 运输闭环展示（事中日报 / 加急预警**两页共用**，不复制两份）
 *
 * 口径真源 = 后端 `app/transport_closure.classify_transport`：
 *   · closure='closed'  已发货货件全部到货（运输已闭环）
 *   · closure='pending' 有已发货票未到货（运输未闭环，列出未到货国家）
 *   · closure='none'    无任何票已发货（运输未形成卡点）
 *
 * 颜色走 theme.js 语义色板（禁硬编码）：
 *   closed→success / pending→warning / none→neutral
 */
const CLOSURE_META = {
  closed: { label: '运输已闭环', color: SEMANTIC.success, tip: '已发货货件全部到货' },
  pending: { label: '运输未闭环', color: SEMANTIC.warning, tip: '有已发货货件未到货' },
  none: { label: '无可发货货件', color: SEMANTIC.neutral, tip: '无任何票已发货' },
};

export function ClosureBadge({ closure, size = 'small' }) {
  const meta = CLOSURE_META[closure] || {
    label: closure || '—', color: SEMANTIC.muted, tip: '',
  };
  return (
    <Tooltip title={meta.tip || ''}>
      <Chip
        size={size}
        label={meta.label}
        sx={{ bgcolor: meta.color, color: SEMANTIC.onColor, fontWeight: 600 }}
      />
    </Tooltip>
  );
}

/** 未到货国家胶囊（pendingHubs）。空 → 不渲染。 */
export function PendingCountries({ hubs, size = 'small', prefix = '未到货' }) {
  const list = hubs || [];
  if (!list.length) return null;
  return (
    <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5, flexWrap: 'wrap' }}>
      {prefix ? (
        <Box component="span" sx={{ fontSize: 12, color: SEMANTIC.warning, mr: 0.5 }}>
          {prefix}：
        </Box>
      ) : null}
      {list.map((h) => (
        <Chip
          key={h}
          size={size}
          variant="outlined"
          label={h}
          sx={{ color: SEMANTIC.warning, borderColor: SEMANTIC.warning }}
        />
      ))}
    </Box>
  );
}

/** 组合展示：徽标 + 未到货国家胶囊（列表/详情一行搞定）。 */
export default function TransportClosure({ closure, pendingCountries, size = 'small' }) {
  if (!closure) return <span>—</span>;
  return (
    <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5, flexWrap: 'wrap' }}>
      <ClosureBadge closure={closure} size={size} />
      <PendingCountries hubs={pendingCountries} size={size} />
    </Box>
  );
}
