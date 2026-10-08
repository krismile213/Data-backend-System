import React, { useMemo, useState } from 'react';
import {
  Box, Button, Chip, Dialog, DialogContent, DialogTitle, MenuItem, Paper, Table,
  TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Tooltip, Typography,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import PageHeader from '../components/PageHeader.jsx';
import KpiCard from '../components/KpiCard.jsx';
import SectionCard from '../components/SectionCard.jsx';
import { ErrorState, LoadingState } from '../components/StateViews.jsx';
import TransportClosure, {
  ClosureBadge, PendingCountries,
} from '../components/TransportClosure.jsx';
import useApi from '../hooks/useApi.js';
import {
  fetchInflightOverview, fetchInflightSku, fetchInflightAlerts,
  fetchDailyReports, runDailyReport, buildDailyReportUrl,
} from '../api.js';
import { SEMANTIC } from '../theme.js';

const STATUS_COLOR = {
  '超期': SEMANTIC.danger,
  '预警': SEMANTIC.warning,
  '进行中': SEMANTIC.info,
  '未开始': SEMANTIC.muted,
  '已完成': SEMANTIC.success,
  '留空': SEMANTIC.neutral,
};

/** 仍在跑的段状态（终点未填 → 展示「至今」） */
const RUNNING_STATES = ['进行中', '预警', '超期'];

/**
 * 运输域段判定 —— 与后端 `app/transport_closure.is_transport_segment` 同口径：
 *   dept ∈ ('运输','4国') 或 seg == l4Name(采购→发货末批) 或 seg 以「·未发货」结尾。
 * l4Name 由后端 `transportDef.l4Name` 下发（不硬编码段名）。
 */
function makeIsTransportSeg(l4Name) {
  return (dept, seg) => {
    const d = dept || '';
    const s = seg || '';
    return d === '运输' || d === '4国'
      || (!!l4Name && s === l4Name) || s.endsWith('·未发货');
  };
}

function StateChip({ state }) {
  return (
    <Chip size="small" label={state}
      variant={state === '超期' || state === '预警' ? 'filled' : 'outlined'}
      sx={state === '超期' || state === '预警'
        ? { bgcolor: STATUS_COLOR[state] || SEMANTIC.text, color: '#fff' }
        : { color: STATUS_COLOR[state] || SEMANTIC.text,
            borderColor: STATUS_COLOR[state] || SEMANTIC.text }} />
  );
}

/** 段起点展示：有值原样，缺值「—」。 */
function segStartText(s) {
  return s.start || '—';
}

/**
 * 段终点展示：
 *   · 有值 → 原样（含 noShip 的「至今未发货」字面量，不加工）；
 *   · 空值且该段在跑（进行中/预警/超期）→ 「至今」；
 *   · 空值且未开始 / 留空(gap) → 「—」。
 */
function segEndText(s) {
  if (s.end) return s.end;
  return RUNNING_STATES.includes(s.state) ? '至今' : '—';
}

function SkuDialog({ sku, onClose }) {
  const { data: det, loading, error } = useApi(
    () => fetchInflightSku(sku), [sku], { enabled: !!sku });

  const segs = det?.segments || det?.segs || [];
  const cause = det?.rootCause;

  return (
    <Dialog open={!!sku} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>事中档案 · {sku}</DialogTitle>
      <DialogContent dividers>
        {loading && <LoadingState label="加载事中档案…" />}
        {error && <ErrorState error={error} />}
        {det && !error && (
          <>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mb: 2, alignItems: 'center' }}>
              {det.model && <Chip variant="outlined" size="small" label={det.model} />}
              {det.status && <StateChip state={det.status} />}
              {det.shipClosure && (
                <TransportClosure closure={det.shipClosure}
                  pendingCountries={det.pendingCountries} />
              )}
              {det.transportExempt && (
                <Chip size="small" variant="outlined"
                  sx={{ color: SEMANTIC.success, borderColor: SEMANTIC.success }}
                  label="运输已闭环 · 运输域免追踪" />
              )}
              {det.multiRound?.flag
                ? <Chip size="small" color="warning" variant="outlined"
                    label={`多轮提交：${det.multiRound.note || det.multiRound.count || ''}`} />
                : null}
            </Box>
            {det.transportReason && (
              <Typography variant="caption" color="text.secondary"
                sx={{ display: 'block', mb: 1.5 }}>
                运输口径：{det.transportReason}
              </Typography>
            )}
            {cause && (
              <SectionCard title="核心原因（因果链）" sx={{ mb: 2 }}>
                <Typography variant="body2">
                  {[cause.dept, cause.seg, cause.note || cause.reason]
                    .filter(Boolean).join(' · ') || JSON.stringify(cause)}
                </Typography>
              </SectionCard>
            )}
            <SectionCard title="段级时间轴">
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>部门</TableCell>
                    <TableCell>环节</TableCell>
                    <TableCell>状态</TableCell>
                    <TableCell>开始</TableCell>
                    <TableCell>结束</TableCell>
                    <TableCell>已进行</TableCell>
                    <TableCell>目标</TableCell>
                    <TableCell>预警线</TableCell>
                    <TableCell>负责人</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {segs.map((s, i) => (
                    <TableRow key={i}>
                      <TableCell>{s.dept || '—'}</TableCell>
                      <TableCell>{s.seg || '—'}</TableCell>
                      <TableCell><StateChip state={s.state} /></TableCell>
                      <TableCell>{segStartText(s)}</TableCell>
                      <TableCell>{segEndText(s)}</TableCell>
                      <TableCell>{s.elapsed ?? '—'}</TableCell>
                      <TableCell>{s.target ?? '—'}</TableCell>
                      <TableCell>{s.warnLine ?? '—'}</TableCell>
                      <TableCell>{s.owner || s.executor || '—'}</TableCell>
                    </TableRow>
                  ))}
                  {!segs.length && (
                    <TableRow>
                      <TableCell colSpan={9}>暂无段级数据</TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </SectionCard>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default function InflightDaily() {
  const { data, error, loading, refetch } = useApi(() => fetchInflightOverview(), []);
  const alerts = useApi(() => fetchInflightAlerts(), []);
  const reports = useApi(() => fetchDailyReports(), []);
  const [selSku, setSelSku] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  // 在研清单「本地筛选」状态（仅影响本表，不与上方 KPI 联动）
  const [fDept, setFDept] = useState('');
  const [fSeg, setFSeg] = useState('');
  const [fStatus, setFStatus] = useState('');
  const [fQ, setFQ] = useState('');
  const [onlyTransport, setOnlyTransport] = useState(false);

  const items = data?.items || [];
  const kpi = data || {};
  const l4Name = data?.transportDef?.l4Name;
  const transportExempted = data?.transportExempted || [];
  const todayNew = alerts.data?.todayNewOverdue || [];
  const ongoing = alerts.data?.ongoingOverdue || [];
  const warns = alerts.data?.warnings || [];
  const repItems = reports.data?.items || [];
  const latest = repItems[0];

  // 筛选选项从当前清单动态汇总（不硬编码部门/环节名）
  const deptOpts = useMemo(
    () => [...new Set(items.map((it) => it.staySegDept).filter(Boolean))].sort(), [items]);
  const segOpts = useMemo(
    () => [...new Set(items.map((it) => it.staySeg).filter(Boolean))].sort(), [items]);
  const statusOpts = useMemo(
    () => [...new Set(items.map((it) => it.status).filter(Boolean))].sort(), [items]);

  const filtered = useMemo(() => {
    const isTransportSeg = makeIsTransportSeg(l4Name);
    const q = fQ.trim().toLowerCase();
    return items.filter((it) => {
      if (fDept && it.staySegDept !== fDept) return false;
      if (fSeg && it.staySeg !== fSeg) return false;
      if (fStatus && it.status !== fStatus) return false;
      if (onlyTransport && !isTransportSeg(it.staySegDept, it.staySeg)) return false;
      if (q && !(String(it.sku || '').toLowerCase().includes(q)
        || String(it.model || '').toLowerCase().includes(q))) return false;
      return true;
    });
  }, [items, fDept, fSeg, fStatus, fQ, onlyTransport, l4Name]);

  const hasFilter = Boolean(fDept || fSeg || fStatus || fQ.trim() || onlyTransport);
  const clearFilters = () => {
    setFDept(''); setFSeg(''); setFStatus(''); setFQ(''); setOnlyTransport(false);
  };

  const makeReport = async () => {
    setBusy(true);
    setMsg('');
    try {
      const r = await runDailyReport();
      setMsg(`已生成 ${r?.date || '今日'} 日报（${r?.sheets ?? 5} sheets）`);
      await reports.refetch();
    } catch (e) {
      setMsg(`生成失败：${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        title="事中日报"
        subtitle="在研 SKU 的段级时效 / 风险预警 / 当日上报（数据 = 审批记录实时口径）"
        dense
        actions={<>
          {msg && <Chip size="small" label={msg} sx={{ mr: 1 }} />}
          <Button size="small" variant="contained" startIcon={<PlayArrowIcon />}
            onClick={makeReport} disabled={busy}>
            生成今日日报
          </Button>
          {latest && (
            <Button size="small" variant="outlined" startIcon={<DownloadIcon />}
              href={buildDailyReportUrl(latest.date)}>
              下载 {latest.date}
            </Button>
          )}
        </>}
      />

      {loading && <LoadingState label="计算在研清单…" />}
      {error && <ErrorState error={error} />}

      {data && !error && (
        <>
          <Box sx={{ display: 'grid',
            gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(5, 1fr)' },
            gap: 2, mb: 2 }}>
            <KpiCard label="在研 SKU" value={kpi.inResearchCount ?? 0}
              accent={SEMANTIC.brand} />
            <KpiCard label="超期 SKU" value={kpi.overdueSkuCount ?? 0}
              color={SEMANTIC.danger} accent={SEMANTIC.danger}
              sub="任一段超过目标天" />
            <KpiCard label="临近预警" value={kpi.warnCount ?? 0}
              color={SEMANTIC.warning} accent={SEMANTIC.warning}
              sub="已用 ≥85% 目标天" />
            <KpiCard label="多轮提交" value={kpi.multiRoundCount ?? 0}
              color={SEMANTIC.caution} accent={SEMANTIC.caution}
              sub="同一单据被撤回重提" />
            <KpiCard label="运输已闭环·免追踪" value={kpi.transportExemptCount ?? 0}
              color={SEMANTIC.success} accent={SEMANTIC.success}
              sub="运输域是唯一卡点，不催办" />
          </Box>

          <SectionCard title="风险预警区" sx={{ mb: 2 }}
            subtitle="当日新超期 → 需要今天上报；持续超期 → 跟进；预警 → 提前干预">
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
              gap: 2 }}>
              {[
                { title: `当日新超期（${todayNew.length}）`, rows: todayNew },
                { title: `持续超期（${ongoing.length}）`, rows: ongoing },
              ].map(({ title, rows }) => (
                <TableContainer key={title} component={Paper} variant="outlined">
                  <Typography variant="subtitle2" sx={{ p: 1 }}>{title}</Typography>
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>SKU</TableCell>
                        <TableCell>部门</TableCell>
                        <TableCell>环节</TableCell>
                        <TableCell>超期</TableCell>
                        <TableCell>负责人</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {rows.map((r, i) => (
                        <TableRow key={i} hover sx={{ cursor: 'pointer' }}
                          onClick={() => setSelSku(r.sku)}>
                          <TableCell>{r.sku}</TableCell>
                          <TableCell>{r.dept}</TableCell>
                          <TableCell>{r.seg}</TableCell>
                          <TableCell sx={{ color: SEMANTIC.danger }}>
                            {r.overDays ?? '—'} 天
                          </TableCell>
                          <TableCell>{r.owner || '—'}</TableCell>
                        </TableRow>
                      ))}
                      {!rows.length && (
                        <TableRow><TableCell colSpan={5}>无</TableCell></TableRow>
                      )}
                    </TableBody>
                  </Table>
                </TableContainer>
              ))}
            </Box>
            <TableContainer component={Paper} variant="outlined" sx={{ mt: 2 }}>
              <Typography variant="subtitle2" sx={{ p: 1 }}>
                临近超期预警（{warns.length}）· 达到目标天的 85% 即亮灯
              </Typography>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>SKU</TableCell>
                    <TableCell>部门</TableCell>
                    <TableCell>环节</TableCell>
                    <TableCell>已进行</TableCell>
                    <TableCell>目标</TableCell>
                    <TableCell>负责人</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {warns.map((w, i) => (
                    <TableRow key={i} hover sx={{ cursor: 'pointer' }}
                      onClick={() => setSelSku(w.sku)}>
                      <TableCell>{w.sku}</TableCell>
                      <TableCell>{w.dept}</TableCell>
                      <TableCell>{w.seg}</TableCell>
                      <TableCell>{w.elapsed ?? '—'}</TableCell>
                      <TableCell>{w.target ?? '—'}</TableCell>
                      <TableCell>{w.owner || '—'}</TableCell>
                    </TableRow>
                  ))}
                  {!warns.length && (
                    <TableRow><TableCell colSpan={6}>无</TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
            </TableContainer>
          </SectionCard>

          <SectionCard title={`在研清单（${filtered.length}/${items.length}）`} sx={{ mb: 2 }}>
            <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'center',
              flexWrap: 'wrap', mb: 0.5 }}>
              <TextField size="small" select label="部门" value={fDept}
                onChange={(e) => setFDept(e.target.value)} sx={{ width: 130 }}>
                <MenuItem value="">全部</MenuItem>
                {deptOpts.map((d) => <MenuItem key={d} value={d}>{d}</MenuItem>)}
              </TextField>
              <TextField size="small" select label="环节" value={fSeg}
                onChange={(e) => setFSeg(e.target.value)} sx={{ width: 200 }}>
                <MenuItem value="">全部</MenuItem>
                {segOpts.map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
              </TextField>
              <TextField size="small" select label="状态" value={fStatus}
                onChange={(e) => setFStatus(e.target.value)} sx={{ width: 130 }}>
                <MenuItem value="">全部</MenuItem>
                {statusOpts.map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
              </TextField>
              <TextField size="small" label="搜索 SKU / 机型" value={fQ}
                onChange={(e) => setFQ(e.target.value)} sx={{ width: 200 }} />
              <Tooltip title="仅显示当前停留环节属于运输域（运输 / 4国 / 采购→发货末批 / 未发货异常）的 SKU">
                <Chip label="只看运输" size="small"
                  color={onlyTransport ? 'primary' : 'default'}
                  variant={onlyTransport ? 'filled' : 'outlined'}
                  onClick={() => setOnlyTransport((v) => !v)} />
              </Tooltip>
              {hasFilter && (
                <Button size="small" onClick={clearFilters}>清空筛选</Button>
              )}
            </Box>
            <Typography variant="caption" color="text.secondary"
              sx={{ display: 'block', mb: 1 }}>
              筛选仅影响本表，上方 KPI 为全量。
            </Typography>
            <TableContainer component={Paper} variant="outlined">
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>SKU</TableCell>
                    <TableCell>机型</TableCell>
                    <TableCell>当前停留环节</TableCell>
                    <TableCell>部门</TableCell>
                    <TableCell>距超时</TableCell>
                    <TableCell>预警段</TableCell>
                    <TableCell>状态</TableCell>
                    <TableCell>运输</TableCell>
                    <TableCell>多轮</TableCell>
                    <TableCell>负责人</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {filtered.map((it) => (
                    <TableRow key={it.sku} hover sx={{ cursor: 'pointer' }}
                      onClick={() => setSelSku(it.sku)}>
                      <TableCell>{it.sku}</TableCell>
                      <TableCell>{it.model || '—'}</TableCell>
                      <TableCell>{it.staySeg || '—'}</TableCell>
                      <TableCell>{it.staySegDept || '—'}</TableCell>
                      <TableCell sx={it.stayDaysLeft != null && it.stayDaysLeft < 0
                        ? { color: SEMANTIC.danger, fontWeight: 600 } : undefined}>
                        {it.stayDaysLeft == null
                          ? '—'
                          : (it.stayDaysLeft < 0
                            ? `已超${Math.round(-it.stayDaysLeft)}天`
                            : `剩${Math.round(it.stayDaysLeft)}天`)}
                      </TableCell>
                      <TableCell>{(it.warnSegs || []).join('、') || '—'}</TableCell>
                      <TableCell><StateChip state={it.status} /></TableCell>
                      <TableCell>
                        {(it.pendingCountries || []).length
                          ? <PendingCountries hubs={it.pendingCountries} />
                          : (it.transportExempt
                            ? <ClosureBadge closure="closed" />
                            : '—')}
                      </TableCell>
                      <TableCell>{it.multiRound ? '⚠ 多轮' : '—'}</TableCell>
                      <TableCell>{it.owner || '—'}</TableCell>
                    </TableRow>
                  ))}
                  {!filtered.length && (
                    <TableRow>
                      <TableCell colSpan={10}>
                        {items.length
                          ? '当前筛选无匹配 SKU（可从上方清空条件）'
                          : '当前没有在研 SKU'}
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </TableContainer>
          </SectionCard>

          <SectionCard title={`运输已闭环 · 免追踪（${transportExempted.length}）`} sx={{ mb: 2 }}
            subtitle={data.transportDef?.note
              || '运输已闭环（已发货货件全部到货 / 无可发货货件）时，运输域不再是卡点'}>
            <Typography variant="caption" color="text.secondary"
              sx={{ display: 'block', mb: 1 }}>
              判据：运输域是<strong>唯一卡点</strong>（已发货货件全部到货或无可发货货件，且无其它在研段）
              —— 物流到货不是可催办动作，故移出在研清单，单列备查。
              真源 = {data.transportDef?.source || 'app/transport_closure.classify_transport'}。
            </Typography>
            <TableContainer component={Paper} variant="outlined">
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>SKU</TableCell>
                    <TableCell>机型</TableCell>
                    <TableCell>运输闭环</TableCell>
                    <TableCell align="right">已发货</TableCell>
                    <TableCell align="right">已到货</TableCell>
                    <TableCell>未发货渠道</TableCell>
                    <TableCell>结论</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {transportExempted.map((r) => (
                    <TableRow key={r.sku} hover sx={{ cursor: 'pointer' }}
                      onClick={() => setSelSku(r.sku)}>
                      <TableCell sx={{ fontWeight: 600 }}>{r.sku}</TableCell>
                      <TableCell>{r.model || '—'}</TableCell>
                      <TableCell><ClosureBadge closure={r.closure} /></TableCell>
                      <TableCell align="right">{r.shippedCount ?? '—'}</TableCell>
                      <TableCell align="right">{r.arrivedCount ?? '—'}</TableCell>
                      <TableCell>
                        {(r.notShippedHubs || []).length
                          ? r.notShippedHubs.join('、')
                          : '—'}
                      </TableCell>
                      <TableCell>
                        <Typography variant="caption" color="text.secondary">
                          {r.reason || '—'}
                        </Typography>
                      </TableCell>
                    </TableRow>
                  ))}
                  {!transportExempted.length && (
                    <TableRow>
                      <TableCell colSpan={7}>无</TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </TableContainer>
          </SectionCard>

          <SectionCard title={`已生成日报（${repItems.length}）`}
            subtitle="按保留期自动清理过期文件">
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>日期</TableCell>
                  <TableCell>在研</TableCell>
                  <TableCell>超期</TableCell>
                  <TableCell>预警</TableCell>
                  <TableCell>下载</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {repItems.map((r) => (
                  <TableRow key={`${r.category}-${r.date}`}>
                    <TableCell>{r.date}</TableCell>
                    <TableCell>{r.skuCount ?? '—'}</TableCell>
                    <TableCell>{r.overdueCount ?? '—'}</TableCell>
                    <TableCell>{r.warnCount ?? '—'}</TableCell>
                    <TableCell>
                      <Button size="small" startIcon={<DownloadIcon />}
                        href={buildDailyReportUrl(r.date)}>
                        xlsx
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
                {!repItems.length && (
                  <TableRow>
                    <TableCell colSpan={5}>
                      还没有日报 —— 点右上角「生成今日日报」
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </SectionCard>
        </>
      )}

      <SkuDialog sku={selSku} onClose={() => setSelSku(null)} />
    </>
  );
}
