import React, { useMemo, useState } from 'react';
import SectionCard from '../components/SectionCard.jsx';
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
  Button,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import EChart from '../components/EChart.jsx';
import { fetchTransport, buildTransportExportUrl } from '../api.js';
import KpiCard from '../components/KpiCard.jsx';
import PageHeader from '../components/PageHeader.jsx';
import { LoadingState, ErrorState } from '../components/StateViews.jsx';
import { SEMANTIC } from '../theme.js';
import useApi from '../hooks/useApi.js';

/** 逐票判定 → 徽标配色（浅底 + 同色系深字） */
const JUDGE_COLOR = {
  超时: { bg: SEMANTIC.dangerBg, color: SEMANTIC.danger },
  未到货: { bg: SEMANTIC.warningBg, color: SEMANTIC.warningDeep },
  未发货: { bg: SEMANTIC.neutralBg, color: SEMANTIC.neutral },
  达标: { bg: SEMANTIC.successBg, color: SEMANTIC.success },
};

/** 追踪状态 → 徽标配色（在途=蓝 / 留观=绿 / 已归档=灰 / 未发货=浅灰） */
const TRACK_COLOR = {
  在途: { bg: '#E3F2FD', color: '#1565C0' },
  未发货: { bg: SEMANTIC.neutralBg, color: SEMANTIC.neutral },
};
function trackStyle(track) {
  if (!track) return TRACK_COLOR.未发货;
  if (track.startsWith('留观')) return { bg: '#E8F5E9', color: '#2E7D32' };
  if (track.startsWith('已归档')) return { bg: '#F5F5F5', color: '#757575' };
  return TRACK_COLOR[track] || TRACK_COLOR.未发货;
}

