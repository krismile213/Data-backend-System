import React, { useEffect, useMemo, useState } from 'react';
import {
  Grid,
  Card,
  CardContent,
  Typography,
  Alert,
  CircularProgress,
  Box,
} from '@mui/material';
import Inventory2Icon from '@mui/icons-material/Inventory2';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ScheduleIcon from '@mui/icons-material/Schedule';
import BugReportIcon from '@mui/icons-material/BugReport';
import { fetchOverview, fetchTimeliness } from '../api.js';
import EChart from '../components/EChart.jsx';

/** 小时数格式化 */
function fmtHours(h) {
  if (h === null || h === undefined) return '—';
  if (h >= 24) return `${(h / 24).toFixed(1)} 天`;
  return `${h.toFixed(1)} 小时`;
}

const KPI_CARDS = [
  { key: 'total', label: '总单据数', icon: <Inventory2Icon color="primary" /> },
  { key: 'finished', label: '已完成单据', icon: <CheckCircleIcon sx={{ color: '#2e7d32' }} /> },
  { key: 'running', label: '审批中（进行中）', icon: <ScheduleIcon sx={{ color: '#ef6c00' }} /> },
  { key: 'anomalyDocCount', label: '异常单据数', icon: <BugReportIcon sx={{ color: '#c62828' }} /> },
];

/** 状态饼图配色（中国市场习惯：完成/正向偏红，进行中橙，终止灰） */
const STATUS_COLORS = {
  已结束: '#c62828',
  审批中: '#ef6c00',
  终止: '#9e9e9e',
};
const PIE_PALETTE = ['#c62828', '#ef6c00', '#9e9e9e', '#1e5eff', '#2e7d32', '#6a1b9a'];

