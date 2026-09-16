import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  InputAdornment,
  List,
  ListItemButton,
  ListItemText,
  Paper,
  TextField,
  Typography,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import DownloadIcon from '@mui/icons-material/Download';
import * as echarts from 'echarts';
import EChart from '../components/EChart.jsx';
import { fetchReviewOverview, fetchReviewSku, buildSkuReportUrl } from '../api.js';

/** 部门配色（对应 gantt_sheet.py DEPT_FILL） */
const DEPT_FILL = {
  产品: '#4472c4',
  __GAP__: '#a6a6a6',
  运营: '#70ad47',
  后勤: '#ed7d31',
  运输: '#7030a0',
  '4国': '#bf8f00',
};
const DEPT_LABEL = {
  产品: '产品部',
  __GAP__: '留空',
  运营: '运营部',
  后勤: '后勤部',
  运输: '运输',
  '4国': '4国单独',
};
const RED_FILL = '#ff3b30';     // 超时部分（对应 gantt_sheet RED_FILL）
const NEUTRAL_FILL = '#a6a6a6'; // 留空段
const JUDGE_COLOR = { '✓': '#1f7a1f', '✗': '#c00000', '⚠': '#ed6c02' };

const DAY_MS = 86400000;

function ts(s) {
  if (!s) return null;
  const t = s.includes('T') ? s : s.replace(' ', 'T');
  const v = new Date(t).getTime();
  return Number.isNaN(v) ? null : v;
}

function judgeColor(judge) {
  const j = String(judge || '');
  for (const [k, c] of Object.entries(JUDGE_COLOR)) {
    if (j.startsWith(k)) return c;
  }
  return '#808080';
}

