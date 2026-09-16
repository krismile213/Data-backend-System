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
  TableRow,
  TextField,
  MenuItem,
  Chip,
  CircularProgress,
  Alert,
  Dialog,
  DialogTitle,
  DialogContent,
  LinearProgress,
  Tooltip,
  Button,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import EChart from '../components/EChart.jsx';
import { fetchReviewOverview, fetchReviewSku, buildReviewExportUrl } from '../api.js';

const PART_SHORT = {
  Part1: 'P1 开发', Part2: 'P2 首单', Part3: 'P3 到货', Part4: 'P4 上架',
  Part5: 'P5 发货', Part6: 'P6 素材', Part7: 'P7 广告', Part8: 'P8 渠到货',
  Part9: 'P9 检查',
};

const DEPT_COLOR = { 产品: '#1e5eff', 运营: '#2e7d32', 后勤: '#ed6c02' };

function KpiCard({ label, value, sub, color = '#1a2740' }) {
  return (
    <Paper sx={{ p: 2, height: '100%' }}>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography variant="h5" sx={{ fontWeight: 700, color, mt: 0.5 }}>{value}</Typography>
      {sub && <Typography variant="caption" color="text.secondary">{sub}</Typography>}
    </Paper>
  );
}

export default function Review() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');
  const [devFilter, setDevFilter] = useState('');
  const [validFilter, setValidFilter] = useState('');
  const [detailSku, setDetailSku] = useState(null);
  const [detail, setDetail] = useState(null);
  const [personQ, setPersonQ] = useState('');

  useEffect(() => {
    fetchReviewOverview().then(setData).catch((e) => setError(e.message));
  }, []);

  const openDetail = (sku) => {
    setDetailSku(sku);
    setDetail(null);
    fetchReviewSku(sku).then(setDetail).catch(() => setDetail({ error: true }));
  };

  const rows = useMemo(() => {
    if (!data) return [];
    return data.rows.filter((r) => {
      if (devFilter && r.devType !== devFilter) return false;
      if (validFilter === 'valid' && !r.valid) return false;
      if (validFilter === 'invalid' && r.valid) return false;
      if (q && !(r.sku.toLowerCase().includes(q.toLowerCase())
        || (r.model || '').toLowerCase().includes(q.toLowerCase()))) return false;
      return true;
    });
  }, [data, q, devFilter, validFilter]);

  const personRows = useMemo(() => {
    if (!data) return [];
    const kw = personQ.trim().toLowerCase();
    return data.persons.filter((p) => !kw || p.operator.toLowerCase().includes(kw));
  }, [data, personQ]);

  if (error) return <Alert severity="error">复盘总览加载失败：{error}</Alert>;
  if (!data) return <Box sx={{ display: 'flex', justifyContent: 'center', mt: 8 }}><CircularProgress /></Box>;

  const validN = data.rows.filter((r) => r.valid).length;
  const totalN = data.rows.length;
  const devKpi = data.devDist['提前开发'] || { total: 0 };
  const bottle1 = data.bottleneckTop[0];

  // ---- 图表 options ----
  const pieOption = {
    tooltip: { trigger: 'item' },
    legend: { bottom: 0 },
    series: [{
      type: 'pie', radius: ['45%', '70%'],
      label: { formatter: '{b}: {c} ({d}%)' },
      data: [
        { name: '有效复盘', value: validN, itemStyle: { color: '#2e7d32' } },
        { name: '未完成三部门', value: totalN - validN, itemStyle: { color: '#b0bec5' } },
      ],
    }],
  };

  const bottleOption = {
    tooltip: { trigger: 'axis' },
    grid: { left: 150, right: 30, top: 20, bottom: 30 },
    xAxis: { type: 'value', name: 'SKU 数' },
    yAxis: {
      type: 'category',
      data: data.bottleneckTop.map((b) => `${b.seg}`),
      inverse: true,
      axisLabel: { fontSize: 11 },
    },
    series: [{
      type: 'bar', barWidth: 22,
      data: data.bottleneckTop.map((b) => b.count),
      itemStyle: { color: '#d84315' },
      label: { show: true, position: 'right' },
    }],
  };

  // 段级达标率（实际动作映射口径）
  const statRows = data.segStats || [];
  const rateOption = {
    tooltip: {
      trigger: 'axis',
      formatter: (params) => {
        const p = params[0];
        const st = statRows[p.dataIndex];
        return `${st.dept} · ${st.seg}<br/>平均实际 ${st.avgActual} 天 / 目标 ${st.target ?? '不考核'} 天<br/>` +
          `样本 ${st.sampleCount}，超时 ${st.overCount}，达标率 ${st.onTimeRate}%<br/>P90 ${st.p90Actual} 天，最长 ${st.maxActual} 天`;
      },
    },
    grid: { left: 150, right: 40, top: 20, bottom: 30 },
    xAxis: { type: 'value', max: 100, name: '达标率 %' },
    yAxis: {
      type: 'category', inverse: true,
      data: statRows.map((s) => `${s.dept}·${s.seg}`),
      axisLabel: { fontSize: 10 },
    },
    series: [{
      type: 'bar', barWidth: 20,
      data: statRows.map((s) => ({
        value: s.onTimeRate,
        itemStyle: { color: s.onTimeRate >= 70 ? '#2e7d32' : s.onTimeRate >= 40 ? '#f9a825' : '#c62828' },
      })),
      label: { show: true, position: 'right', formatter: '{c}%' },
    }],
  };

  // 超标天数分布（全部考核段：实际 - 目标 > 0 的部分）
  const overDist = data.overDaysDist || [];
  const overDistOption = {
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'category', data: overDist.map((d) => d.label) },
    yAxis: { type: 'value', name: '段次数' },
    series: [{
      type: 'bar', barWidth: 30,
      data: overDist.map((d) => d.count),
      itemStyle: { color: '#d84315' },
      label: { show: true, position: 'top' },
    }],
  };

  // 月度达标趋势（按段终点月份）
  const trend = data.monthlyTrend || [];
  const trendOption = {
    tooltip: {
      trigger: 'axis',
      formatter: (ps) => ps.map((p) => {
        const t = trend[p.dataIndex];
        return `${t.month}<br/>达标率 ${t.onTimeRate}%<br/>考核段 ${t.sampleCount}，超时 ${t.overCount}`;
      }).join(''),
    },
    grid: { left: 50, right: 50, top: 30, bottom: 30 },
    xAxis: { type: 'category', data: trend.map((t) => t.month) },
    yAxis: [
      { type: 'value', name: '达标率 %', max: 100 },
      { type: 'value', name: '考核段数', splitLine: { show: false } },
    ],
    series: [
      {
        name: '达标率', type: 'line', smooth: true, data: trend.map((t) => t.onTimeRate),
        itemStyle: { color: '#1e5eff' },
        label: { show: true, position: 'top', formatter: '{c}%', fontSize: 10 },
      },
      {
        name: '考核段数', type: 'bar', yAxisIndex: 1, barWidth: 16,
        data: trend.map((t) => t.sampleCount),
        itemStyle: { color: '#cfd8dc' },
      },
    ],
  };

  return (
    <Box>
      <Typography variant="h6" gutterBottom>
        大流程复盘总览
        <Typography variant="caption" color="text.secondary" sx={{ ml: 2 }}>
          口径：三部门终节点全完成即有效（全量数据，不限时间窗口）· 计算于 {data.computedAt}
        </Typography>
      </Typography>

      {/* KPI */}
      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid item xs={6} md={3}>
          <KpiCard label="复盘 SKU 总数（已结束单据）" value={totalN} sub="跨全部批次去重" />
        </Grid>
        <Grid item xs={6} md={3}>
          <KpiCard label="有效复盘" value={`${validN}（${Math.round(100 * validN / totalN)}%）`}
            sub="产品/运营/后勤三部门终节点全完成" color="#2e7d32" />
        </Grid>
        <Grid item xs={6} md={3}>
          <KpiCard label="提前开发 SKU" value={`${devKpi.total}`}
            sub={`有效 ${data.devDist['提前开发']?.valid ?? 0} 个 · 占比 ${Math.round(100 * devKpi.total / totalN)}%`} />
        </Grid>
        <Grid item xs={6} md={3}>
          <KpiCard label="头号卡点环节" value={bottle1 ? bottle1.seg : '—'}
            sub={bottle1 ? `${bottle1.count} 个有效复盘 SKU 的最长段在此环节` : ''}
            color="#d84315" />
        </Grid>
      </Grid>

      {/* 图表区 */}
      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid item xs={12} md={4}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2">有效 / 无效复盘分布</Typography>
            <EChart option={pieOption} height={260} />
          </Paper>
        </Grid>
        <Grid item xs={12} md={4}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2">卡点环节 TOP（有效复盘 SKU 最长段）</Typography>
            <EChart option={bottleOption} height={260} />
          </Paper>
        </Grid>
        <Grid item xs={12} md={4}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2">各段达标率（实际动作映射，悬停看明细）</Typography>
            <EChart option={rateOption} height={260} />
          </Paper>
        </Grid>
      </Grid>

      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid item xs={12} md={6}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2">超标天数分布（超时考核段的超出幅度）</Typography>
            <EChart option={overDistOption} height={260} />
          </Paper>
        </Grid>
        <Grid item xs={12} md={6}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2">月度达标趋势（按段终点月份）</Typography>
            <EChart option={trendOption} height={260} />
          </Paper>
        </Grid>
      </Grid>

      {/* SKU 总览表 */}
      <Paper sx={{ mb: 2 }}>
        <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', p: 2, flexWrap: 'wrap' }}>
          <Typography variant="subtitle2">SKU 复盘总览（点击行查看环节档案）</Typography>
          <Box sx={{ flexGrow: 1 }} />
          <Button variant="outlined" size="small" startIcon={<DownloadIcon />}
            href={buildReviewExportUrl()}>
            导出 xlsx
          </Button>
          <TextField size="small" label="搜索 SKU / 型号" value={q}
            onChange={(e) => setQ(e.target.value)} sx={{ width: 220 }} />
          <TextField size="small" select label="开发类型" value={devFilter}
            onChange={(e) => setDevFilter(e.target.value)} sx={{ width: 140 }}>
            <MenuItem value="">全部</MenuItem>
            <MenuItem value="常规开发">常规开发</MenuItem>
            <MenuItem value="提前开发">提前开发</MenuItem>
          </TextField>
          <TextField size="small" select label="有效性" value={validFilter}
            onChange={(e) => setValidFilter(e.target.value)} sx={{ width: 140 }}>
            <MenuItem value="">全部</MenuItem>
            <MenuItem value="valid">有效复盘</MenuItem>
            <MenuItem value="invalid">三部门未完成</MenuItem>
          </TextField>
        </Box>
        <TableContainer sx={{ maxHeight: 480 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell>SKU</TableCell>
                <TableCell>型号</TableCell>
                <TableCell>开发类型</TableCell>
                <TableCell>产品部·签样</TableCell>
                <TableCell>运营部·上架</TableCell>
                <TableCell>后勤部·到货</TableCell>
                <TableCell>状态</TableCell>
                <TableCell>头号卡点</TableCell>
                <TableCell align="right">总贡献(天)</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.sku} hover onClick={() => openDetail(r.sku)}
                  sx={{ cursor: 'pointer' }}>
                  <TableCell sx={{ fontWeight: 600 }}>{r.sku}</TableCell>
                  <TableCell>{r.model || '—'}</TableCell>
                  <TableCell>
                    <Chip size="small" variant="outlined"
                      label={r.devType}
                      color={r.devType === '提前开发' ? 'secondary' : 'default'} />
                  </TableCell>
                  <TableCell>{r.prodAt || '—'}</TableCell>
                  <TableCell>{r.opAt || '—'}</TableCell>
                  <TableCell>{r.logiAt || '—'}</TableCell>
                  <TableCell>
                    {r.valid
                      ? <Chip size="small" color="success" label="有效" />
                      : <Tooltip title={r.invalidReason}><Chip size="small" label="未完成" /></Tooltip>}
                  </TableCell>
                  <TableCell>
                    {r.bottleneck
                      ? `${r.bottleneck.seg} ${r.bottleneck.days}天`
                      : '—'}
                  </TableCell>
                  <TableCell align="right"
                    sx={{ color: r.contributionTotal == null ? 'inherit'
                      : r.contributionTotal >= 0 ? '#2e7d32' : '#c62828' }}>
                    {r.contributionTotal == null ? '—' : r.contributionTotal}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
        <Typography variant="caption" color="text.secondary" sx={{ p: 2, display: 'block' }}>
          共 {rows.length} 个 SKU · 口径：实际动作映射（每段=起点动作→终点动作）· 总贡献 = Σ(段目标 − 实际天数)，正数表示快于目标，负数表示超时（仅考核段计入）
        </Typography>
      </Paper>

      {/* 人员时效达成 */}
      <Paper>
        <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', p: 2 }}>
          <Typography variant="subtitle2">人员 × 环节 时效达成</Typography>
          <Box sx={{ flexGrow: 1 }} />
          <TextField size="small" label="搜索人员" value={personQ}
            onChange={(e) => setPersonQ(e.target.value)} sx={{ width: 200 }} />
        </Box>
        <TableContainer sx={{ maxHeight: 420 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell>操作人</TableCell>
                <TableCell>部门 · 环节</TableCell>
                <TableCell align="right">参与 SKU 数</TableCell>
                <TableCell align="right">超时 SKU 数</TableCell>
                <TableCell>达标率</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {personRows.map((p, i) => (
                <TableRow key={`${p.operator}-${p.seg}-${i}`}>
                  <TableCell>{p.operator}</TableCell>
                  <TableCell>
                    <Box component="span" sx={{ color: DEPT_COLOR[p.dept] || 'inherit', fontWeight: 600, mr: 1 }}>
                      {p.dept}
                    </Box>
                    {p.seg}
                  </TableCell>
                  <TableCell align="right">{p.skuCount}</TableCell>
                  <TableCell align="right"
                    sx={{ color: p.overCount > 0 ? '#c62828' : 'inherit' }}>
                    {p.overCount}
                  </TableCell>
                  <TableCell sx={{ width: 220 }}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <Box sx={{ flexGrow: 1 }}>
                        <LinearProgress
                          variant="determinate"
                          value={p.onTimeRate}
                          sx={{ height: 8, borderRadius: 4,
                            '& .MuiLinearProgress-bar': {
                              bgcolor: p.onTimeRate >= 70 ? '#2e7d32'
                                : p.onTimeRate >= 40 ? '#f9a825' : '#c62828',
                            } }}
                        />
                      </Box>
                      <Typography variant="caption" sx={{ width: 44 }}>{p.onTimeRate}%</Typography>
                    </Box>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>

      {/* SKU 复盘档案弹窗 */}
      <Dialog open={!!detailSku} onClose={() => setDetailSku(null)} maxWidth="md" fullWidth>
        <DialogTitle>
          复盘档案 · {detailSku}
          {detail && (
            <Chip size="small" sx={{ ml: 2 }} variant="outlined"
              label={detail.devType}
              color={detail.devType === '提前开发' ? 'secondary' : 'default'} />
          )}
        </DialogTitle>
        <DialogContent dividers>
          {!detail && <Box sx={{ py: 4, textAlign: 'center' }}><CircularProgress /></Box>}
          {detail && detail.error && <Alert severity="error">档案加载失败</Alert>}
          {detail && !detail.error && (
            <>
              <Box sx={{ display: 'flex', gap: 2, mb: 2, flexWrap: 'wrap' }}>
                {Object.entries(detail.dept).map(([k, v]) => (
                  <Chip key={k} variant="outlined"
                    label={`${k}：${v.time || v.note}`}
                    color={v.time ? 'success' : 'default'} />
                ))}
                {detail.bottleneck && (
                  <Chip color="warning"
                    label={`卡点：${detail.bottleneck.dept} ${detail.bottleneck.seg}（${detail.bottleneck.days} 天）`} />
                )}
              </Box>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>部门</TableCell>
                    <TableCell>环节</TableCell>
                    <TableCell>起点 → 终点</TableCell>
                    <TableCell align="right">实际(天)</TableCell>
                    <TableCell align="right">目标(天)</TableCell>
                    <TableCell>判定</TableCell>
                    <TableCell align="right">贡献(天)</TableCell>
                    <TableCell>执行人</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {detail.segments.map((s, i) => (
                    <TableRow key={i} sx={s.gap ? { bgcolor: 'action.hover' } : undefined}>
                      <TableCell>{s.gap ? '留空' : s.dept}</TableCell>
                      <TableCell sx={{ fontWeight: 600 }}>{s.seg}</TableCell>
                      <TableCell>{s.start || '—'} → {s.end || '—'}</TableCell>
                      <TableCell align="right">{s.gap ? '—' : (s.days ?? '—')}</TableCell>
                      <TableCell align="right">
                        {s.gap || !s.assessed
                          ? <Typography variant="caption">{s.gap ? '—' : '不考核'}</Typography>
                          : s.target}
                      </TableCell>
                      <TableCell>
                        <Typography variant="caption" sx={{
                          fontWeight: 600,
                          color: String(s.judge || '').startsWith('✗') ? '#c62828'
                            : String(s.judge || '').startsWith('✓') ? '#2e7d32'
                              : String(s.judge || '').startsWith('⚠') ? '#ed6c02' : 'text.secondary',
                        }}>
                          {s.judge || '—'}
                        </Typography>
                      </TableCell>
                      <TableCell align="right"
                        sx={{ color: s.contribution == null ? 'inherit'
                          : s.contribution >= 0 ? '#2e7d32' : '#c62828' }}>
                        {s.gap ? '—' : (s.contribution == null ? '—' : s.contribution)}
                      </TableCell>
                      <TableCell>{s.executor || '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {(detail.shipments?.length || 0) > 0 && (
                <>
                  <Typography variant="subtitle2" sx={{ mt: 2, mb: 1 }}>
                    货件运输（发货 → 到货，目标按渠道：空运15/海运35/卡航35天；红底=渠道未发货异常）
                  </Typography>
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>货件号</TableCell>
                        <TableCell>渠道仓</TableCell>
                        <TableCell>发货</TableCell>
                        <TableCell>到货</TableCell>
                        <TableCell align="right">运输(天)</TableCell>
                        <TableCell align="right">目标(天)</TableCell>
                        <TableCell>判定</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {detail.shipments.map((s, i) => (
                        <TableRow key={`${s.trk}-${s.hub}-${i}`}
                          sx={{ bgcolor: s.noShip ? '#fdecea' : 'inherit' }}>
                          <TableCell sx={{ fontFamily: 'monospace' }}>{s.trk || '—'}</TableCell>
                          <TableCell>{s.hub || '—'}</TableCell>
                          <TableCell>{s.ship || '—'}</TableCell>
                          <TableCell>{s.arr || '—'}</TableCell>
                          <TableCell align="right">{s.days ?? '—'}</TableCell>
                          <TableCell align="right">{s.target}</TableCell>
                          <TableCell>
                            <Typography variant="caption" sx={{
                              fontWeight: 600,
                              color: String(s.judge || '').startsWith('✗') ? '#c62828'
                                : String(s.judge || '').startsWith('✓') ? '#2e7d32' : 'text.secondary',
                            }}>
                              {s.judge}
                            </Typography>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </>
              )}
            </>
          )}
        </DialogContent>
      </Dialog>
    </Box>
  );
}
