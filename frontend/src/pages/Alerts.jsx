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
  Tooltip,
  Collapse,
  IconButton,
} from '@mui/material';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import KeyboardArrowUpIcon from '@mui/icons-material/KeyboardArrowUp';
import DownloadIcon from '@mui/icons-material/Download';
import Button from '@mui/material/Button';
import { fetchAlerts, buildAlertsExportUrl } from '../api.js';

const PRIO_COLOR = {
  P0: { bgcolor: '#c62828', label: 'P0 立即处理' },
  P1: { bgcolor: '#ef6c00', label: 'P1 重点跟进' },
  P2: { bgcolor: '#546e7a', label: 'P2 推进中' },
};

function fmtDays(d) {
  return d === null || d === undefined ? '—' : `${Math.round(d)}天`;
}

function judgeColor(judge) {
  if (!judge) return 'text.secondary';
  if (judge.startsWith('✗') || judge === '差') return '#c62828';
  if (judge.startsWith('⚠') || judge === '良') return '#ef6c00';
  if (judge.startsWith('✓') || judge === '优') return '#2e7d32';
  return 'text.secondary';
}

function KpiCard({ label, value, sub, color = '#1a2740' }) {
  return (
    <Paper sx={{ p: 2, height: '100%' }}>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography variant="h5" sx={{ fontWeight: 700, color, mt: 0.5 }}>{value}</Typography>
      {sub && <Typography variant="caption" color="text.secondary">{sub}</Typography>}
    </Paper>
  );
}

function worstLoc(detail) {
  if (!detail || !detail.length || !detail[0].arr) return null;
  const w = detail[0];
  return `${w.hub} · 货件${w.trk}（${(w.arr || '').slice(0, 10)}到货，取数起${w.fetchDays === null ? '—' : Math.round(w.fetchDays)}天）`;
}