/** 由 SKU 档案构建 ECharts 甘特图 option（模仿 gantt_sheet.py：部门分组/超时红色/条尾判定） */
function buildGanttOption(detail, publishAt) {
  const rows = detail.segments || [];
  if (rows.length === 0) return null;

  const cats = rows.map((r) => r.seg);

  // 部门分组：仅组内第一行显示部门名（两行式标签）
  const deptFlags = [];
  let prevDept = null;
  rows.forEach((r) => {
    deptFlags.push(r.dept !== prevDept);
    prevDept = r.dept;
  });

  // 每行一个条形：[ci, t0, t1, target, baseColor, judge, days]
  const data = [];
  rows.forEach((r, ci) => {
    const t0 = ts(r.t0) ?? ts(r.start);
    const t1 = ts(r.t1) ?? ts(r.end);
    if (t0 == null || t1 == null) return;
    const base = r.gap ? NEUTRAL_FILL : (DEPT_FILL[r.dept] || NEUTRAL_FILL);
    const days = (t1 - t0) / DAY_MS;
    data.push([ci, t0, t1, r.gap ? 0 : (r.target || 0), base,
      r.judge || '-', days]);
  });

  // 三部门终节点里程碑：放在同部门、终点时间匹配的行上
  const MS = [
    { key: '产品部', dept: '产品' },
    { key: '运营部', dept: '运营' },
    { key: '后勤部', dept: '运输' },
  ];
  const milestones = [];
  MS.forEach((m) => {
    const t = ts(detail.dept?.[m.key]?.time);
    if (t == null) return;
    let ci = rows.findIndex(
      (r) => r.dept === m.dept && !r.gap && Math.abs((ts(r.t1) ?? 0) - t) < DAY_MS);
    if (ci < 0) ci = rows.map((r) => r.dept).lastIndexOf(m.dept);
    if (ci < 0) ci = rows.map((r) => r.dept).lastIndexOf('4国');
    if (ci >= 0) milestones.push([t, ci, `${m.key}终节点`]);
  });

  const barH = 14;

  const series = [
    {
      type: 'custom',
      renderItem: (params, api) => {
        const ci = api.value(0);
        const t0 = api.value(1);
        const t1 = api.value(2);
        const target = api.value(3);
        const base = api.value(4);
        const judge = api.value(5);
        const days = api.value(6);
        const start = api.coord([t0, ci]);
        const end = api.coord([t1, ci]);
        const y = start[1] - barH / 2;
        const height = barH;
        const clip = {
          x: params.coordSys.x,
          y: params.coordSys.y,
          width: params.coordSys.width,
          height: params.coordSys.height,
        };

        const rectShape = (x, w, fill) => {
          const clipped = echarts.graphic.clipRectByRect(
            { x, y, width: Math.max(w, 2), height },
            clip);
          return clipped
            ? { type: 'rect', shape: clipped, style: { fill } }
            : null;
        };

        // 基础条（部门色/留空灰）
        const children = [rectShape(start[0], end[0] - start[0], base)];

        // 超时部分红色：起点 + target 天之后（对应 gantt_sheet 条形切红）
        const overStartT = t0 + target * DAY_MS;
        if (target > 0 && days > target) {
          const overX = api.coord([overStartT, ci])[0];
          children.push(rectShape(overX, end[0] - overX, RED_FILL));
        }

        // 条尾判定标签
        const jColor = judgeColor(judge);
        const endX = Math.min(end[0] + 6, params.coordSys.x + params.coordSys.width - 34);
        if (judge && judge !== '-') {
          children.push({
            type: 'text',
            style: {
              x: endX,
              y: start[1] + 4,
              text: judge,
              fill: jColor,
              font: 'bold 10px sans-serif',
            },
          });
        }

        return { type: 'group', children };
      },
      encode: { x: [1, 2], y: 0 },
      data,
      markLine: ts(publishAt) == null ? undefined : {
        symbol: 'none',
        lineStyle: { color: '#5e35b1', type: 'dashed', width: 1.5 },
        label: { formatter: '官方发布', fontSize: 10, color: '#5e35b1' },
        data: [{ xAxis: ts(publishAt) }],
      },
    },
  ];

  // 部门终节点里程碑（菱形）
  if (milestones.length > 0) {
    series.push({
      type: 'scatter',
      symbol: 'diamond',
      symbolSize: 13,
      itemStyle: { color: '#7b1fa2', borderColor: '#fff', borderWidth: 1 },
      data: milestones.map(([t, ci, name]) => ({ value: [t, ci], name })),
      label: {
        show: true,
        position: 'top',
        formatter: (p) => p.name,
        fontSize: 10,
        color: '#7b1fa2',
      },
      z: 5,
    });
  }

  return {
    tooltip: {
      formatter: (p) => {
        if (p.seriesType === 'scatter') {
          return `${p.name}<br/>${p.value[0] ? new Date(p.value[0]).toLocaleString('zh-CN') : ''}`;
        }
        const r = rows[p.dataIndex] || {};
        const lines = [
          `${DEPT_LABEL[r.dept] || r.dept || ''} · ${r.seg || ''}`,
          `${r.start || '—'} → ${r.end || '—'}`,
        ];
        if (!r.gap) {
          lines.push(`耗时 <b>${r.days ?? '—'}</b> 天 / 目标 ${r.target ?? '—'} 天`);
          lines.push(`判定 <b style="color:${judgeColor(r.judge)}">${r.judge || '—'}</b>`);
        }
        if (r.executor) lines.push(`执行人 ${r.executor}`);
        if (r.note) lines.push(`<span style="color:#888">${r.note}</span>`);
        return lines.join('<br/>');
      },
    },
    grid: { left: 150, right: 70, top: 24, bottom: 56 },
    xAxis: { type: 'time', axisLabel: { formatter: '{MM}-{dd}' } },
    yAxis: {
      type: 'category',
      data: cats,
      inverse: true,
      axisLabel: {
        fontSize: 11,
        formatter: (value) => {
          const idx = cats.indexOf(value);
          if (idx < 0) return value;
          // 组内第一行：部门名(彩色) + 环节名 两行式标签
          if (!deptFlags[idx]) return value;
          const dept = rows[idx].dept;
          return `{dept${dept === '__GAP__' ? '' : ''}|${DEPT_LABEL[dept] || dept || ''}}\n${value}`;
        },
        rich: {
          dept: {
            fontWeight: 'bold',
            fontSize: 11,
            color: '#333',
            lineHeight: 16,
          },
        },
      },
    },
    dataZoom: [
      { type: 'slider', xAxisIndex: 0, height: 20, bottom: 10 },
      { type: 'inside', xAxisIndex: 0 },
    ],
    series,
  };
}

