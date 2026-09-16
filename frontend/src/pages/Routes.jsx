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
  Button,
  Collapse,
  Tooltip,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import EChart from '../components/EChart.jsx';
import { fetchRoutes, buildRoutesExportUrl, fetchReviewSku } from '../api.js';

const ROUTE_COLOR = { 运营: '#1e5eff', 空运: '#0288d1', 海运: '#6a1b9a' };
const GRADE_COLOR = { 优: '#2e7d32', 良: '#f9a825', 差: '#c62828', '-': '#9e9e9e' };

function KpiCard({ label, value, sub, color = '#1a2740' }) {
  return (
    <Paper sx={{ p: 2, height: '100%' }}>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography variant="h5" sx={{ fontWeight: 700, color, mt: 0.5 }}>{value}</Typography>
      {sub && <Typography variant="caption" color="text.secondary">{sub}</Typography>}
    </Paper>
  );
}

/** 路线小徽标：名字 + 天数 + 评级色点 */
function RouteChip({ rt }) {
  const color = GRADE_COLOR[rt.grade] || '#9e9e9e';
  const label = rt.days === null || rt.days === undefined
    ? `${rt.route} —` : `${rt.route} ${rt.days}天`;
  return (
    <Tooltip title={`${rt.route}：${rt.start || '—'} → ${rt.end || '—'}，${rt.days ?? '—'} 天（优≤${rt.good}${rt.limit ? ` / 良≤${rt.limit}` : ' / 不列良'}）`}>
      <Chip size="small" label={`${label} ${rt.grade}`}
        sx={{ bgcolor: `${color}18`, color, fontWeight: 700, mr: 0.5, mb: 0.5 }} />
    </Tooltip>
  );
}

