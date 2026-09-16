import React, { useEffect, useMemo, useState } from 'react';
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
  TablePagination,
  TableRow,
  TextField,
  MenuItem,
  Chip,
  CircularProgress,
  Alert,
  Button,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import EChart from '../components/EChart.jsx';
import { fetchTransport, buildTransportExportUrl } from '../api.js';

const JUDGE_COLOR = {
  超时: { bg: '#fcebe9', color: '#c62828' },
  未到货: { bg: '#fff8e1', color: '#b26a00' },
  未发货: { bg: '#f4f5f7', color: '#616161' },
  达标: { bg: '#e8f5e9', color: '#2e7d32' },
};

function KpiCard({ label, value, sub, color = '#1a2740' }) {
  return (
    <Paper sx={{ p: 2, height: '100%' }}>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography variant="h5" sx={{ fontWeight: 700, color, mt: 0.5 }}>{value}</Typography>
      {sub && <Typography variant="caption" color="text.secondary">{sub}</Typography>}
    </Paper>
  );
}

export default function Transport() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');
  const [judgeF, setJudgeF] = useState('');
  const [clsF, setClsF] = useState('');
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(50);

  useEffect(() => {
    fetchTransport().then(setData).catch((e) => setError(e.message));
  }, []);

  const rows = useMemo(() => {
    if (!data) return [];
    const kw = q.trim().toLowerCase();
    return data.items.filter((i) => {
      if (judgeF && i.judge !== judgeF) return false;
      if (clsF && i.cls !== clsF) return false;
      if (kw && !(`${i.sku} ${i.model} ${i.trk} ${i.hub}`.toLowerCase().includes(kw))) return false;
      return true;
    });
  }, [data, q, judgeF, clsF]);

  if (error) return <Alert severity="error">货件运输加载失败：{error}</Alert>;
  if (!data) return <Box sx={{ display: 'flex', justifyContent: 'center', mt: 8 }}><CircularProgress /></Box>;

  const k = data.kpi;
  const hubs = data.byHub.slice(0, 14);

  const hubOption = {
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0 },
    grid: { left: 110, right: 30, top: 20, bottom: 40 },
    xAxis: { type: 'value', name: '票数' },
    yAxis: {
      type: 'category', inverse: true,
      data: hubs.map((h) => h.hub),
      axisLabel: { fontSize: 10 },
    },
    series: [
      {
        name: '总票数', type: 'bar', barWidth: 10,
        data: hubs.map((h) => h.total),
        itemStyle: { color: '#90a4ae' },
      },
      {
        name: '超时票数', type: 'bar', barWidth: 10,
        data: hubs.map((h) => h.over),
        itemStyle: { color: '#c62828' },
        label: { show: true, position: 'right' },
      },
    ],
  };

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
        <Box>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>货件运输看板</Typography>
          <Typography variant="caption" color="text.secondary">
            Part5 发货 × Part8 到货按货件号配对，逐票对照渠道目标（空运15 / 海运35 / 卡航35）· 计算于 {data.computedAt}
          </Typography>
        </Box>
        <Button variant="outlined" startIcon={<DownloadIcon />} href={buildTransportExportUrl()}>
          导出 xlsx
        </Button>
      </Box>

      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid item xs={6} sm={4} md={2}>
          <KpiCard label="货件总票数" value={k.total} sub={`涉及 ${k.skuCount} 个 SKU`} />
        </Grid>
        <Grid item xs={6} sm={4} md={2}>
          <KpiCard label="已到货 / 未到货" value={`${k.arrived} / ${k.notArrived}`} sub="未到货含在途" />
        </Grid>
        <Grid item xs={6} sm={4} md={2}>
          <KpiCard label="超时票数" value={k.over} color="#c62828"
            sub={`占已到货 ${k.overRate}%`} />
        </Grid>
        <Grid item xs={6} sm={4} md={2}>
          <KpiCard label="平均 / 最长运输" value={`${k.avgDays} / ${k.maxDays} 天`} sub="发货 → 到货" />
        </Grid>
        <Grid item xs={6} sm={4} md={2}>
          <KpiCard label="未发货渠道" value={k.noShip} color="#616161"
            sub="Part5 有渠道仓但无货件号" />
        </Grid>
        <Grid item xs={6} sm={4} md={2}>
          <KpiCard label="未发货渠道清单" value={k.noShipHubs.length} color="#616161"
            sub={k.noShipHubs.slice(0, 3).join('、') + (k.noShipHubs.length > 3 ? '…' : '')} />
        </Grid>
      </Grid>

      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid item xs={12} md={4}>
          <Paper sx={{ p: 2, height: '100%' }}>
            <Typography variant="subtitle2">按运输方式</Typography>
            {data.byMode.map((m) => (
              <Box key={m.cls} sx={{ mt: 1 }}>
                <Typography variant="body2">
                  <b style={{ color: m.cls === '空运' ? '#0288d1' : '#6a1b9a' }}>{m.cls}</b>
                  　总 {m.total} 票 · 已到 {m.arrived} · 超时 <b style={{ color: '#c62828' }}>{m.over}</b>
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  平均 {m.avgDays} 天 · 最长 {m.maxDays} 天 · 最长超目标 {m.maxOverDays} 天
                </Typography>
              </Box>
            ))}
          </Paper>
        </Grid>
        <Grid item xs={12} md={8}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2">各渠道超时票数 TOP（灰=总票数，红=超时）</Typography>
            <EChart option={hubOption} height={300} />
          </Paper>
        </Grid>
      </Grid>

      <Paper sx={{ mb: 2 }}>
        <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', p: 2, flexWrap: 'wrap' }}>
          <Typography variant="subtitle2">逐票明细（{rows.length} 票）</Typography>
          <Box sx={{ flexGrow: 1 }} />
          <TextField size="small" label="SKU / 型号 / 货件号 / 渠道" value={q}
            onChange={(e) => { setQ(e.target.value); setPage(0); }} sx={{ width: 230 }} />
          <TextField size="small" select label="判定" value={judgeF}
            onChange={(e) => { setJudgeF(e.target.value); setPage(0); }} sx={{ width: 110 }}>
            <MenuItem value="">全部</MenuItem>
            <MenuItem value="超时">超时</MenuItem>
            <MenuItem value="未到货">未到货</MenuItem>
            <MenuItem value="未发货">未发货</MenuItem>
            <MenuItem value="达标">达标</MenuItem>
          </TextField>
          <TextField size="small" select label="方式" value={clsF}
            onChange={(e) => { setClsF(e.target.value); setPage(0); }} sx={{ width: 110 }}>
            <MenuItem value="">全部</MenuItem>
            <MenuItem value="空运">空运</MenuItem>
            <MenuItem value="海运">海运</MenuItem>
          </TextField>
        </Box>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>SKU</TableCell>
                <TableCell>型号</TableCell>
                <TableCell>货件号</TableCell>
                <TableCell>渠道仓</TableCell>
                <TableCell>方式</TableCell>
                <TableCell>发货</TableCell>
                <TableCell>到货</TableCell>
                <TableCell>运输(天)</TableCell>
                <TableCell>目标(天)</TableCell>
                <TableCell>判定</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows
                .slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage)
                .map((i, idx) => {
                  const c = JUDGE_COLOR[i.judge] || { bg: '#f5f5f5', color: '#616161' };
                  return (
                    <TableRow key={`${i.sku}-${i.trk}-${idx}`} hover>
                      <TableCell><b>{i.sku}</b></TableCell>
                      <TableCell>{i.model || '—'}</TableCell>
                      <TableCell>
                        {i.trk || <Typography variant="caption" color="text.secondary">（无货件号）</Typography>}
                      </TableCell>
                      <TableCell>{i.hub || '—'}</TableCell>
                      <TableCell>{i.mode || '—'}</TableCell>
                      <TableCell>{i.ship || '—'}</TableCell>
                      <TableCell>{i.arr || '—'}</TableCell>
                      <TableCell>{i.days ?? '—'}</TableCell>
                      <TableCell>{i.target ?? '—'}</TableCell>
                      <TableCell>
                        <Chip size="small" label={i.judge}
                          sx={{ bgcolor: c.bg, color: c.color, fontWeight: 700 }} />
                      </TableCell>
                    </TableRow>
                  );
                })}
            </TableBody>
          </Table>
        </TableContainer>
        <TablePagination
          component="div"
          count={rows.length}
          page={page}
          onPageChange={(e, p) => setPage(p)}
          rowsPerPage={rowsPerPage}
          onRowsPerPageChange={(e) => { setRowsPerPage(parseInt(e.target.value, 10)); setPage(0); }}
          rowsPerPageOptions={[25, 50, 100]}
          labelRowsPerPage="每页"
        />
      </Paper>

      <Paper sx={{ p: 2 }}>
        <Typography variant="subtitle2" gutterBottom>口径说明</Typography>
        <Typography variant="body2" color="text.secondary">
          · 货件配对与目标同复盘引擎（`review.py` build_shipments / transport_rows）：Part5 发货 × Part8 到货按货件号配对；
          目标按渠道仓推断运输方式（空运15 / 海运35 / 卡航35 / 美客多·墨西哥等归海运35）。
        </Typography>
        <Typography variant="body2" color="text.secondary">
          · 「未发货」为 Part5 已立项渠道但无货件号的占位记录（不计超时统计）；其停滞天数与超时判定见「加急预警」页。
        </Typography>
      </Paper>
    </Box>
  );
}
