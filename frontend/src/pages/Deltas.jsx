import React, { useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  Grid,
  MenuItem,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import FileDownloadIcon from '@mui/icons-material/FileDownload';
import AddchartIcon from '@mui/icons-material/Addchart';
import { LoadingState, ErrorState, EmptyState } from '../components/StateViews.jsx';
import PageHeader from '../components/PageHeader.jsx';
import SectionCard from '../components/SectionCard.jsx';
import KpiCard from '../components/KpiCard.jsx';
import EChart from '../components/EChart.jsx';
import useApi from '../hooks/useApi.js';
import { SEMANTIC } from '../theme.js';
import {
  fetchDeltas,
  buildDeltasExportUrl,
  triggerDeltaSnapshot,
} from '../api.js';

/**
 * Deltas —— 单据增量（未闭环 SKU 的新增单据追踪，T10）
 *
 * 为什么有这一页：本系统改成「钉钉每天导出最新全量、批次目录名固定」后，
 * 批次表里永远只有一个批次在长，「批次对比」页没有第二个批次可比；
 * 而单看「4027 个单据」这样的大数，业务感知不到「这周到底动了多少」。
 * 这一页把同一批**未闭环 SKU** 的单据集合当作随时间演化的状态：
 * 每次数据指纹变化拍一次快照，只记录增量（新增单据 / 既有单据的新动作 /
 * 状态变化 / 进入或离开未闭环集合），于是「感知弱」变成一张可数的清单。
 */

const WIN_OPTIONS = [
  { value: 7, label: '近 7 天' },
  { value: 30, label: '近 30 天' },
  { value: 90, label: '近 90 天' },
];

const TABS = [
  { key: 'newDocs', label: '新增单据', color: 'error' },
  { key: 'newNodes', label: '单据新动作', color: 'warning' },
  { key: 'statusChanges', label: '状态变化', color: 'info' },
];

const STATUS_COLOR = {
  已结束: 'error',
  审批中: 'warning',
  终止: 'default',
};

function toLabel(d) {
  return d ? `${d.slice(0, 10)} ${d.slice(11, 16)}` : '—';
}

export default function Deltas() {
  const [days, setDays] = useState(30);
  const [sku, setSku] = useState('');
  const [tab, setTab] = useState('newDocs');
  const [snapping, setSnapping] = useState(false);
  const [flash, setFlash] = useState('');

  const { data, error, loading, refetch } = useApi(
    () => fetchDeltas({ days, sku: sku || undefined }),
    [days, sku],
  );

  const chartOption = useMemo(() => {
    const rows = data?.window?.byDay || [];
    const pick = (k) => rows.map((r) => r[k]);
    return {
      tooltip: { trigger: 'axis' },
      legend: { data: ['新增单据', '单据新动作', '状态变化'], bottom: 0 },
      grid: { left: 48, right: 16, top: 24, bottom: 48 },
      xAxis: { type: 'category', data: rows.map((r) => r.date) },
      yAxis: { type: 'value', minInterval: 1 },
      series: [
        { name: '新增单据', type: 'bar', stack: 'x', data: pick('newDocs'),
          itemStyle: { color: SEMANTIC.danger } },
        { name: '单据新动作', type: 'bar', stack: 'x', data: pick('newNodes'),
          itemStyle: { color: SEMANTIC.warning } },
        { name: '状态变化', type: 'bar', stack: 'x', data: pick('statusChanges'),
          itemStyle: { color: SEMANTIC.info } },
      ],
    };
  }, [data]);

  const doSnapshot = async () => {
    setSnapping(true);
    setFlash('');
    try {
      const r = await triggerDeltaSnapshot(true);
      setFlash(`${r.recorded ? '已记录' : '未记录'}：${r.reason}`);
      await refetch();
    } catch (e) {
      setFlash(`拍快照失败：${e.message}`);
    } finally {
      setSnapping(false);
    }
  };

  if (loading && !data) return <LoadingState />;
  if (error) return <ErrorState prefix="加载失败" error={error} />;
  if (!data) return null;

  const s = data.window.summary;
  const last = data.last;
  const exportQuery = { days, sku };
  const tabRows = (last && last[tab]) || [];

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <PageHeader
        title="单据增量"
        subtitle={`追踪范围：${data.scope}。批次名固定后批次对比已无意义，`
          + '改为逐次追踪「这批未闭环 SKU 又多了哪些单据」。'}
        actions={
          <>
            <TextField
              select
              size="small"
              label="窗口"
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              sx={{ width: 130 }}
            >
              {WIN_OPTIONS.map((o) => (
                <MenuItem key={o.value} value={o.value}>{o.label}</MenuItem>
              ))}
            </TextField>
            <TextField
              size="small"
              label="只看 SKU"
              value={sku}
              onChange={(e) => setSku(e.target.value.trim())}
              sx={{ width: 140 }}
            />
            <Button
              variant="outlined"
              size="small"
              startIcon={<AddchartIcon />}
              disabled={snapping}
              onClick={doSnapshot}
            >
              {snapping ? '拍摄中…' : '立即拍快照'}
            </Button>
            <a
              href={buildDeltasExportUrl(exportQuery)}
              style={{ textDecoration: 'none' }}
            >
              <Button variant="outlined" size="small" startIcon={<FileDownloadIcon />}>
                导出 xlsx
              </Button>
            </a>
          </>
        }
      />

      {flash && <Alert severity="info" onClose={() => setFlash('')}>{flash}</Alert>}

      {!data.exists && (
        <Alert severity="info">
          尚未建立基线：会在下一次数据同步（或点「立即拍快照」）时建立。
          首次只建基线、不报增量——否则会把当前全部未闭环单据一次性算成「新增」。
        </Alert>
      )}

      {last?.suspect && (
        <Alert severity="warning">
          最近一次快照的新增单据占当前单据总数比例过高，
          <b>疑似基线重算</b>（源单据号段变化 / 基线文件被清理），
          请先按「快照流水」核对，不要把这一轮数字当真增量引用。
        </Alert>
      )}

      {data.exists && !last && (
        <Alert severity="info">
          基线已建立，但还没有产生任何增量事件——说明自基线以来数据指纹没变过。
        </Alert>
      )}

      <Grid container spacing={2}>
        <Grid item xs={12} sm={6} md={2.4}>
          <KpiCard
            label={`新增单据（近 ${days} 天）`}
            value={s.newDocs}
            sub={`共 ${s.snapshots} 次快照`}
            color={SEMANTIC.danger}
            accent={SEMANTIC.danger}
          />
        </Grid>
        <Grid item xs={12} sm={6} md={2.4}>
          <KpiCard
            label="单据新动作"
            value={s.newNodes}
            sub="既有单据上的推进"
            color={SEMANTIC.warning}
            accent={SEMANTIC.warning}
          />
        </Grid>
        <Grid item xs={12} sm={6} md={2.4}>
          <KpiCard
            label="状态变化"
            value={s.statusChanges}
            sub="审批中→已结束 等"
            color={SEMANTIC.info}
            accent={SEMANTIC.info}
          />
        </Grid>
        <Grid item xs={12} sm={6} md={2.4}>
          <KpiCard
            label="新进入未闭环"
            value={s.newSkus}
            sub="新 SKU / 刚有数据"
            color={SEMANTIC.brand}
          />
        </Grid>
        <Grid item xs={12} sm={6} md={2.4}>
          <KpiCard
            label="已闭环离开"
            value={s.closedSkus}
            sub="离开追踪集合"
            color={SEMANTIC.success}
          />
        </Grid>
      </Grid>

      <SectionCard
        title={`增量走势（近 ${days} 天）`}
        subtitle="按日聚合的增量事件；柱子为空 = 当天没有同步或数据未变化"
        divider
      >
        {(data.window.byDay || []).length === 0 ? (
          <EmptyState>窗口内没有增量事件</EmptyState>
        ) : (
          <EChart option={chartOption} height={300} />
        )}
      </SectionCard>

      <SectionCard
        title="最近一次同步的增量"
        subtitle={last
          ? `快照时间 ${toLabel(last.at)}${last.baseline ? '（首次基线，不计增量）' : ''}`
          : '尚无增量事件'}
        actions={
          <Box sx={{ display: 'flex', gap: 0.75 }}>
            {TABS.map((t) => (
              <Chip
                key={t.key}
                size="small"
                label={`${t.label}（${(last?.[t.key] || []).length}）`}
                color={tab === t.key ? t.color : 'default'}
                variant={tab === t.key ? 'filled' : 'outlined'}
                onClick={() => setTab(t.key)}
              />
            ))}
          </Box>
        }
        divider
      >
        <TableContainer sx={{ maxHeight: 420 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              {tab === 'newDocs' && (
                <TableRow>
                  <TableCell>SKU</TableCell>
                  <TableCell>Part</TableCell>
                  <TableCell>审批编号</TableCell>
                  <TableCell>审批状态</TableCell>
                  <TableCell>创建时间</TableCell>
                  <TableCell align="right">节点数</TableCell>
                </TableRow>
              )}
              {tab === 'newNodes' && (
                <TableRow>
                  <TableCell>SKU</TableCell>
                  <TableCell>Part</TableCell>
                  <TableCell>审批编号</TableCell>
                  <TableCell>新动作</TableCell>
                  <TableCell>动作时间</TableCell>
                  <TableCell>操作人</TableCell>
                </TableRow>
              )}
              {tab === 'statusChanges' && (
                <TableRow>
                  <TableCell>SKU</TableCell>
                  <TableCell>Part</TableCell>
                  <TableCell>审批编号</TableCell>
                  <TableCell>原状态</TableCell>
                  <TableCell>新状态</TableCell>
                </TableRow>
              )}
            </TableHead>
            <TableBody>
              {tabRows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} align="center">
                    最近一次同步没有这一类增量
                  </TableCell>
                </TableRow>
              )}
              {tab === 'newDocs' && tabRows.map((r, i) => (
                <TableRow key={i} hover>
                  <TableCell>{r.sku}</TableCell>
                  <TableCell>{r.part}</TableCell>
                  <TableCell sx={{ fontFamily: 'monospace' }}>{r.approvalNo || '—'}</TableCell>
                  <TableCell>
                    <Chip size="small" label={r.status || '—'}
                      color={STATUS_COLOR[r.status] || 'default'} />
                  </TableCell>
                  <TableCell>{r.createAt || '—'}</TableCell>
                  <TableCell align="right">{r.nodeCount}</TableCell>
                </TableRow>
              ))}
              {tab === 'newNodes' && tabRows.map((r, i) => (
                <TableRow key={i} hover>
                  <TableCell>{r.sku}</TableCell>
                  <TableCell>{r.part}</TableCell>
                  <TableCell sx={{ fontFamily: 'monospace' }}>{r.approvalNo || '—'}</TableCell>
                  <TableCell>{r.action}</TableCell>
                  <TableCell>{r.at || '—'}</TableCell>
                  <TableCell>{r.operator || '—'}</TableCell>
                </TableRow>
              ))}
              {tab === 'statusChanges' && tabRows.map((r, i) => (
                <TableRow key={i} hover>
                  <TableCell>{r.sku}</TableCell>
                  <TableCell>{r.part}</TableCell>
                  <TableCell sx={{ fontFamily: 'monospace' }}>{r.approvalNo || '—'}</TableCell>
                  <TableCell>{r.from}</TableCell>
                  <TableCell>
                    <Chip size="small" label={r.to}
                      color={STATUS_COLOR[r.to] || 'default'} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </SectionCard>

      <SectionCard
        title={`未闭环 SKU 增量排行（近 ${days} 天）`}
        subtitle={data.state
          ? `当前基线：${data.state.skuCount} 个未闭环 SKU / ${data.state.docCount} 张单据`
          : '尚未建立基线'}
        divider
      >
        <TableContainer sx={{ maxHeight: 460 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell>SKU</TableCell>
                <TableCell>型号</TableCell>
                <TableCell>开发类型</TableCell>
                <TableCell>未闭环原因</TableCell>
                <TableCell align="right">当前单据</TableCell>
                <TableCell align="right">新增单据</TableCell>
                <TableCell align="right">新增动作</TableCell>
                <TableCell>最近变化</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {(data.window.bySku || []).length === 0 && (
                <TableRow>
                  <TableCell colSpan={8} align="center">
                    窗口内没有 SKU 发生变化
                  </TableCell>
                </TableRow>
              )}
              {(data.window.bySku || []).map((r) => (
                <TableRow key={r.sku} hover>
                  <TableCell
                    sx={{ cursor: 'pointer' }}
                    onClick={() => setSku(sku === r.sku ? '' : r.sku)}
                  >
                    <Tooltip title={sku === r.sku ? '取消筛选' : '只看这个 SKU'}>
                      <span>{r.sku}</span>
                    </Tooltip>
                  </TableCell>
                  <TableCell>{r.model || '—'}</TableCell>
                  <TableCell>{r.devType || '—'}</TableCell>
                  <TableCell sx={{ maxWidth: 320 }}>{r.reason || '—'}</TableCell>
                  <TableCell align="right">{r.docCount}</TableCell>
                  <TableCell align="right"
                    sx={{ fontWeight: r.newDocs ? 700 : 400,
                          color: r.newDocs ? SEMANTIC.danger : 'inherit' }}>
                    {r.newDocs}
                  </TableCell>
                  <TableCell align="right"
                    sx={{ fontWeight: r.newNodes ? 700 : 400,
                          color: r.newNodes ? SEMANTIC.warning : 'inherit' }}>
                    {r.newNodes}
                  </TableCell>
                  <TableCell>{toLabel(r.lastChangeAt)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </SectionCard>

      <SectionCard
        title="快照流水"
        subtitle={`每次数据指纹变化记一条；事件保留 ${data.settings.retentionDays} 天`
          + `（当前累计 ${data.eventCount} 条）`}
        divider
      >
        <TableContainer sx={{ maxHeight: 360 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell>快照时间</TableCell>
                <TableCell>类型</TableCell>
                <TableCell align="right">新增单据</TableCell>
                <TableCell align="right">新动作</TableCell>
                <TableCell align="right">状态变化</TableCell>
                <TableCell align="right">单据总数</TableCell>
                <TableCell>标记</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {(data.events || []).length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} align="center">还没有增量事件</TableCell>
                </TableRow>
              )}
              {(data.events || []).map((e, i) => (
                <TableRow key={i} hover>
                  <TableCell>{toLabel(e.at)}</TableCell>
                  <TableCell>
                    {e.baseline ? '基线' : '增量'}
                  </TableCell>
                  <TableCell align="right">{e.summary.newDocs || 0}</TableCell>
                  <TableCell align="right">{e.summary.newNodes || 0}</TableCell>
                  <TableCell align="right">{e.summary.statusChanges || 0}</TableCell>
                  <TableCell align="right">{e.summary.docCount || 0}</TableCell>
                  <TableCell>
                    {e.suspect && (
                      <Tooltip title="新增单据占比过高，疑似源单据号段变化（基线重算）">
                        <Chip size="small" color="warning" label="疑似基线重算" />
                      </Tooltip>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
          新增单据的动作不计入「新增动作」，避免一张新单被数成 1 单 + N 个动作。
        </Typography>
      </SectionCard>
    </Box>
  );
}
