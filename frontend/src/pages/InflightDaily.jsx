import React, { useState } from 'react';
import {
  Box, Button, Chip, Dialog, DialogContent, DialogTitle, Paper, Table,
  TableBody, TableCell, TableContainer, TableHead, TableRow, Typography,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import PageHeader from '../components/PageHeader.jsx';
import KpiCard from '../components/KpiCard.jsx';
import SectionCard from '../components/SectionCard.jsx';
import { ErrorState, LoadingState } from '../components/StateViews.jsx';
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
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mb: 2 }}>
              {det.model && <Chip variant="outlined" size="small" label={det.model} />}
              {det.status && <StateChip state={det.status} />}
              {det.multiRound?.flag
                ? <Chip size="small" color="warning" variant="outlined"
                    label={`多轮提交：${det.multiRound.note || det.multiRound.count || ''}`} />
                : null}
            </Box>
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
                      <TableCell>{s.elapsed ?? '—'}</TableCell>
                      <TableCell>{s.target ?? '—'}</TableCell>
                      <TableCell>{s.warnLine ?? '—'}</TableCell>
                      <TableCell>{s.owner || s.executor || '—'}</TableCell>
                    </TableRow>
                  ))}
                  {!segs.length && (
                    <TableRow>
                      <TableCell colSpan={7}>暂无段级数据</TableCell>
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

  const items = data?.items || [];
  const kpi = data || {};
  const todayNew = alerts.data?.todayNewOverdue || [];
  const ongoing = alerts.data?.ongoingOverdue || [];
  const warns = alerts.data?.warnings || [];
  const repItems = reports.data?.items || [];
  const latest = repItems[0];

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
            gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(4, 1fr)' },
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

          <SectionCard title={`在研清单（${items.length}）`} sx={{ mb: 2 }}>
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
                    <TableCell>多轮</TableCell>
                    <TableCell>负责人</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {items.map((it) => (
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
                      <TableCell>{it.multiRound ? '⚠ 多轮' : '—'}</TableCell>
                      <TableCell>{it.owner || '—'}</TableCell>
                    </TableRow>
                  ))}
                  {!items.length && (
                    <TableRow>
                      <TableCell colSpan={9}>当前没有在研 SKU</TableCell>
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
