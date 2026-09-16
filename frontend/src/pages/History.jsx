import React, { useCallback, useEffect, useState } from 'react';
import {
  Box,
  Paper,
  Typography,
  Grid,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  CircularProgress,
  Alert,
  Button,
  ToggleButtonGroup,
  ToggleButton,
  Tooltip,
} from '@mui/material';
import SnapshotIcon from '@mui/icons-material/PhotoCamera';
import DownloadIcon from '@mui/icons-material/Download';
import EChart from '../components/EChart.jsx';
import { fetchHistory, triggerSnapshot, buildHistoryReportUrl } from '../api.js';

const KPI_ROWS = [
  ['skuCount', '复盘 SKU 数'],
  ['openSkus', '未闭环 SKU'],
  ['onTimeRate', '总体达标率(%)'],
  ['transportOverRate', '货件超时率(%)'],
  ['p0', 'P0 预警'],
  ['p1', 'P1 预警'],
  ['p2', 'P2 预警'],
  ['scenA', '场景 A(素材未完成)'],
  ['scenB', '场景 B(待最终上架)'],
  ['scenC', '场景 C(发货未完成)'],
];

export default function History() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [days, setDays] = useState(7);

  const load = useCallback(() => {
    fetchHistory().then(setData).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { load(); }, [load]);

  const doSnapshot = async () => {
    setBusy(true);
    try {
      await triggerSnapshot(false);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const trendOption = (() => {
    const snaps = data?.snapshots || [];
    if (snaps.length === 0) return null;
    const x = snaps.map((s) => s.at);
    const mk = (key, name, color, yAxisIndex) => ({
      name, type: 'line', smooth: true, yAxisIndex,
      data: snaps.map((s) => s.kpi?.[key] ?? null),
      itemStyle: { color }, lineStyle: { width: 2 },
      connectNulls: true,
    });
    return {
      tooltip: { trigger: 'axis' },
      legend: { data: ['总体达标率(%)', '货件超时率(%)', '未闭环 SKU'] },
      grid: { left: 50, right: 50, top: 40, bottom: 30 },
      xAxis: { type: 'category', data: x },
      yAxis: [
        { type: 'value', name: '%' },
        { type: 'value', name: '个', splitLine: { show: false } },
      ],
      series: [
        mk('onTimeRate', '总体达标率(%)', '#2e7d32', 0),
        mk('transportOverRate', '货件超时率(%)', '#c62828', 0),
        mk('openSkus', '未闭环 SKU', '#0277bd', 1),
      ],
    };
  })();

  if (error && !data) return <Alert severity="error">趋势周报加载失败：{error}</Alert>;
  if (!data) return <Box sx={{ display: 'flex', justifyContent: 'center', mt: 8 }}><CircularProgress /></Box>;

  const snaps = data.snapshots || [];
  const first = snaps[0]?.kpi || {};
  const last = snaps[snaps.length - 1]?.kpi || {};

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2, flexWrap: 'wrap', gap: 1 }}>
        <Box>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>
            KPI 快照与自动周报
          </Typography>
          <Typography variant="caption" color="text.secondary">
            数据指纹变化后的首次访问自动记录快照；快照积累后一键生成周报 —— 当前 {data.count} 张
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
          <ToggleButtonGroup size="small" value={days} exclusive onChange={(e, v) => v && setDays(v)}>
            <ToggleButton value={7}>周报 7 天</ToggleButton>
            <ToggleButton value={30}>月报 30 天</ToggleButton>
          </ToggleButtonGroup>
          <Tooltip title="强制补拍一张当前快照（供周会前打点）">
            <span>
              <Button variant="outlined" startIcon={<SnapshotIcon />} disabled={busy} onClick={doSnapshot}>
                立即快照
              </Button>
            </span>
          </Tooltip>
          <Button variant="contained" startIcon={<DownloadIcon />} href={buildHistoryReportUrl(days)}>
            生成{days === 7 ? '周' : '月'}报 md
          </Button>
        </Box>
      </Box>

      {error && <Alert severity="warning" sx={{ mb: 2 }} onClose={() => setError('')}>{error}</Alert>}

      {snaps.length === 0 ? (
        <Alert severity="info">还没有快照，点击「立即快照」记录第一张。</Alert>
      ) : (
        <Grid container spacing={2}>
          <Grid item xs={12}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="subtitle2" gutterBottom>核心指标走势（快照时间轴）</Typography>
              <EChart option={trendOption} height={300} />
            </Paper>
          </Grid>
          <Grid item xs={12}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="subtitle2" gutterBottom>
                期初（{snaps[0].at}）→ 最新（{snaps[snaps.length - 1].at}）对比
              </Typography>
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>指标</TableCell>
                      <TableCell>期初</TableCell>
                      <TableCell>最新</TableCell>
                      <TableCell>变化</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {KPI_ROWS.map(([key, label]) => {
                      const a = first[key], b = last[key];
                      const d = (a !== null && a !== undefined && b !== null && b !== undefined)
                        ? Math.round((b - a) * 10) / 10 : null;
                      const goodDown = ['openSkus', 'transportOverRate', 'p0', 'p1', 'p2', 'scenA', 'scenB', 'scenC'].includes(key);
                      return (
                        <TableRow key={key} hover>
                          <TableCell>{label}</TableCell>
                          <TableCell>{a ?? '—'}</TableCell>
                          <TableCell>{b ?? '—'}</TableCell>
                          <TableCell>
                            {d === null ? '—' : d === 0 ? '持平' : (
                              <Typography variant="body2" color={d === 0 ? 'text.primary' : (d < 0) === goodDown ? 'success.main' : 'warning.main'}>
                                {d > 0 ? '+' : ''}{d}
                              </Typography>
                            )}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </TableContainer>
            </Paper>
          </Grid>
          <Grid item xs={12}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="subtitle2" gutterBottom>快照明细</Typography>
              <TableContainer sx={{ maxHeight: 360 }}>
                <Table size="small" stickyHeader>
                  <TableHead>
                    <TableRow>
                      <TableCell>时间</TableCell>
                      <TableCell>SKU 数</TableCell>
                      <TableCell>未闭环</TableCell>
                      <TableCell>达标率</TableCell>
                      <TableCell>货件超时率</TableCell>
                      <TableCell>P0/P1/P2</TableCell>
                      <TableCell>场景 A/B/C</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {[...snaps].reverse().map((s) => (
                      <TableRow key={s.at + s.fingerprint} hover>
                        <TableCell>{s.at}</TableCell>
                        <TableCell>{s.kpi?.skuCount ?? '—'}</TableCell>
                        <TableCell>{s.kpi?.openSkus ?? '—'}</TableCell>
                        <TableCell>{s.kpi?.onTimeRate ?? '—'}%</TableCell>
                        <TableCell>{s.kpi?.transportOverRate ?? '—'}%</TableCell>
                        <TableCell>{s.kpi?.p0}/{s.kpi?.p1}/{s.kpi?.p2}</TableCell>
                        <TableCell>{s.kpi?.scenA}/{s.kpi?.scenB}/{s.kpi?.scenC}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </Paper>
          </Grid>
        </Grid>
      )}
    </Box>
  );
}