/** 展开行：该 SKU 的段级明细（复用复盘档案接口） */
function SegDetail({ sku }) {
  const [detail, setDetail] = useState(null);
  const [err, setErr] = useState('');
  useEffect(() => {
    fetchReviewSku(sku).then(setDetail).catch((e) => setErr(e.message));
  }, [sku]);
  if (err) return <Alert severity="error" sx={{ m: 1 }}>加载失败：{err}</Alert>;
  if (!detail) return <Box sx={{ display: 'flex', justifyContent: 'center', p: 2 }}><CircularProgress size={20} /></Box>;
  return (
    <TableContainer sx={{ maxHeight: 340 }}>
      <Table size="small" stickyHeader>
        <TableHead>
          <TableRow>
            <TableCell>部门</TableCell>
            <TableCell>环节</TableCell>
            <TableCell>起点</TableCell>
            <TableCell>终点</TableCell>
            <TableCell>耗时(天)</TableCell>
            <TableCell>目标(天)</TableCell>
            <TableCell>判定</TableCell>
            <TableCell>执行人</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {(detail.segments || []).map((s, idx) => (
            <TableRow key={idx}>
              <TableCell>{s.gap ? '留空' : s.dept}</TableCell>
              <TableCell>{s.seg}</TableCell>
              <TableCell>{s.start || '—'}</TableCell>
              <TableCell>{s.end || '—'}</TableCell>
              <TableCell>{s.days ?? '—'}</TableCell>
              <TableCell>{s.target ?? '—'}</TableCell>
              <TableCell sx={{ color: String(s.judge || '').startsWith('✗') ? '#c62828' : 'inherit' }}>{s.judge}</TableCell>
              <TableCell>{s.executor || '—'}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

export default function Routes() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');
  const [routeFilter, setRouteFilter] = useState('');
  const [gradeFilter, setGradeFilter] = useState('');
  const [expanded, setExpanded] = useState(null);

  useEffect(() => {
    fetchRoutes().then(setData).catch((e) => setError(e.message));
  }, []);

  const rows = useMemo(() => {
    if (!data) return [];
    const kw = q.trim().toLowerCase();
    return data.rows.filter((r) => {
      if (kw && !(r.sku.toLowerCase().includes(kw)
        || (r.model || '').toLowerCase().includes(kw))) return false;
      if (routeFilter) {
        const rt = r.routes.find((x) => x.route === routeFilter);
        if (!rt) return false;
        if (gradeFilter && rt.grade !== gradeFilter) return false;
      } else if (gradeFilter && r.worstGrade !== gradeFilter) return false;
      return true;
    });
  }, [data, q, routeFilter, gradeFilter]);

  if (error) return <Alert severity="error">路线达标加载失败：{error}</Alert>;
  if (!data) return <Box sx={{ display: 'flex', justifyContent: 'center', mt: 8 }}><CircularProgress /></Box>;

  const distOption = {
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0 },
    grid: { left: 50, right: 20, top: 20, bottom: 40 },
    xAxis: { type: 'category', data: ['优', '良', '差', '缺失'] },
    yAxis: { type: 'value', name: 'SKU 数' },
    series: ['运营', '空运', '海运'].map((name) => ({
      name, type: 'bar', barWidth: 18,
      data: ['优', '良', '差', '-'].map((g) => data.routeSummary[name][g]),
      itemStyle: { color: ROUTE_COLOR[name] },
    })),
  };

  const t = data.targets;

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
        <Box>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>路线达标看板</Typography>
          <Typography variant="caption" color="text.secondary">
            口径：运营 = 取数 → 上架/广告（≤{t['运营'].good}优 / ≤{t['运营'].limit}良）·
            空运 = 取数 → 最晚空运到货（≤{t['空运'].good}优 / ≤{t['空运'].limit}良）·
            海运 = 取数 → 最晚海运到货（≤{t['海运'].good}优 / 不列良）· 计算于 {data.computedAt}
          </Typography>
        </Box>
        <Button variant="outlined" startIcon={<DownloadIcon />} href={buildRoutesExportUrl()}>
          导出 xlsx
        </Button>
      </Box>

      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid item xs={12} md={6}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2">三条路线评级分布（按 SKU）</Typography>
            <EChart option={distOption} height={260} />
          </Paper>
        </Grid>
        <Grid item xs={12} md={6}>
          <Grid container spacing={2}>
            {['运营', '空运', '海运'].map((name) => {
              const s = data.routeSummary[name];
              return (
                <Grid item xs={12} sm={4} key={name}>
                  <KpiCard
                    label={`${name}路线`}
                    value={`${s.avgDays ?? '—'} 天`}
                    sub={`优 ${s['优']} · 良 ${s['良']} · 差 ${s['差']} · 缺 ${s['-']}${s.maxDays !== null ? ` · 最长 ${s.maxDays}` : ''}`}
                    color={ROUTE_COLOR[name]}
                  />
                </Grid>
              );
            })}
            <Grid item xs={12} sm={4}>
              <KpiCard label="运营 > 60 天底线" value={data.focusCounts['运营>60天底线']} color="#c62828"
                sub="直接影响上架销售" />
            </Grid>
            <Grid item xs={12} sm={4}>
              <KpiCard label="后勤 > 60 天底线" value={data.focusCounts['后勤>60天底线']} color="#c62828"
                sub="取数 → 最晚渠道仓到货" />
            </Grid>
            <Grid item xs={12} sm={4}>
              <KpiCard label="重点: 后勤 < 运营" value={data.focusCounts['重点:后勤<运营']} color="#f9a825"
                sub="两条均在 60 天内但后勤更快 → 运营环节拖后腿" />
            </Grid>
          </Grid>
        </Grid>
      </Grid>

      <Paper sx={{ mb: 2 }}>
        <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', p: 2, flexWrap: 'wrap' }}>
          <Typography variant="subtitle2">SKU 路线评级（点击行展开段级明细）</Typography>
          <Box sx={{ flexGrow: 1 }} />
          <TextField size="small" label="搜索 SKU / 型号" value={q}
            onChange={(e) => setQ(e.target.value)} sx={{ width: 200 }} />
          <TextField size="small" select label="路线" value={routeFilter}
            onChange={(e) => setRouteFilter(e.target.value)} sx={{ width: 120 }}>
            <MenuItem value="">全部</MenuItem>
            <MenuItem value="运营">运营</MenuItem>
            <MenuItem value="空运">空运</MenuItem>
            <MenuItem value="海运">海运</MenuItem>
          </TextField>
          <TextField size="small" select label="评级" value={gradeFilter}
            onChange={(e) => setGradeFilter(e.target.value)} sx={{ width: 110 }}>
            <MenuItem value="">全部</MenuItem>
            <MenuItem value="优">优</MenuItem>
            <MenuItem value="良">良</MenuItem>
            <MenuItem value="差">差</MenuItem>
          </TextField>
        </Box>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell style={{ width: 40 }} />
                <TableCell>SKU</TableCell>
                <TableCell>型号 / 开发类型</TableCell>
                <TableCell>运营路线</TableCell>
                <TableCell>空运路线</TableCell>
                <TableCell>海运路线</TableCell>
                <TableCell>最差评级</TableCell>
                <TableCell>重点复盘</TableCell>
                <TableCell>头号卡点</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((r) => {
                const rt = (name) => r.routes.find((x) => x.route === name);
                const b = r.bottleneck;
                return (
                  <React.Fragment key={r.sku}>
                    <TableRow hover onClick={() => setExpanded(expanded === r.sku ? null : r.sku)}>
                      <TableCell>
                        <KeyboardArrowDownIcon
                          fontSize="small"
                          sx={{ transform: expanded === r.sku ? 'rotate(180deg)' : 'none', transition: '0.2s' }}
                        />
                      </TableCell>
                      <TableCell><b>{r.sku}</b></TableCell>
                      <TableCell>
                        <Typography variant="body2">{r.model || '—'}</Typography>
                        <Typography variant="caption" color="text.secondary">{r.devType}</Typography>
                      </TableCell>
                      <TableCell><RouteChip rt={rt('运营')} /></TableCell>
                      <TableCell><RouteChip rt={rt('空运')} /></TableCell>
                      <TableCell><RouteChip rt={rt('海运')} /></TableCell>
                      <TableCell>
                        <Chip size="small" label={r.worstGrade}
                          sx={{ bgcolor: `${GRADE_COLOR[r.worstGrade]}22`, color: GRADE_COLOR[r.worstGrade], fontWeight: 700 }} />
                      </TableCell>
                      <TableCell>
                        {r.focus
                          ? <Typography variant="caption" sx={{ color: r.focus.startsWith('重点') ? '#f9a825' : '#c62828' }}>{r.focus}</Typography>
                          : '—'}
                      </TableCell>
                      <TableCell>
                        {b ? <Typography variant="caption">{b.dept}·{b.seg}（{b.days}天）</Typography> : '—'}
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell colSpan={9} sx={{ py: 0, border: 0 }}>
                        <Collapse in={expanded === r.sku} unmountOnExit>
                          <Box sx={{ py: 1 }}>
                            <SegDetail sku={r.sku} />
                          </Box>
                        </Collapse>
                      </TableCell>
                    </TableRow>
                  </React.Fragment>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>

      <Paper sx={{ p: 2 }}>
        <Typography variant="subtitle2" gutterBottom>评级口径（移植自 route_check.py）</Typography>
        <Typography variant="body2" color="text.secondary">
          · grade(d)：d ≤ 优线 → 优；d ≤ 良线/底线 → 良；否则差。海运不稳定不列良（≤69 优 / &gt;69 差）。
        </Typography>
        <Typography variant="body2" color="text.secondary">
          · 重点复盘：运营、后勤均 ≤60 天且后勤 &lt; 运营 → 「重点:后勤&lt;运营」，表示运营环节拖后腿。
        </Typography>
        <Typography variant="body2" color="text.secondary">
          · 路线起点统一为「取数」（Part1 取得手机数据）；空运/海运用各自方式下最晚到货的货件判定。
        </Typography>
      </Paper>
    </Box>
  );
}