export default function Transport() {
  const { data, error } = useApi(() => fetchTransport(), []);
  const [q, setQ] = useState('');
  const [judgeF, setJudgeF] = useState('');
  const [clsF, setClsF] = useState('');
  const [trackF, setTrackF] = useState('');
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(50);

  const rows = useMemo(() => {
    if (!data) return [];
    const kw = q.trim().toLowerCase();
    return data.items.filter((i) => {
      if (judgeF && i.judge !== judgeF) return false;
      if (clsF && i.cls !== clsF) return false;
      if (trackF === '追踪中' && i.archived) return false;
      if (trackF === '已归档' && !i.archived) return false;
      if (kw && !(`${i.sku} ${i.model} ${i.trk} ${i.hub}`.toLowerCase().includes(kw))) return false;
      return true;
    });
  }, [data, q, judgeF, clsF, trackF]);

  if (error) return <ErrorState prefix="货件运输加载失败" error={error} />;
  if (!data) return <LoadingState />;

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
        itemStyle: { color: SEMANTIC.muted },
      },
      {
        name: '超时票数', type: 'bar', barWidth: 10,
        data: hubs.map((h) => h.over),
        itemStyle: { color: SEMANTIC.danger },
        label: { show: true, position: 'right' },
      },
    ],
  };

  return (
    <Box>
      <PageHeader
        title="货件运输看板"
        subtitle={`Part5 发货 × Part8 到货按货件号配对，逐票对照渠道目标（空运15 / 海运35 / 卡航35）· 明细全量保留，到货 5 天内标「留观」、超出标「已归档」 · 计算于 ${data.computedAt}`}
        actions={
          <Button variant="outlined" startIcon={<DownloadIcon />} href={buildTransportExportUrl()}>
            导出 xlsx
          </Button>
        }
      />

      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid item xs={6} sm={4} md={2}>
          <KpiCard label="货件总票数" value={k.total} sub={`涉及 ${k.skuCount} 个 SKU`} />
        </Grid>
        <Grid item xs={6} sm={4} md={2}>
          <KpiCard label="已到货 / 未到货" value={`${k.arrived} / ${k.notArrived}`}
            sub={`追踪中 ${k.tracking} 票${k.archived ? ` · 到货超${k.retentionDays}天归档 ${k.archived} 票` : ''}`} />
        </Grid>
        <Grid item xs={6} sm={4} md={2}>
          <KpiCard
            label="超时票数"
            value={k.over}
            color={SEMANTIC.danger}
            sub={`占已到货 ${k.overRate}%`}
          />
        </Grid>
        <Grid item xs={6} sm={4} md={2}>
          <KpiCard label="平均 / 最长运输" value={`${k.avgDays} / ${k.maxDays} 天`} sub="发货 → 到货" />
        </Grid>
        <Grid item xs={6} sm={4} md={2}>
          <KpiCard
            label="未发货渠道"
            value={k.noShip}
            color={SEMANTIC.neutral}
            sub="Part5 有渠道仓但无货件号"
          />
        </Grid>
        <Grid item xs={6} sm={4} md={2}>
          <KpiCard
            label="未发货渠道清单"
            value={k.noShipHubs.length}
            color={SEMANTIC.neutral}
            sub={k.noShipHubs.slice(0, 3).join('、') + (k.noShipHubs.length > 3 ? '…' : '')}
          />
        </Grid>
      </Grid>

      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid item xs={12} md={4}>
          <SectionCard title="按运输方式" sx={{ height: '100%' }}>
            {data.byMode.map((m) => (
              <Box key={m.cls} sx={{ mt: 1 }}>
                <Typography variant="body2">
                  <b style={{ color: m.cls === '空运' ? SEMANTIC.sky : SEMANTIC.purple }}>{m.cls}</b>
                  　总 {m.total} 票 · 已到 {m.arrived} · 超时{' '}
                  <b style={{ color: SEMANTIC.danger }}>{m.over}</b>
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  平均 {m.avgDays} 天 · 最长 {m.maxDays} 天 · 最长超目标 {m.maxOverDays} 天
                </Typography>
              </Box>
            ))}
          </SectionCard>
        </Grid>
        <Grid item xs={12} md={8}>
          <SectionCard title="各渠道超时票数 TOP（灰=总票数，红=超时）">
            <EChart option={hubOption} height={300} />
          </SectionCard>
        </Grid>
      </Grid>

      <Paper sx={{ mb: 2 }}>
        <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', p: 2, flexWrap: 'wrap' }}>
          <Typography variant="subtitle2">逐票明细（{rows.length} 条）</Typography>
          <Typography variant="caption" color="text.secondary">
            含未发货占位 {rows.filter((i) => i.noShip).length} 条（无货件号，不计入票数）
          </Typography>
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
          <TextField size="small" select label="追踪" value={trackF}
            onChange={(e) => { setTrackF(e.target.value); setPage(0); }} sx={{ width: 120 }}>
            <MenuItem value="">全部</MenuItem>
            <MenuItem value="追踪中">追踪中</MenuItem>
            <MenuItem value="已归档">已归档</MenuItem>
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
                <TableCell>已运输(天)</TableCell>
                <TableCell>目标(天)</TableCell>
                <TableCell>判定</TableCell>
                <TableCell>追踪</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows
                .slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage)
                .map((i, idx) => {
                  const c = JUDGE_COLOR[i.judge] || { bg: SEMANTIC.neutralBg, color: SEMANTIC.neutral };
                  const t = trackStyle(i.track);
                  // 已运输天数：在途 = 发货→至今（可能已超目标 → 红字），已到货 = 发货→到货
                  const overDays = (i.elapsed != null && i.target != null
                    && Number(i.elapsed) > Number(i.target));
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
                      <TableCell sx={overDays ? { color: SEMANTIC.danger, fontWeight: 700 } : {}}>
                        {i.elapsed ?? '—'}
                      </TableCell>
                      <TableCell>{i.target ?? '—'}</TableCell>
                      <TableCell>
                        <Chip size="small" label={i.judge}
                          sx={{ bgcolor: c.bg, color: c.color, fontWeight: 700 }} />
                      </TableCell>
                      <TableCell>
                        {i.track
                          ? <Chip size="small" label={i.track}
                              sx={{ bgcolor: t.bg, color: t.color, fontWeight: 700 }} />
                          : '—'}
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
        <Typography variant="body2" color="text.secondary">
          · 「货件明细」全量保留：所有票（含到货很久的历史票）都在本页，可按「追踪」筛选只看追踪中；
          到货 5 天内标「留观 N/5 天」，超出标「已归档(到货 N 天)」——只是状态标记，不再隐藏。
        </Typography>
        <Typography variant="body2" color="text.secondary">
          · 事中日报的「货件运输」sheet 与本页**同口径全量保留**（SKU 在研期间每一票都在），
          5 天留观同样只改状态文案，不删行。
        </Typography>
        <Typography variant="body2" color="text.secondary">
          · 「已运输(天)」在途票 = 发货→至今（超目标红字），已到货票 = 发货→到货。
        </Typography>
      </Paper>
    </Box>
  );
}