function AlertRow({ r }) {
  const [open, setOpen] = useState(false);
  const pc = PRIO_COLOR[r.priority];
  return (
    <>
      <TableRow hover sx={{ '& > *': { borderBottom: 'unset' } }}>
        <TableCell>
          <IconButton size="small" onClick={() => setOpen(!open)}>
            {open ? <KeyboardArrowUpIcon /> : <KeyboardArrowDownIcon />}
          </IconButton>
        </TableCell>
        <TableCell>
          <Chip size="small" label={r.priority} sx={{ bgcolor: pc.bgcolor, color: '#fff', fontWeight: 700 }} />
        </TableCell>
        <TableCell sx={{ fontWeight: 600 }}>{r.sku}</TableCell>
        <TableCell>{r.model || '—'}</TableCell>
        <TableCell>
          {r.pendingPart ? (
            <Tooltip title={`最近动态 ${r.lastDynamic || '—'}`}>
              <span>{r.pendingPartName || r.pendingPart}</span>
            </Tooltip>
          ) : '—'}
        </TableCell>
        <TableCell align="right"
          sx={{ color: (r.stuckDays || 0) > 7 ? '#c62828' : 'inherit', fontWeight: 600 }}>
          {r.stuckDays === null || r.stuckDays === undefined ? '—' : r.stuckDays}
        </TableCell>
        <TableCell>
          <Tooltip title={r.prodSeg.basis}>
            <span style={{ color: judgeColor(r.prodSeg.judge) }}>
              {r.prodSeg.days === null ? '—' : `${Math.round(r.prodSeg.days)}/${r.prodSeg.target}`} {r.prodSeg.judge}
            </span>
          </Tooltip>
        </TableCell>
        <TableCell>
          <span style={{ color: judgeColor(r.opsSeg.judge) }}>
            {r.opsSeg.days === null ? '—' : `${Math.round(r.opsSeg.days)}/${r.opsSeg.target}`} {r.opsSeg.judge}
          </span>
        </TableCell>
        <TableCell>
          <Tooltip title={worstLoc(r.air.detail) || '无空运货件'}>
            <span style={{ color: judgeColor(r.air.judge) }}>
              {fmtDays(r.air.days)} {r.air.judge}
            </span>
          </Tooltip>
        </TableCell>
        <TableCell>
          <Tooltip title={worstLoc(r.sea.detail) || '无海运货件'}>
            <span style={{ color: judgeColor(r.sea.judge) }}>
              {fmtDays(r.sea.days)} {r.sea.judge}
            </span>
          </Tooltip>
        </TableCell>
        <TableCell align="right"
          sx={{ color: r.proposal.stale ? '#c62828' : 'inherit' }}>
          {r.proposal.days === null ? '—' : Math.round(r.proposal.days)}
        </TableCell>
        <TableCell align="right"
          sx={{ color: r.countries < r.countryMin ? '#ef6c00' : '#2e7d32' }}>
          {r.countries}/{r.countryMin}
        </TableCell>
        <TableCell>
          <Typography variant="caption"
            sx={{ display: 'block', maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {r.nextSteps.join('；')}
          </Typography>
        </TableCell>
      </TableRow>
      <TableRow>
        <TableCell colSpan={13} sx={{ py: 0, bgcolor: '#fafafa' }}>
          <Collapse in={open} unmountOnExit>
            <Box sx={{ p: 2 }}>
              <Grid container spacing={2}>
                <Grid item xs={12} md={6}>
                  <Typography variant="subtitle2" gutterBottom>加急原因</Typography>
                  {r.reasons.map((x, i) => (
                    <Typography key={i} variant="body2" sx={{ color: '#c62828' }}>• {x}</Typography>
                  ))}
                </Grid>
                <Grid item xs={12} md={6}>
                  <Typography variant="subtitle2" gutterBottom>下一步</Typography>
                  {r.nextSteps.map((x, i) => (
                    <Typography key={i} variant="body2">• {x}</Typography>
                  ))}
                  {r.missingHubs.length > 0 && (
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                      未到货渠道：{r.missingHubs.join('、')}
                    </Typography>
                  )}
                </Grid>
                <Grid item xs={12}>
                  <Typography variant="subtitle2" gutterBottom>
                    运输明细（逐货件 · 按到货时间倒序，红色=超底线，即空运/海运耗时的实际来源）
                  </Typography>
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>货件号</TableCell>
                        <TableCell>到货渠道（国家）</TableCell>
                        <TableCell>运输方式</TableCell>
                        <TableCell>发货日期</TableCell>
                        <TableCell>到货日期</TableCell>
                        <TableCell align="right">发货→到货(天)</TableCell>
                        <TableCell align="right">取数→到货(天)</TableCell>
                        <TableCell>判定</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {[...(r.air.detail || []), ...(r.sea.detail || [])]
                        .sort((a, b) => (b.arr || '').localeCompare(a.arr || ''))
                        .map((s, i) => (
                        <TableRow key={`${s.trk}-${i}`}
                          sx={{ bgcolor: s.judge === '差' ? '#fdecea' : 'inherit' }}>
                          <TableCell sx={{ fontFamily: 'monospace' }}>{s.trk}</TableCell>
                          <TableCell>{s.hub || '—'}</TableCell>
                          <TableCell>{s.mode}</TableCell>
                          <TableCell>{(s.ship || '').slice(0, 10) || '—'}</TableCell>
                          <TableCell>{(s.arr || '').slice(0, 10) || '—'}</TableCell>
                          <TableCell align="right">{s.transit === null ? '—' : Math.round(s.transit)}</TableCell>
                          <TableCell align="right"
                            sx={{ fontWeight: s.judge === '差' ? 700 : 400 }}>
                            {s.fetchDays === null ? '—' : Math.round(s.fetchDays)}
                          </TableCell>
                          <TableCell>
                            <span style={{
                              color: s.judge === '差' ? '#c62828'
                                : s.judge === '良' ? '#ef6c00'
                                : s.judge === '优' ? '#2e7d32' : '#ef6c00',
                              fontWeight: s.judge === '差' ? 700 : 400,
                            }}>
                              {s.judge === '未到货' || s.judge === '未发货'
                                ? `⏳ ${s.judge}` : s.judge}
                            </span>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  {!(r.air.detail || []).length && !(r.sea.detail || []).length && (
                    <Typography variant="caption" color="text.secondary">无货件运输记录</Typography>
                  )}
                </Grid>
              </Grid>
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}

export default function Alerts() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');
  const [prio, setPrio] = useState('');

  useEffect(() => {
    fetchAlerts().then(setData).catch((e) => setError(e.message));
  }, []);

  const rows = useMemo(() => {
    if (!data) return [];
    return data.rows.filter((r) => {
      if (prio && r.priority !== prio) return false;
      if (q && !(r.sku.toLowerCase().includes(q.toLowerCase())
        || (r.model || '').toLowerCase().includes(q.toLowerCase()))) return false;
      return true;
    });
  }, [data, q, prio]);

  if (error) return <Alert severity="error">加急预警加载失败：{error}</Alert>;
  if (!data) return <Box sx={{ display: 'flex', justifyContent: 'center', mt: 8 }}><CircularProgress /></Box>;

  return (
    <Box>
      <Typography variant="h6" gutterBottom>
        加急预警与盯数清单
        <Typography variant="caption" color="text.secondary" sx={{ ml: 2 }}>
          口径：route_check_sys 系统口径 · 只列未闭环 SKU，停滞越久越靠前 · 计算于 {data.computedAt}
        </Typography>
      </Typography>

      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid item xs={6} md={2.4}>
          <KpiCard label="覆盖 SKU" value={data.totalSkus} sub="全部批次去重" />
        </Grid>
        <Grid item xs={6} md={2.4}>
          <KpiCard label="未闭环（预警中）" value={data.alertCount}
            sub="已闭环 SKU 不列入" color="#ef6c00" />
        </Grid>
        <Grid item xs={4} md={2.4}>
          <KpiCard label="P0 立即处理" value={data.p0}
            sub="空/海超底线 · 提案超期 · Part7停滞>7天" color="#c62828" />
        </Grid>
        <Grid item xs={4} md={2.4}>
          <KpiCard label="P1 重点跟进" value={data.p1}
            sub="产品段超时 · 停滞>7天 · 渠道缺口" color="#ef6c00" />
        </Grid>
        <Grid item xs={4} md={2.4}>
          <KpiCard label="数据新鲜度" value={data.dataLagDays === null ? '—' : `滞后 ${data.dataLagDays} 天`}
            sub={`全库最新动态 ${data.latestDynamic || '—'}`}
            color={data.dataLagDays >= 3 ? '#c62828' : '#2e7d32'} />
        </Grid>
      </Grid>

      <Paper sx={{ mb: 2 }}>
        <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', p: 2, flexWrap: 'wrap' }}>
          <Typography variant="subtitle2">加急明细（P0 → P2，点击行首箭头展开详情）</Typography>
          <Box sx={{ flexGrow: 1 }} />
          <TextField size="small" label="搜索 SKU / 型号" value={q}
            onChange={(e) => setQ(e.target.value)} sx={{ width: 220 }} />
          <TextField size="small" select label="优先级" value={prio}
            onChange={(e) => setPrio(e.target.value)} sx={{ width: 160 }}>
            <MenuItem value="">全部</MenuItem>
            <MenuItem value="P0">P0 立即处理</MenuItem>
            <MenuItem value="P1">P1 重点跟进</MenuItem>
            <MenuItem value="P2">P2 推进中</MenuItem>
          </TextField>
          <Button variant="outlined" size="small" startIcon={<DownloadIcon />}
            href={buildAlertsExportUrl(prio)}>
            导出 xlsx
          </Button>
        </Box>
        <TableContainer sx={{ maxHeight: 560 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell width={50} />
                <TableCell>优先级</TableCell>
                <TableCell>SKU</TableCell>
                <TableCell>型号</TableCell>
                <TableCell>卡点环节</TableCell>
                <TableCell align="right">停滞(天)</TableCell>
                <TableCell>产品段</TableCell>
                <TableCell>运营段</TableCell>
                <TableCell>空运</TableCell>
                <TableCell>海运</TableCell>
                <TableCell align="right">提案(天)</TableCell>
                <TableCell align="right">到货国</TableCell>
                <TableCell>下一步</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((r) => <AlertRow key={r.sku} r={r} />)}
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={13} align="center">
                    <Typography variant="body2" color="text.secondary" sx={{ py: 3 }}>
                      当前筛选条件下没有预警 SKU 🎉
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>

      <Paper>
        <Box sx={{ p: 2 }}>
          <Typography variant="subtitle2" gutterBottom>
            盯数清单（下次去钉钉导数时，优先重导以下卡点单据）
          </Typography>
          <Grid container spacing={1}>
            {data.watchList.map((w) => (
              <Grid item xs={12} sm={6} md={4} key={w.part}>
                <Paper variant="outlined" sx={{ p: 1.5 }}>
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {w.part} · {w.partName}
                    </Typography>
                    <Chip size="small" label={`${w.skuCount} 个`} />
                  </Box>
                  <Typography variant="caption" color="text.secondary"
                    sx={{ wordBreak: 'break-all' }}>
                    {w.skus.slice(0, 12).join('、')}{w.skus.length > 12 ? ` …共${w.skus.length}个` : ''}
                  </Typography>
                </Paper>
              </Grid>
            ))}
          </Grid>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5 }}>
            用法：按上面环节去钉钉重导对应审批单 → 重跑 ETL（或等数据文件更新）→ 本看板自动刷新预警状态
          </Typography>
        </Box>
      </Paper>
    </Box>
  );
}