export default function Gantt() {
  const [rows, setRows] = useState([]);
  const [kw, setKw] = useState('');
  const [sku, setSku] = useState('');
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    fetchReviewOverview()
      .then((d) => setRows(d.rows || []))
      .catch((e) => setError(e.message));
  }, []);

  const pick = (code) => {
    setSku(code);
    setDetail(null);
    setLoading(true);
    setError('');
    fetchReviewSku(code)
      .then(setDetail)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  const filtered = useMemo(() => {
    const k = kw.trim().toLowerCase();
    if (!k) return rows;
    return rows.filter(
      (r) => r.sku.toLowerCase().includes(k)
        || (r.model || '').toLowerCase().includes(k),
    );
  }, [rows, kw]);

  const option = useMemo(() => {
    if (!detail) return null;
    const row = rows.find((r) => r.sku === sku);
    return buildGanttOption(detail, row ? row.publishAt : null);
  }, [detail, sku, rows]);

  const segRows = (detail?.segments || []).filter((r) => ts(r.t0 || r.start) != null
    && ts(r.t1 || r.end) != null).length;

  return (
    <Box sx={{ display: 'flex', gap: 2, height: 'calc(100vh - 120px)' }}>
      {/* 左侧 SKU 检索 */}
      <Paper sx={{ width: 300, flexShrink: 0, display: 'flex', flexDirection: 'column' }}>
        <Box sx={{ p: 1.5 }}>
          <TextField
            size="small"
            fullWidth
            placeholder="搜索 SKU / 型号"
            value={kw}
            onChange={(e) => setKw(e.target.value)}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon fontSize="small" />
                </InputAdornment>
              ),
            }}
          />
        </Box>
        <List sx={{ overflow: 'auto', flexGrow: 1 }} dense>
          {filtered.map((r) => (
            <ListItemButton
              key={r.sku}
              selected={sku === r.sku}
              onClick={() => pick(r.sku)}
            >
              <ListItemText
                primary={`${r.sku}${r.model ? ` · ${r.model}` : ''}`}
                secondary={r.valid ? '有效复盘' : '未闭环'}
                primaryTypographyProps={{ fontSize: 13 }}
                secondaryTypographyProps={{
                  fontSize: 11,
                  color: r.valid ? 'success.main' : 'text.secondary',
                }}
              />
            </ListItemButton>
          ))}
        </List>
      </Paper>

      {/* 右侧甘特图 */}
      <Box sx={{ flexGrow: 1, minWidth: 0, overflow: 'auto' }}>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        {!sku && !error && (
          <Alert severity="info">左侧选择一个 SKU，查看其实际动作映射全流程甘特图</Alert>
        )}
        {loading && <CircularProgress sx={{ m: 4 }} />}
        {detail && option && (
          <Paper sx={{ p: 2 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1, flexWrap: 'wrap' }}>
              <Typography variant="subtitle1" fontWeight={600}>
                {sku} 全流程甘特图
              </Typography>
              <Chip size="small" label={detail.devType} />
              <Chip
                size="small"
                label={detail.valid ? '有效复盘' : '未闭环'}
                color={detail.valid ? 'success' : 'warning'}
              />
              {detail.bottleneck && (
                <Chip
                  size="small"
                  color="error"
                  label={`头号卡点 ${detail.bottleneck.dept}·${detail.bottleneck.seg} ${detail.bottleneck.days}天`}
                />
              )}
              <Box sx={{ flexGrow: 1 }} />
              <Button
                variant="outlined"
                size="small"
                startIcon={<DownloadIcon />}
                href={buildSkuReportUrl(sku)}
              >
                下载复盘报告 .md
              </Button>
            </Box>
            <Box sx={{ mb: 1, display: 'flex', gap: 2, flexWrap: 'wrap' }}>
              {[
                ['产品部', DEPT_FILL.产品],
                ['运营部', DEPT_FILL.运营],
                ['后勤部', DEPT_FILL.后勤],
                ['运输', DEPT_FILL.运输],
                ['4国单独', DEPT_FILL['4国']],
                ['留空段', NEUTRAL_FILL],
                ['超时部分', RED_FILL],
                ['部门终节点', '#7b1fa2'],
              ].map(([label, color]) => (
                <Box key={label} sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  <Box sx={{ width: 14, height: 10, bgcolor: color, borderRadius: 0.5 }} />
                  <Typography variant="caption" color="text.secondary">{label}</Typography>
                </Box>
              ))}
            </Box>
            <EChart option={option} height={Math.max(360, segRows * 46 + 120)} />
            <Typography variant="caption" color="text.secondary">
              每行一个环节段（起点动作→终点动作），条形超出目标的部分为红色，条尾跟判定；
              灰条为产品部留空·后勤接管段；橙色「XX渠道·未发货」行 = Part5 已立项渠道但至今无货件号
              （采购下单→今，超目标即红色 ✗超时未发货，独立于采购→发货末批判定）；
              紫色菱形为三部门终节点完成时刻；紫色虚线为官方发布时间（提前开发判定依据）；
              底部滑块可缩放时间轴。
            </Typography>
          </Paper>
        )}
      </Box>
    </Box>
  );
}
