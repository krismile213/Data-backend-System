/**
 * theme.js —— 全站视觉唯一来源
 *
 * 改造前：153 处硬编码色值散落在 17 个页面里（#c62828 出现 34 次、#2e7d32 出现 20 次…），
 *        想调整一处颜色要改十几个文件；也导致深色模式无法实现（色值是写死的）。
 * 改造后：所有颜色先在这里定义语义名，页面只引用语义名。
 *        ECharts 的配置项取不到 MUI theme，所以额外导出纯 JS 常量 SEMANTIC。
 *
 * 语义映射（沿用系统既有口径，非美式红绿）：
 *   danger  #c62828 —— 已结束 / 超时 / 未达标 / 需关注（加急预警 P0）
 *   warning #ef6c00 —— 审批中 / 临近超时 / 待处理（P1）
 *   caution #f9a825 —— 轻微偏离 / 提示（P2）
 *   success #2e7d32 —— 达标 / 已闭环 / 去化路径
 *   info    #0277bd —— 说明性信息 / 场景 C 上游停滞
 *   neutral #616161 —— 终止 / 无数据（不计入考核）
 */

import { createTheme } from '@mui/material';

/** 语义色板 —— 页面与图表统一从这里取色 */
export const SEMANTIC = {
  brand: '#1e5eff',
  danger: '#c62828',
  dangerDeep: '#b71c1c',
  /** 深橙红：头号卡点等需要比 danger 更重的强调 */
  deepOrange: '#d84315',
  warning: '#ef6c00',
  /** 浅橙底上的深橙文字（对比度需要，不能直接用 warning） */
  warningDeep: '#b26a00',
  caution: '#f9a825',
  success: '#2e7d32',
  info: '#0277bd',
  /** 分类色：空运 / 海运等运输方式 */
  sky: '#0288d1',
  /** 蓝灰：优先级 P2「推进中」等中性偏冷的分级色 */
  slate: '#546e7a',
  neutral: '#616161',
  muted: '#9e9e9e',
  purple: '#6a1b9a',

  /** 正文主色（深蓝墨） */
  text: '#1a2740',
  /** 深色底 / 彩色底上的文字色 */
  onColor: '#fff',

  /** 页面底色 / 卡片底色 */
  surface: '#f4f6f8',
  border: '#e0e0e0',

  /** 代码/文案块底色 */
  codeBg: '#f8fafc',

  /** 浅底（用于 Chip、提示条底色） */
  dangerBg: '#fdecea',
  warningBg: '#fff8e1',
  successBg: '#e8f5e9',
  infoBg: '#e3f2fd',
  neutralBg: '#f5f5f5',
};

/** 单据审批状态 → 固定色（Dashboard / Compare 共用，此前两处各写一份） */
export const STATUS_COLORS = {
  已结束: SEMANTIC.danger,
  审批中: SEMANTIC.warning,
  终止: SEMANTIC.muted,
};

/** 饼图 / 多序列图通用色环 */
export const PIE_PALETTE = [
  SEMANTIC.danger,
  SEMANTIC.warning,
  SEMANTIC.muted,
  SEMANTIC.brand,
  SEMANTIC.success,
  SEMANTIC.purple,
];

/** 分段耗时热力图：低 → 高 */
export const HEAT_RANGE = ['#e3f2fd', '#ffcc80', SEMANTIC.danger];

/** 折线面积色（半透明） */
export const AREA_FILL = 'rgba(198,40,40,0.12)';

/**
 * 停滞天数 → MUI Chip 语义级别
 * 口径与场景诊断一致：≥60 红、≥30 橙、其余中性
 */
export function severityOfDays(d) {
  if (d === null || d === undefined || d === '') return 'default';
  if (d >= 60) return 'error';
  if (d >= 30) return 'warning';
  return 'default';
}

/** 停滞天数 → 十六进制色（图表内使用） */
export function colorOfDays(d) {
  if (d === null || d === undefined || d === '') return SEMANTIC.muted;
  if (d >= 60) return SEMANTIC.danger;
  if (d >= 30) return SEMANTIC.warning;
  return SEMANTIC.neutral;
}

const theme = createTheme({
  palette: {
    mode: 'light',
    primary: { main: SEMANTIC.brand },
    secondary: { main: SEMANTIC.danger },
    success: { main: SEMANTIC.success },
    warning: { main: SEMANTIC.warning },
    error: { main: SEMANTIC.danger },
    info: { main: SEMANTIC.info },
    background: { default: SEMANTIC.surface },
    text: { primary: SEMANTIC.text },
  },
  shape: { borderRadius: 8 },
  typography: {
    fontFamily:
      "'Microsoft YaHei', 'PingFang SC', 'Helvetica Neue', Arial, sans-serif",
  },
});

/**
 * 卡片外观（边框 + 内边距）—— 刻意不放进 theme.components，
 * 因为 MuiPaper 同时是 AppBar / Drawer 的底座，全局覆盖会连带改掉导航的阴影。
 * 需要统一观感的地方直接 spread 这个对象。
 */
export const CARD_SX = {
  border: `1px solid ${SEMANTIC.border}`,
  borderRadius: 2,
  bgcolor: '#fff',
};

/** 页面级标题样式（此前 8 处手写 h6 + fontWeight 700） */
export const PAGE_TITLE_SX = { fontWeight: 700 };

export default theme;