export default function Dashboard({ batch }) {
  const [overview, setOverview] = useState(null);
  const [timeliness, setTimeliness] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!batch) return;
    setLoading(true);
    setError('');
    Promise.all([fetchOverview(batch), fetchTimeliness(batch)])
      .then(([ov, tl]) => {
        setOverview(ov);
        setTimeliness(tl);
      })
      .catch((e) => setError(e.message || '加载失败'))
      .finally(() => setLoading(false));
  }, [batch]);

  // ---- 图表配置 ----
  const partBarOption = useMemo(() => {
    if (!timeliness) return null;
    const names = timeliness.partBar.map((p) => `${p.part} ${p.name}`);
    return {
      title: { text: '各 Part 全流程耗时对比（已结束单据）', left: 'center', textStyle: { fontSize: 15 } },
      tooltip: { trigger: 'axis', valueFormatter: (v) => (v == null ? '—' : `${v} 小时`) },
      legend: { top: 28 },
      grid: { top: 70, left: 60, right: 30, bottom: 80 },
      xAxis: { type: 'category', data: names, axisLabel: { rotate: 30, fontSize: 11 } },
      yAxis: { type: 'value', name: '小时' },
      series: [
        {
          name: '平均耗时',
          type: 'bar',
          data: timeliness.partBar.map((p) => p.avg),
          itemStyle: { color: '#c62828' },
        },
        {
          name: '中位数',
          type: 'bar',
          data: timeliness.partBar.map((p) => p.median),
          itemStyle: { color: '#ef6c00' },
        },
        {
          name: 'P90',
          type: 'bar',
          data: timeliness.partBar.map((p) => p.p90),
          itemStyle: { color: '#1e5eff' },
        },
      ],
    };
  }, [timeliness]);

  const pieOption = useMemo(() => {
    if (!overview) return null;
    return {
      title: { text: '单据审批状态分布（全部 Part 合计）', left: 'center', textStyle: { fontSize: 15 } },
      tooltip: { trigger: 'item', formatter: '{b}: {c} 张（{d}%）' },
      legend: { bottom: 0 },
      series: [
        {
          name: '审批状态',
          type: 'pie',
          radius: ['38%', '62%'],
          center: ['50%', '55%'],
          label: { formatter: '{b}\n{c} 张' },
          data: overview.statusPie.map((s, i) => ({
            name: s.name,
            value: s.value,
            itemStyle: {
              color: STATUS_COLORS[s.name] || PIE_PALETTE[i % PIE_PALETTE.length],
            },
          })),
        },
      ],
    };
  }, [overview]);

  const heatOption = useMemo(() => {
    if (!timeliness) return null;
    const seg = timeliness.segmentHeat;
    if (!seg.length) return null;
    // 热力图：x=分段动作（去重），y=Part
    const xCats = [...new Set(seg.map((s) => s.segment))];
    const yCats = seg.map((s) => `${s.part} ${s.partName}`);
    const yUnique = [...new Set(yCats)];
    const data = [];
    let maxVal = 0;
    seg.forEach((s) => {
      const x = xCats.indexOf(s.segment);
      const y = yUnique.indexOf(`${s.part} ${s.partName}`);
      if (s.avg !== null && s.avg !== undefined) {
        data.push([x, y, Math.round(s.avg * 100) / 100]);
        if (s.avg > maxVal) maxVal = s.avg;
      }
    });
    return {
      title: { text: '各 Part 分段动作平均耗时热力图（小时）', left: 'center', textStyle: { fontSize: 15 } },
      tooltip: {
        position: 'top',
        formatter: (p) => `${yUnique[p.value[1]]}<br/>${xCats[p.value[0]]}<br/>平均 ${p.value[2]} 小时`,
      },
      grid: { top: 50, left: 200, right: 100, bottom: 90 },
      xAxis: { type: 'category', data: xCats, axisLabel: { rotate: 40, fontSize: 10 } },
      yAxis: { type: 'category', data: yUnique, axisLabel: { fontSize: 11 } },
      visualMap: {
        min: 0,
        max: maxVal || 1,
        calculable: true,
        orient: 'horizontal',
        left: 'center',
        bottom: 0,
        inRange: { color: ['#e3f2fd', '#ffcc80', '#c62828'] },
      },
      series: [
        {
          type: 'heatmap',
          data,
          label: { show: true, fontSize: 10 },
          emphasis: { itemStyle: { shadowBlur: 6, shadowColor: 'rgba(0,0,0,0.4)' } },
        },
      ],
    };
  }, [timeliness]);

  const trendOption = useMemo(() => {
    if (!timeliness) return null;
    return {
      title: { text: '单据创建时间趋势（按日，全部 Part 合计）', left: 'center', textStyle: { fontSize: 15 } },
      tooltip: { trigger: 'axis' },
      grid: { top: 50, left: 60, right: 30, bottom: 60 },
      xAxis: {
        type: 'category',
        data: timeliness.trend.map((t) => t.date),
        axisLabel: { rotate: 30, fontSize: 11 },
      },
      yAxis: { type: 'value', name: '创建单据数', minInterval: 1 },
      dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 5, height: 18 }],
      series: [
        {
          name: '创建单据数',
          type: 'line',
          smooth: true,
          data: timeliness.trend.map((t) => t.count),
          itemStyle: { color: '#c62828' },
          areaStyle: { color: 'rgba(198,40,40,0.12)' },
        },
      ],
    };
  }, [timeliness]);

  if (!batch) {
    return <Alert severity="info">请先在右上角选择数据批次</Alert>;
  }
  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', mt: 8 }}>
        <CircularProgress />
      </Box>
    );
  }
  if (error) {
    return <Alert severity="error">加载失败：{error}</Alert>;
  }
  if (!overview || !timeliness) return null;

  const kpi = overview.kpi;

  return (
    <div className="space-y-4">
      {/* 数据基准时间 */}
      <Typography variant="body2" color="text.secondary">
        批次：{overview.batch} ｜ 数据基准时间（模拟当前）：{overview.baseTime || '—'}
      </Typography>

      {/* KPI 卡片 */}
      <Grid container spacing={2}>
        {KPI_CARDS.map((c) => (
          <Grid item xs={12} sm={6} md={3} key={c.key}>
            <Card sx={{ borderTop: '3px solid #1e5eff' }}>
              <CardContent className="flex items-center justify-between">
                <Box>
                  <Typography variant="body2" color="text.secondary">
                    {c.label}
                  </Typography>
                  <Typography variant="h4" fontWeight={700}>
                    {c.key === 'finished' ? `${kpi.finished} / ${kpi.total}` : kpi[c.key]}
                  </Typography>
                  {c.key === 'total' && (
                    <Typography variant="caption" color="text.secondary">
                      完成率 {(kpi.finishRate * 100).toFixed(1)}%
                    </Typography>
                  )}
                  {c.key === 'anomalyDocCount' && (
                    <Typography variant="caption" color="text.secondary">
                      共 {kpi.anomalyCount} 条异常记录
                    </Typography>
                  )}
                </Box>
                {c.icon}
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>

      {/* 平均全流程耗时（跨所有已结束单据）卡片行 */}
      <Grid container spacing={2}>
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent className="flex items-center justify-between">
              <Box>
                <Typography variant="body2" color="text.secondary">
                  平均全流程耗时（已结束单据，跨 Part 合计口径见各环节）
                </Typography>
                <Typography variant="h5" fontWeight={700} sx={{ color: '#c62828' }}>
                  {fmtHours(kpi.avgTotalHours)}
                </Typography>
              </Box>
              <ScheduleIcon sx={{ fontSize: 40, color: '#c62828' }} />
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="body2" color="text.secondary" gutterBottom>
                终止单据
              </Typography>
              <Typography variant="h5" fontWeight={700} sx={{ color: '#616161' }}>
                {kpi.terminated} 张
              </Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* 图表区 */}
      <Grid container spacing={2}>
        <Grid item xs={12} lg={7}>
          <Card>
            <CardContent>
              {partBarOption ? <EChart option={partBarOption} height={400} /> : <Alert severity="info">暂无时效数据</Alert>}
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} lg={5}>
          <Card>
            <CardContent>
              {pieOption ? <EChart option={pieOption} height={400} /> : <Alert severity="info">暂无状态数据</Alert>}
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12}>
          <Card>
            <CardContent>
              {heatOption ? <EChart option={heatOption} height={420} /> : <Alert severity="info">暂无分段耗时数据</Alert>}
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12}>
          <Card>
            <CardContent>
              {trendOption ? <EChart option={trendOption} height={360} /> : <Alert severity="info">暂无创建趋势数据</Alert>}
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </div>
  );
}
