import React, { useMemo, useState } from 'react';
import {
  Box, Button, Chip, Dialog, DialogContent, DialogTitle, MenuItem, Paper, Tab, Table,
  TableBody, TableCell, TableContainer, TableHead, TableRow, Tabs, TextField, Tooltip, Typography,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import PageHeader from '../components/PageHeader.jsx';
import KpiCard from '../components/KpiCard.jsx';
import SectionCard from '../components/SectionCard.jsx';
import RegistryImport from '../components/RegistryImport.jsx';
import { ErrorState, LoadingState } from '../components/StateViews.jsx';
import TransportClosure, { ClosureBadge } from '../components/TransportClosure.jsx';
import useApi from '../hooks/useApi.js';
import {
  fetchInflightOverview, fetchInflightSku, fetchInflightAlerts,
  fetchDailyReports, runDailyReport, buildDailyReportUrl, fetchRegistryStatus,
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

/**
 * 久悬线（天）：当前停留段超期 ≥ 此值 → 「催一下」已失效，转决策（重启评估/豁免/关闭）。
 * 与后端 reporting.py 的 OVER_STALE 同值（2026-11-06 方案4）；30 天是升级提醒，90 天是该拍板。
 */
const STALE_DAYS = 90;

/**
 * 久悬单建议动作 —— 与后端 reporting.stale_action 同文案（两处展示同一句话）。
 * 只出建议文案，不改任何状态：关单/豁免仍走人工登记通道。
 */
function staleAction(it) {
  const dept = it.staySegDept || '';
  const seg = it.staySeg || '';
  if (dept === '运输' || dept === '4国' || seg.includes('运输') || seg.endsWith('·未发货')) {
    return '核对货件：丢件 / 已到货未回传';
  }
  if (it.stayTarget == null) return '无目标段久悬：确认是否已终止、该不该登记豁免';
  return '重启评估 / 登记豁免 / 关闭';
}

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

/**
 * 在研清单「运输」列单元格（结构化展示，优先级）：
 *   ① 未到货国（pendingCountries）→ 未闭环徽标 + 黄色国家胶囊（催物流第一优先）
 *   ② 未发货渠道（notShippedHubs）→ warning 文字
 *   ③ shipClosure='closed' → 「已发N·到M」+ 绿色闭环徽标
 *   ④ 其它已发货形态（个别 pending 无国家）→ 「已发N·到M·在途K」文字
 *   ⑤ 未涉及运输（closure='none' 且无未发货渠道）→ 「—」
 * ⚠️ 判定一律以 **shipClosure** 为准；**绝不可**用 transportExempt —— 它只表示
 *    「运输域不参与卡点判定」，对「一件都没发」的在研 SKU 同样为 True，
 *    据它显示会把未开发/未发货的 SKU 误标成「运输已闭环」。
 * 颜色全部走 theme.js 语义色板（禁硬编码）。
 */
function TransportCell({ it }) {
  const pend = it.pendingCountries || [];
  const unshipped = it.notShippedHubs || [];
  const sc = it.shipCounts || {};
  const closure = it.shipClosure;
  if (pend.length) {
    // 同上：列宽固定 12% 后，「运输未闭环」徽标 + 「未到货:」+ 国家胶囊会竖着堆
    // 三行把行高撑爆。计数徽标 + Tooltip 名单，一行解决。
    return (
      <Tooltip arrow enterDelay={150}
        title={
          <Box sx={{ lineHeight: 1.7, maxWidth: 240 }}>
            {pend.map((h) => <Box key={h}>{h}</Box>)}
          </Box>
        }>
        <Chip size="small" label={`未到货 ${pend.length} 国`}
          sx={{ bgcolor: SEMANTIC.warning, color: SEMANTIC.onColor, height: 22 }} />
      </Tooltip>
    );
  }
  if (unshipped.length) {
    // 一个 SKU 常有 15 个渠道同时「未发货」（货都还没采购到，必然状态），
    // 全名铺进单元格会把行高撑到 3~4 行、同样内容重复 6 行（2026-11-06 实测）。
    // 这里只给计数徽标，渠道名单放 Tooltip 里按需展开。
    return (
      <Tooltip arrow enterDelay={150}
        title={
          <Box sx={{ lineHeight: 1.7, maxWidth: 240 }}>
            {unshipped.map((h) => <Box key={h}>{h}</Box>)}
          </Box>
        }>
        <Chip size="small" variant="outlined"
          label={`未发货 ${unshipped.length} 渠道`}
          sx={{ color: SEMANTIC.warning, borderColor: SEMANTIC.warning, height: 22 }} />
      </Tooltip>
    );
  }
  if (closure === 'closed') {
    return (
      <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
        <Box component="span" sx={{ fontSize: 12, color: SEMANTIC.neutral }}>
          已发{sc.shipped}·到{sc.arrived}
        </Box>
        <ClosureBadge closure="closed" />
      </Box>
    );
  }
  if (sc.shipped) {
    return (
      <Box component="span" sx={{ fontSize: 12, color: SEMANTIC.neutral }}>
        已发{sc.shipped}·到{sc.arrived}{sc.pending ? `·在途${sc.pending}` : ''}
      </Box>
    );
  }
  // closure === 'none'：尚未涉及运输 —— 不得据 transportExempt 误标「已闭环」
  return '—';
}

function SkuDialog({ sku, onClose }) {
  const { data: det, loading, error } = useApi(
    () => fetchInflightSku(sku), [sku], { enabled: !!sku });

  const segs = det?.segments || det?.segs || [];
  const cause = det?.rootCause;

  return (
    <Dialog open={!!sku} onClose={onClose} maxWidth="xl" fullWidth>
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
              {det.transportExempt && det.shipClosure === 'closed' && (
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
              {/* 双轨时间列（2026-11-06，与复盘页段级表同口径「照搬」）：
                  实际·起/终 = 纯系统业务日期，audit·起/终 = 纯审批上报，两套互不冒充；
                  耗时（已进行）仍走判定轨，fallback 时数字旁灰字「补」。
                  甘特图 / 日报 xlsx 不动（用户口径：保持甘特图不变）。 */}
              <Box sx={{ overflowX: 'auto' }}>
              <Table size="small" sx={{ minWidth: 1120, tableLayout: 'fixed' }}>
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ whiteSpace: 'nowrap', width: 44 }}>部门</TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap', width: 132 }}>环节</TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap', width: 76 }}>状态</TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap', width: 104 }}>实际·起点</TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap', width: 104 }}>实际·终点</TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap', width: 104 }}>audit·起点</TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap', width: 108 }}>audit·终点</TableCell>
                    <TableCell align="right" sx={{ whiteSpace: 'nowrap', width: 72 }}>已进行</TableCell>
                    <TableCell align="right" sx={{ whiteSpace: 'nowrap', width: 60 }}>目标</TableCell>
                    <TableCell align="right" sx={{ whiteSpace: 'nowrap', width: 60 }}>预警线</TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap', width: 66 }}>负责人</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {segs.map((s, i) => (
                    <TableRow key={i} sx={s.gap ? { bgcolor: 'action.hover' } : undefined}>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>{s.gap ? '留空' : (s.dept || '—')}</TableCell>
                      <TableCell sx={{ fontWeight: 600, wordBreak: 'keep-all' }}>{s.seg || '—'}</TableCell>
                      <TableCell><StateChip state={s.state} /></TableCell>
                      {/* 纯轨四列：缺就写「缺」（斜体灰），不用另一轨的值冒充（复盘页同款） */}
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>
                        {s.startReal
                          || <Typography component="span" variant="caption"
                            sx={{ fontStyle: 'italic', color: 'text.disabled' }}>缺</Typography>}
                      </TableCell>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>
                        {s.endReal
                          || <Typography component="span" variant="caption"
                            sx={{ fontStyle: 'italic', color: 'text.disabled' }}>缺</Typography>}
                      </TableCell>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>
                        {s.auditFrom
                          || <Typography component="span" variant="caption"
                            sx={{ fontStyle: 'italic', color: 'text.disabled' }}>缺</Typography>}
                      </TableCell>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>
                        {s.auditTo
                          || <Typography component="span" variant="caption"
                            sx={{ fontStyle: 'italic', color: 'text.disabled' }}>缺</Typography>}
                      </TableCell>
                      <TableCell align="right">
                        {s.gap ? '—'
                          : <>{s.elapsed ?? '—'}
                            {s.fallback && s.elapsed != null && (
                              <Typography component="span" variant="caption"
                                sx={{ color: 'text.disabled', ml: 0.5 }}
                                title="判定轨起点/终点有补位（兜底列或 audit），时间戳列看不到它">
                                补
                              </Typography>)}
                          </>}
                      </TableCell>
                      <TableCell align="right">{s.target ?? '—'}</TableCell>
                      <TableCell align="right">{s.warnLine ?? '—'}</TableCell>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>{s.owner || s.executor || '—'}</TableCell>
                    </TableRow>
                  ))}
                  {!segs.length && (
                    <TableRow>
                      <TableCell colSpan={11}>暂无段级数据</TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
              </Box>
              <Typography variant="caption" color="text.secondary"
                sx={{ display: 'block', mt: 0.5 }}>
                实际·起/终 = 纯系统业务日期（表单主字段；兜底列不算 → 写「缺」）；
                audit·起/终 = 纯审批上报时间。两套互不冒充。
                「已进行」走判定轨（系统&gt;audit&gt;动作），数字旁「补」= 判定轨里有补位。
              </Typography>
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
  // 登记表（SKU换号 / 人工豁免）状态：接口取不到时降级 null，不影响日报主流程
  const registry = useApi(() => fetchRegistryStatus().catch(() => null), []);
  const [tab, setTab] = useState('daily');
  const [selSku, setSelSku] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  // 在研清单「本地筛选」状态（仅影响本表，不与上方 KPI 联动）
  const [fDept, setFDept] = useState('');
  const [fSeg, setFSeg] = useState('');
  const [fStatus, setFStatus] = useState('');
  const [fOwner, setFOwner] = useState('');
  const [fTransport, setFTransport] = useState('');
  const [fQ, setFQ] = useState('');
  const [onlyTransport, setOnlyTransport] = useState(false);
  // 逐票表默认不展开「已到货」票（见 transitRows 注释）
  const [showArrived, setShowArrived] = useState(false);

  const items = data?.items || [];
  const kpi = data || {};
  const l4Name = data?.transportDef?.l4Name;
  const transportExempted = data?.transportExempted || [];
  const todayNew = alerts.data?.todayNewOverdue || [];
  const ongoing = alerts.data?.ongoingOverdue || [];
  const warns = alerts.data?.warnings || [];
  // 预警是**环节级**现象：一个 SKU 可能多段同时到 85%，且可以与"超期"并存
  // （当前停留段已超期、另一段刚到预警线）。所以 KPI 卡片必须与下方预警列表
  // 用同一个数组计数，绝不能再各自按 status 枚举算一遍（2026-11-05 口径统一）。
  const warnSkuCount = new Set(warns.map((w) => w.sku)).size;
  const repItems = reports.data?.items || [];
  const latest = repItems[0];

  // 运输在途·逐票（前端④）：把 transportInTransit 的 tickets + unshippedHubs 摊平成行。
  // 拆两组 —— 待办（未到货 / 未发货）与已到货。后端 transportInTransit 按 SKU 给，
  // 一个 SKU 只要还有 1 张票没到，整组 15 张票（含十几张早已到货）都会带出来：
  // 全铺开时 345 行里 173 行是「已到货」，标题写"用于催物流"却一半是已完成历史
  // （2026-11-06 实测）。已到货降级成一行计数，点开才铺。
  const { transitRows, arrivedRows } = useMemo(() => {
    const all = [];
    (data?.transportInTransit || []).forEach((e) => {
      (e.tickets || []).forEach((t) => {
        all.push({ sku: e.sku, model: e.model, hub: t.hub, trk: t.trk,
          ship: t.ship, arr: t.arr, state: t.state });
      });
      (e.unshippedHubs || []).forEach((h) => {
        all.push({ sku: e.sku, model: e.model, hub: h, trk: '',
          ship: '', arr: '', state: '未发货' });
      });
    });
    return {
      transitRows: all.filter((r) => r.state !== '已到货'),
      arrivedRows: all.filter((r) => r.state === '已到货'),
    };
  }, [data]);

  // 筛选选项从当前清单动态汇总（不硬编码部门/环节名）
  const deptOpts = useMemo(
    () => [...new Set(items.map((it) => it.staySegDept).filter(Boolean))].sort(), [items]);
  const segOpts = useMemo(
    () => [...new Set(items.map((it) => it.staySeg).filter(Boolean))].sort(), [items]);
  const statusOpts = useMemo(
    () => [...new Set(items.map((it) => it.status).filter(Boolean))].sort(), [items]);
  const ownerOpts = useMemo(
    () => [...new Set(items.map((it) => it.owner).filter(Boolean))].sort(), [items]);

  const filtered = useMemo(() => {
    const isTransportSeg = makeIsTransportSeg(l4Name);
    const q = fQ.trim().toLowerCase();
    return items.filter((it) => {
      if (fDept && it.staySegDept !== fDept) return false;
      if (fSeg && it.staySeg !== fSeg) return false;
      if (fStatus && it.status !== fStatus) return false;
      if (fOwner && it.owner !== fOwner) return false;
      if (fTransport === 'closed' && it.shipClosure !== 'closed') return false;
      if (fTransport === 'pending' && !(it.pendingCountries || []).length) return false;
      if (fTransport === 'unshipped' && !(it.notShippedHubs || []).length) return false;
      if (onlyTransport && !isTransportSeg(it.staySegDept, it.staySeg)) return false;
      if (q && !(String(it.sku || '').toLowerCase().includes(q)
        || String(it.model || '').toLowerCase().includes(q))) return false;
      return true;
    });
  }, [items, fDept, fSeg, fStatus, fTransport, fQ, onlyTransport, l4Name]);

  const hasFilter = Boolean(fDept || fSeg || fStatus || fOwner || fTransport || fQ.trim()
    || onlyTransport);
  const clearFilters = () => {
    setFDept(''); setFSeg(''); setFStatus(''); setFOwner(''); setFTransport('');
    setFQ(''); setOnlyTransport(false);
  };

  // 无目标段（口径同日报 reporting.py 的 `_no_tgt`）：当前停留段没设目标天 →
  // 后端 stay_left 恒为 None，这批 SKU 既不进「超期」也不进「预警」，等于在所有
  // 风险列表里隐身（2026-11-06：33 个在研里 16 个是这种）。日报 xlsx 早有这张表，
  // 页面一直没有 —— 这里补齐，并单列成独立小节而不是混在清单里沉底。
  const noTarget = useMemo(
    () => items.filter((it) => it.stayTarget == null
      && it.stayElapsed != null && it.stayElapsed > 0)
      .sort((a, b) => (b.stayElapsed || 0) - (a.stayElapsed || 0)),
    [items]);

  // 久悬单（2026-11-06 方案4，口径同后端 reporting.py 的 OVER_STALE）：
  // **当前停留段**超期 ≥90 天才算 —— 历史段超 90 但当前段在跑的不算（过去了）。
  // 这批 SKU 已经不是「催一下」的事：要么重启评估、要么登记豁免/关闭。
  const staleItems = useMemo(() => items
    .map((it) => ({ it, overDays: it.stayDaysLeft != null && it.stayDaysLeft < 0
      ? Math.round(-it.stayDaysLeft * 10) / 10 : null }))
    .filter((x) => x.overDays != null && x.overDays >= STALE_DAYS)
    .sort((a, b) => b.overDays - a.overDays),
    [items]);

  // 按责任人聚合（方案1：催办视角 —— 日报是拿去催人的，按人分组才顺手）。
  // 块排序与后端「按责任人·催办」sheet 同序：久悬多 → 超期多 → 在研多 → 名字。
  const ownerGroups = useMemo(() => {
    const map = new Map();
    items.forEach((it) => {
      const key = it.owner || '(未配置)';
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(it);
    });
    const rows = [...map.entries()].map(([owner, list]) => {
      const sorted = [...list].sort((a, b) => {
        const av = a.stayDaysLeft == null ? Infinity : a.stayDaysLeft;
        const bv = b.stayDaysLeft == null ? Infinity : b.stayDaysLeft;
        return av - bv || String(a.sku).localeCompare(String(b.sku));
      });
      const overCount = sorted.filter((x) => x.status === '超期').length;
      const staleCount = sorted.filter((x) => x.stayDaysLeft != null
        && x.stayDaysLeft < 0 && -x.stayDaysLeft >= STALE_DAYS).length;
      return { owner, rows: sorted, overCount, staleCount };
    });
    return rows.sort((a, b) => b.staleCount - a.staleCount
      || b.overCount - a.overCount || b.rows.length - a.rows.length
      || a.owner.localeCompare(b.owner));
  }, [items]);

  // KPI：运输未闭环 = 有未到货渠道的在研 SKU 数（不与筛选联动）
  const pendingTransportCount = items.filter(
    (it) => (it.pendingCountries || []).length).length;

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
        actions={tab === 'daily' ? <>
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
        </> : null}
      />

      {/* 次 sheet：登记表（SKU换号 / 人工豁免）—— 这两张子表直接决定日报里谁「不催办」，
          属于日报口径的一部分，所以挂在事中日报页下，而不是数据管理页（那边管 ETL 与备份）。 */}
      <Tabs value={tab} onChange={(e, v) => setTab(v)} sx={{ mb: 2, minHeight: 36 }}>
        <Tab value="daily" sx={{ minHeight: 36 }}
          label={`在研日报${data ? `（${kpi.inResearchCount ?? 0}）` : ''}`} />
        <Tab value="registry" sx={{ minHeight: 36 }}
          label={`登记表·人工豁免${registry.data ? `（${registry.data.holdTotal ?? 0}）` : ''}`} />
      </Tabs>

      {tab === 'daily' && (
        <>
          {loading && <LoadingState label="计算在研清单…" />}
          {error && <ErrorState error={error} />}
        </>
      )}

      {tab === 'daily' && data && !error && (
        <>
          <Box sx={{ display: 'grid',
            gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(6, 1fr)' },
            gap: 2, mb: 2 }}>
            <KpiCard label="在研 SKU" value={kpi.inResearchCount ?? 0}
              accent={SEMANTIC.brand} />
            <KpiCard label="超期 SKU" value={kpi.overdueSkuCount ?? 0}
              color={SEMANTIC.danger} accent={SEMANTIC.danger}
              sub="当前停留环节已超目标天" />
            <KpiCard label="临近预警·环节" value={alerts.data ? warns.length : (kpi.warnCount ?? 0)}
              color={SEMANTIC.warning} accent={SEMANTIC.warning}
              sub={warnSkuCount > 0
                ? `已用 ≥85% 目标天 · 涉及 ${warnSkuCount} 个 SKU`
                : '已用 ≥85% 目标天（未超期）'} />
            <KpiCard label="多轮提交" value={kpi.multiRoundCount ?? 0}
              color={SEMANTIC.caution} accent={SEMANTIC.caution}
              sub="同一单据被撤回重提" />
            <KpiCard label="运输未闭环" value={pendingTransportCount}
              color={SEMANTIC.warning} accent={SEMANTIC.warning}
              sub="有已发货未到货渠道" />
            <KpiCard label={`久悬单(≥${STALE_DAYS}天)`} value={staleItems.length}
              color={SEMANTIC.danger} accent={SEMANTIC.danger}
              sub="当前停留段超期 ≥90 天 · 催不动了，需决策" />
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
                临近超期预警（{warns.length} 个环节）· 达到目标天的 85% 即亮灯
                {warnSkuCount > 0 && warnSkuCount !== warns.length
                  ? ` · 涉及 ${warnSkuCount} 个 SKU` : ''}
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

          {/* 按责任人 · 催办视角（2026-11-06 方案1）：日报是拿去催人的 ——
              在研 SKU 摊在几个人头上，按人分组才顺手。点行 → 在研清单只看此人。
              排序与后端日报「按责任人·催办」sheet 同序：久悬多 → 超期多 → 在研多。 */}
          <SectionCard title={`按责任人 · 催办视角（${ownerGroups.length} 人）`} sx={{ mb: 2 }}
            subtitle="点一行 → 下方在研清单只看这个人名下的 SKU；块排序 = 久悬多 → 超期多 → 在研多">
            <TableContainer component={Paper} variant="outlined">
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ width: '16%' }}>责任人</TableCell>
                    <TableCell align="right" sx={{ width: '10%' }}>名下在研</TableCell>
                    <TableCell align="right" sx={{ width: '8%' }}>超期</TableCell>
                    <TableCell align="right" sx={{ width: '8%' }}>久悬</TableCell>
                    <TableCell sx={{ width: '18%' }}>最该催（SKU · 环节）</TableCell>
                    <TableCell>已停留</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {ownerGroups.map((g) => {
                    const top = g.rows[0];
                    return (
                      <TableRow key={g.owner} hover sx={{ cursor: 'pointer' }}
                        onClick={() => setFOwner(g.owner === '(未配置)' ? '' : g.owner)}>
                        <TableCell sx={{ fontWeight: 600, whiteSpace: 'nowrap' }}>
                          {g.owner}
                        </TableCell>
                        <TableCell align="right">{g.rows.length}</TableCell>
                        <TableCell align="right">
                          {g.overCount > 0
                            ? <Box component="span" sx={{ color: SEMANTIC.danger, fontWeight: 600 }}>
                                {g.overCount}
                              </Box>
                            : g.overCount}
                        </TableCell>
                        <TableCell align="right">
                          {g.staleCount > 0
                            ? <Box component="span" sx={{ color: 'error.dark', fontWeight: 700 }}>
                                {g.staleCount}
                              </Box>
                            : g.staleCount}
                        </TableCell>
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>
                          {top
                            ? `${top.sku} · ${top.staySeg || '—'}`
                            : '—'}
                        </TableCell>
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>
                          {top && top.stayElapsed != null ? `${top.stayElapsed} 天` : '—'}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                  {!ownerGroups.length && (
                    <TableRow><TableCell colSpan={6}>当前无在研 SKU</TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
            </TableContainer>
          </SectionCard>

          {/* 久悬单（2026-11-06 方案4）：当前停留段超期 ≥90 天 —— 这批已不是「催一下」的事，
              要么重启评估、要么登记豁免/关闭。口径写进 KPI 卡片副标题与表头，避免误读为普通超期。
              「建议动作」只出文案，不改任何状态；真关单/豁免仍走人工登记通道。 */}
          <SectionCard title={`久悬单（≥${STALE_DAYS} 天 · ${staleItems.length}）`} sx={{ mb: 2 }}
            subtitle={`当前停留段超期 ≥${STALE_DAYS} 天 —— 催已失效，需决策：重启评估 / 登记豁免 / 关闭`}>
            <TableContainer component={Paper} variant="outlined">
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>SKU</TableCell>
                    <TableCell>机型</TableCell>
                    <TableCell>当前停留环节</TableCell>
                    <TableCell>部门</TableCell>
                    <TableCell align="right">已停留</TableCell>
                    <TableCell align="right">目标</TableCell>
                    <TableCell align="right">超期</TableCell>
                    <TableCell>责任人</TableCell>
                    <TableCell>建议动作</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {staleItems.map(({ it, overDays }) => (
                    <TableRow key={it.sku} hover sx={{ cursor: 'pointer' }}
                      onClick={() => setSelSku(it.sku)}>
                      <TableCell sx={{ fontWeight: 600, whiteSpace: 'nowrap' }}>{it.sku}</TableCell>
                      <TableCell>{it.model || '—'}</TableCell>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>{it.staySeg || '—'}</TableCell>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>{it.staySegDept || '—'}</TableCell>
                      <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                        {it.stayElapsed == null ? '—' : `${it.stayElapsed} 天`}
                      </TableCell>
                      <TableCell align="right">{it.stayTarget ?? '无目标'}</TableCell>
                      <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                        <Box component="span" sx={{ color: 'error.dark', fontWeight: 700 }}>
                          {`${overDays} 天`}
                        </Box>
                      </TableCell>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>{it.owner || '⚠未配置'}</TableCell>
                      <TableCell>
                        <Typography variant="caption" sx={{ color: 'error.dark' }}>
                          {staleAction(it)}
                        </Typography>
                      </TableCell>
                    </TableRow>
                  ))}
                  {!staleItems.length && (
                    <TableRow>
                      <TableCell colSpan={9}>
                        {`无超期 ≥${STALE_DAYS} 天的久悬单`}
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </TableContainer>
          </SectionCard>

          <SectionCard title={`在研清单（${filtered.length}/${items.length}）`} sx={{ mb: 2 }}>
            <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'center',
              flexWrap: 'wrap', mb: 0.5 }}>
              <TextField size="small" select label="责任人" value={fOwner}
                onChange={(e) => setFOwner(e.target.value)} sx={{ width: 130 }}>
                <MenuItem value="">全部</MenuItem>
                {ownerOpts.map((o) => <MenuItem key={o} value={o}>{o}</MenuItem>)}
              </TextField>
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
              <TextField size="small" select label="运输状态" value={fTransport}
                onChange={(e) => setFTransport(e.target.value)} sx={{ width: 140 }}>
                <MenuItem value="">全部</MenuItem>
                <MenuItem value="closed">已闭环</MenuItem>
                <MenuItem value="pending">有未到货</MenuItem>
                <MenuItem value="unshipped">有未发货</MenuItem>
              </TextField>
              <TextField size="small" label="搜索 SKU / 机型" value={fQ}
                onChange={(e) => setFQ(e.target.value)} sx={{ width: 190 }} />
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
              sx={{ display: 'block' }}>
              筛选仅影响本表，上方 KPI 为全量。「无目标」= 该段未设目标天（运输在途 / 渠道未发货），
              不参与超期与预警判定，只看「已停留」自行判断要不要催。
            </Typography>
            <Typography variant="caption" color="text.secondary"
              sx={{ display: 'block', mb: 1 }}>
              emag「·未发货」在日报里按「有发货才标记」不计入（2026-10-15 口径）——
              本页展示它，不代表欠账。
            </Typography>
            <TableContainer component={Paper} variant="outlined">
              <Table size="small" sx={{ tableLayout: 'fixed' }}>
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ width: '7%' }}>SKU</TableCell>
                    <TableCell sx={{ width: '15%' }}>机型</TableCell>
                    <TableCell sx={{ width: '14%' }}>当前停留环节</TableCell>
                    <TableCell sx={{ width: '6%' }}>部门</TableCell>
                    <TableCell sx={{ width: '7%' }} align="right">已停留</TableCell>
                    <TableCell sx={{ width: '9%' }}>距超时</TableCell>
                    <TableCell sx={{ width: '10%' }}>预警段</TableCell>
                    <TableCell sx={{ width: '7%' }}>状态</TableCell>
                    <TableCell sx={{ width: '12%' }}>运输</TableCell>
                    <TableCell sx={{ width: '6%' }}>多轮</TableCell>
                    <TableCell sx={{ width: '7%' }}>负责人</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {filtered.map((it) => (
                    <TableRow key={it.sku} hover sx={{ cursor: 'pointer' }}
                      onClick={() => setSelSku(it.sku)}>
                      <TableCell sx={{ whiteSpace: 'nowrap', fontWeight: 600 }}>
                        {it.sku}
                      </TableCell>
                      <TableCell>{it.model || '—'}</TableCell>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>{it.staySeg || '—'}</TableCell>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>{it.staySegDept || '—'}</TableCell>
                      {/* 已停留：后端一直有 stayElapsed，之前没渲染 —— 运输/未发货段
                          没有目标天，「距超时」必然是空，这一列成了那 16 行唯一的时钟 */}
                      <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                        {it.stayElapsed == null ? '—' : `${it.stayElapsed} 天`}
                      </TableCell>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>
                        {it.stayTarget == null
                          ? (
                            <Box component="span" sx={{ fontSize: 12, color: SEMANTIC.muted }}>
                              无目标
                            </Box>
                          )
                          : (it.stayDaysLeft == null
                            ? '—'
                            : (it.stayDaysLeft < 0
                              ? <Box component="span"
                                  sx={{ color: SEMANTIC.danger, fontWeight: 600 }}>
                                  {`已超${Math.round(-it.stayDaysLeft)}天`}
                                </Box>
                              : `剩${Math.round(it.stayDaysLeft)}天`))}
                      </TableCell>
                      <TableCell>{(it.warnSegs || []).join('、') || '—'}</TableCell>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>
                        <StateChip state={it.status} />
                      </TableCell>
                      <TableCell><TransportCell it={it} /></TableCell>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>
                        {it.multiRound ? '⚠ 多轮' : '—'}
                      </TableCell>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>{it.owner || '—'}</TableCell>
                    </TableRow>
                  ))}
                  {!filtered.length && (
                    <TableRow>
                      <TableCell colSpan={11}>
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

          <SectionCard title={`无目标段（${noTarget.length}）`} sx={{ mb: 2 }}
            subtitle="当前停留段未设目标天 → 不参与超期/预警判定；按「已停留」人工判断要不要催">
            <TableContainer component={Paper} variant="outlined">
              <Table size="small" sx={{ tableLayout: 'fixed' }}>
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ width: '9%' }}>SKU</TableCell>
                    <TableCell sx={{ width: '22%' }}>机型</TableCell>
                    <TableCell sx={{ width: '20%' }}>当前停留环节</TableCell>
                    <TableCell sx={{ width: '10%' }} align="right">已停留</TableCell>
                    <TableCell sx={{ width: '25%' }}>运输进度</TableCell>
                    <TableCell sx={{ width: '14%' }}>负责人</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {noTarget.map((it) => (
                    <TableRow key={it.sku} hover sx={{ cursor: 'pointer' }}
                      onClick={() => setSelSku(it.sku)}>
                      <TableCell sx={{ whiteSpace: 'nowrap', fontWeight: 600 }}>
                        {it.sku}
                      </TableCell>
                      <TableCell>{it.model || '—'}</TableCell>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>{it.staySeg || '—'}</TableCell>
                      <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                        {it.stayElapsed} 天
                      </TableCell>
                      <TableCell><TransportCell it={it} /></TableCell>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>{it.owner || '—'}</TableCell>
                    </TableRow>
                  ))}
                  {!noTarget.length && (
                    <TableRow>
                      <TableCell colSpan={6}>无 —— 所有在研段都设了目标天</TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </TableContainer>
          </SectionCard>

          <SectionCard title={`运输在途 · 逐票（${transitRows.length}）`} sx={{ mb: 2 }}
            subtitle="只列未到货的票 + 未发货的渠道 —— 用于催物流（已到货票默认折叠）">
            <TableContainer component={Paper} variant="outlined">
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>SKU</TableCell>
                    <TableCell>机型</TableCell>
                    <TableCell>渠道（国家）</TableCell>
                    <TableCell>票号</TableCell>
                    <TableCell>发货</TableCell>
                    <TableCell>到货</TableCell>
                    <TableCell>状态</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {(showArrived ? [...transitRows, ...arrivedRows] : transitRows)
                    .map((r, i) => (
                      <TableRow key={`${r.sku}-${r.trk || r.hub}-${i}`} hover
                        sx={{ cursor: 'pointer' }}
                        onClick={() => setSelSku(r.sku)}>
                        <TableCell sx={{ fontWeight: 600 }}>{r.sku}</TableCell>
                        <TableCell>{r.model || '—'}</TableCell>
                        <TableCell>{r.hub || '—'}</TableCell>
                        <TableCell sx={{ fontFamily: 'monospace' }}>{r.trk || '—'}</TableCell>
                        <TableCell>{r.ship || '—'}</TableCell>
                        <TableCell>{r.arr || '—'}</TableCell>
                        <TableCell>
                          <Chip size="small" label={r.state}
                            variant={r.state === '已到货' ? 'outlined' : 'filled'}
                            sx={r.state === '已到货'
                              ? { color: SEMANTIC.success, borderColor: SEMANTIC.success }
                              : { bgcolor: SEMANTIC.warning, color: SEMANTIC.onColor }} />
                        </TableCell>
                      </TableRow>
                    ))}
                  {arrivedRows.length > 0 && (
                    <TableRow>
                      <TableCell colSpan={7} sx={{ py: 0.5 }}>
                        <Button size="small" onClick={() => setShowArrived((v) => !v)}>
                          {showArrived
                            ? `收起已到货 ${arrivedRows.length} 票`
                            : `另有已到货 ${arrivedRows.length} 票（不影响催办）—— 展开查看`}
                        </Button>
                      </TableCell>
                    </TableRow>
                  )}
                  {!transitRows.length && (
                    <TableRow>
                      <TableCell colSpan={7}>
                        无待办运输（无未到货票 / 无未发货渠道）
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </TableContainer>
          </SectionCard>

          <SectionCard title={`运输已闭环 · 免追踪（${transportExempted.length}）`} sx={{ mb: 2 }}
            subtitle={data.transportDef?.note
              || '在售品（所有段已完成且运输无未到货）→ 运输域不再是卡点，免追踪'}>
            <Typography variant="caption" color="text.secondary"
              sx={{ display: 'block', mb: 1 }}>
              判据：<strong>所有段都已推进到位</strong>（无任何在研段，运输域也算）且运输无未到货
              （已发货货件全部到货 / 无可发货货件）—— 属<strong>在售品</strong>，
              物流到货不是可催办动作，故移出在研清单，单列备查。
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
            subtitle="按保留期自动清理过期文件 · 超期/预警列是环节行数（= 表内数据行）">
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>日期</TableCell>
                  <TableCell>在研（SKU）</TableCell>
                  <TableCell>超期（环节）</TableCell>
                  <TableCell>预警（环节）</TableCell>
                  <TableCell>下载</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {repItems.map((r) => (
                  <TableRow key={`${r.category}-${r.date}`}>
                    <TableCell>{r.date}</TableCell>
                    <TableCell>{r.skuCount ?? '—'}</TableCell>
                    <TableCell>{r.overdueCount ?? '—'}</TableCell>
                    <TableCell title={r.warnSkuCount != null
                      ? `涉及 ${r.warnSkuCount} 个 SKU` : undefined}>
                      {r.warnCount ?? '—'}
                      {r.warnSkuCount != null && r.warnSkuCount !== r.warnCount
                        ? `（${r.warnSkuCount} SKU）` : ''}
                    </TableCell>
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

      {tab === 'registry' && (
        <RegistryImport registry={registry.data} onDone={registry.refetch} />
      )}

      <SkuDialog sku={selSku} onClose={() => setSelSku(null)} />
    </>
  );
}
