import React, { useMemo, useState } from 'react';
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
  Dialog,
  DialogTitle,
  DialogContent,
  LinearProgress,
  Tooltip,
  Button,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import EChart from '../components/EChart.jsx';
import {
  fetchReviewOverview, fetchReviewSku, buildReviewExportUrl,
  buildCheckReportUrl, buildSkuReportUrl,
} from '../api.js';
import KpiCard from '../components/KpiCard.jsx';
import PageHeader from '../components/PageHeader.jsx';
import { LoadingState, ErrorState } from '../components/StateViews.jsx';
import { SEMANTIC } from '../theme.js';
import useApi from '../hooks/useApi.js';

const PART_SHORT = {
  Part1: 'P1 开发', Part2: 'P2 首单', Part3: 'P3 到货', Part4: 'P4 上架',
  Part5: 'P5 发货', Part6: 'P6 素材', Part7: 'P7 广告', Part8: 'P8 渠到货',
  Part9: 'P9 检查',
};

const DEPT_COLOR = { 产品: SEMANTIC.brand, 运营: SEMANTIC.success, 后勤: SEMANTIC.warning };

export default function Review() {
  const { data, error } = useApi(() => fetchReviewOverview(), []);
  const [q, setQ] = useState('');
  const [devFilter, setDevFilter] = useState('');
  const [validFilter, setValidFilter] = useState('');
  const [detailSku, setDetailSku] = useState(null);
  const [detail, setDetail] = useState(null);
  const [personQ, setPersonQ] = useState('');

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

  if (error) return <ErrorState prefix="复盘总览加载失败" error={error} />;
  if (!data) return <LoadingState />;

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
        { name: '有效复盘', value: validN, itemStyle: { color: SEMANTIC.success } },
        { name: '未完成三部门', value: totalN - validN, itemStyle: { color: SEMANTIC.muted } },
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
      itemStyle: { color: SEMANTIC.deepOrange },
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
        itemStyle: { color: s.onTimeRate >= 70 ? SEMANTIC.success : s.onTimeRate >= 40 ? SEMANTIC.caution : SEMANTIC.danger },
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
      itemStyle: { color: SEMANTIC.deepOrange },
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
        itemStyle: { color: SEMANTIC.brand },
        label: { show: true, position: 'top', formatter: '{c}%', fontSize: 10 },
      },
      {
        name: '考核段数', type: 'bar', yAxisIndex: 1, barWidth: 16,
        data: trend.map((t) => t.sampleCount),
        itemStyle: { color: SEMANTIC.border },
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
            sub="产品/运营/后勤三部门终节点全完成" color={SEMANTIC.success} />
        </Grid>
        <Grid item xs={6} md={3}>
          <KpiCard label="提前开发 SKU" value={`${devKpi.total}`}
            sub={`有效 ${data.devDist['提前开发']?.valid ?? 0} 个 · 占比 ${Math.round(100 * devKpi.total / totalN)}%`} />
        </Grid>
        <Grid item xs={6} md={3}>
          <KpiCard label="头号卡点环节" value={bottle1 ? bottle1.seg : '—'}
            sub={bottle1 ? `${bottle1.count} 个有效复盘 SKU 的最长段在此环节` : ''}
            color={SEMANTIC.deepOrange} />
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
                <TableCell>产品部·素材交付</TableCell>
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
                  <TableCell sx={{ fontWeight: 600 }}>
                    {r.sku}
                    {r.holdOut && (
                      <Tooltip title="人工登记：业务上已决定不再走完流程（流程停在中途属正常，不催办）">
                        <Chip size="small" variant="outlined" color="warning" sx={{ ml: 0.75 }}
                          label="已豁免" />
                      </Tooltip>
                    )}
                    {r.aliasOut && (
                      <Tooltip title={`流程中途换了编码：已由 ${r.aliasOut} 承接，旧号停在半路属正常，不催办`}>
                        <Chip size="small" variant="outlined" sx={{ ml: 0.75 }}
                          label={`换号 → ${r.aliasOut}`} />
                      </Tooltip>
                    )}
                    {(r.aliasIn || []).length > 0 && (
                      <Tooltip title={`承接旧号 ${r.aliasIn.join('、')} 的流程（原编码已停用）`}>
                        <Chip size="small" variant="outlined" color="info" sx={{ ml: 0.75 }}
                          label={`承接 ${r.aliasIn.join('/')}`} />
                      </Tooltip>
                    )}
                  </TableCell>
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
                      : r.contributionTotal >= 0 ? SEMANTIC.success : SEMANTIC.danger }}>
                    {r.contributionTotal == null ? '—' : r.contributionTotal}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
        <Typography variant="caption" color="text.secondary" sx={{ p: 2, display: 'block' }}>
          共 {rows.length} 个 SKU · 口径：实际动作映射（每段=起点动作→终点动作）· 总贡献 = Σ(段目标 − 实际天数)，正数表示快于目标，负数表示超时（仅考核段计入）
          {(data.aliases?.outN || data.aliases?.inN) ? (
            <>
              {' · '}
              <span style={{ color: SEMANTIC.neutral }}>
                换号：旧号 {data.aliases.outN ?? 0} 个（已换号→新号，不催办）·
                新号 {data.aliases.inN ?? 0} 个（承接旧号流程）
              </span>
            </>
          ) : ''}
          {(data.holds?.outN) ? (
            <>
              {' · '}
              <span style={{ color: SEMANTIC.neutral }}>
                人工登记豁免 {data.holds.outN ?? 0} 个（业务上已不需走完流程，不催办）
              </span>
            </>
          ) : ''}
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
                    sx={{ color: p.overCount > 0 ? SEMANTIC.danger : 'inherit' }}>
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
                              bgcolor: p.onTimeRate >= 70 ? SEMANTIC.success
                                : p.onTimeRate >= 40 ? SEMANTIC.caution : SEMANTIC.danger,
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
      <Dialog open={!!detailSku} onClose={() => setDetailSku(null)} maxWidth="lg" fullWidth>
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
          {detail && detail.error && <ErrorState>档案加载失败</ErrorState>}
          {detail && !detail.error && (
            <>
              {/* 下载按钮放内容区右上角，**不要放 DialogTitle 里**：DialogTitle 的 textContent
                  就是弹窗的可访问名（aria-labelledby 指向它），按钮文字混进去会让读屏念成
                  「复盘档案·G598 导出核查报告 复盘报告 md」。
                  顺带：放在这里还能免掉「弹窗刚开、detail 未回来时按钮短暂禁用」的闪烁。 */}
              <Box sx={{ display: 'flex', gap: 2, mb: 2, flexWrap: 'wrap', alignItems: 'center' }}>
                {Object.entries(detail.dept).map(([k, v]) => (
                  <Chip key={k} variant="outlined"
                    label={`${k}：${v.time || v.note}`}
                    color={v.time ? 'success' : 'default'} />
                ))}
                {detail.bottleneck && (
                  <Chip color="warning"
                    label={`卡点：${detail.bottleneck.dept} ${detail.bottleneck.seg}（${detail.bottleneck.days} 天）`} />
                )}
                <Box sx={{ flexGrow: 1 }} />
                <Button size="small" variant="contained" startIcon={<DownloadIcon />}
                  href={buildCheckReportUrl(detailSku)}
                  title="甘特（按部门归块）+ 段级明细 + 末尾「人工核对」空白列，供线下核查回填">
                  导出核查报告
                </Button>
                <Button size="small" variant="outlined" startIcon={<DownloadIcon />}
                  href={buildSkuReportUrl(detailSku)}
                  title="该 SKU 复盘的文字版（markdown），与流程甘特页「下载复盘报告 .md」是同一个文件">
                  复盘报告 md
                </Button>
              </Box>
              {(detail.hold || detail.noChase || (detail.aliasIn || []).length > 0) && (
                <Box sx={{ mb: 2, p: 1.5, borderRadius: 1,
                  border: '1px solid', borderColor: 'divider', bgcolor: 'action.hover' }}>
                  <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                    {detail.hold ? '已豁免（人工登记）· 不催办'
                      : (detail.noChase ? '已换号（旧号）· 不催办' : '已承接旧号（新号）')}
                  </Typography>
                  {detail.hold && (
                    <Typography variant="body2">
                      业务上<strong>已决定不再走完流程</strong>（渠道取消 / 并入其它型号 /
                      末端环节实际不执行），下面「未完成 / 缺节点」属正常，
                      <strong>不计入催办</strong>。
                      {detail.hold.reason ? ` 说明：${detail.hold.reason}` : ''}
                      {detail.hold.registeredAt ? `（登记于 ${detail.hold.registeredAt}）` : ''}
                    </Typography>
                  )}
                  {detail.aliasOut && (
                    <Typography variant="body2">
                      流程中途换了编码：<strong>{detailSku}</strong> → <strong>{detail.aliasOut.new}</strong>
                      {detail.aliasOut.effectiveAt ? `（${detail.aliasOut.effectiveAt} 生效）` : ''}
                      {detail.aliasOut.note ? ` · ${detail.aliasOut.note}` : ''}
                      。旧号流程必然停在中途，下面「未完成 / 缺节点」属正常，不计入催办。
                    </Typography>
                  )}
                  {(detail.aliasIn || []).length > 0 && (
                    <Typography variant="body2">
                      本号承接了旧号 <strong>{detail.aliasIn.join('、')}</strong>
                      的流程（原编码已停用，旧号的异常不计入催办）。
                    </Typography>
                  )}
                  <Typography variant="caption" color="text.secondary">
                    真源：SKU登记表.xlsx{detail.hold ? '「人工登记」' : '「SKU换号」'}子表（业务登记，非自动推断）
                  </Typography>
                </Box>
              )}
              {detail.multiRound?.flag && (
                <Box sx={{ mb: 2, p: 1.5, borderRadius: 1,
                  bgcolor: 'warning.light', color: 'warning.contrastText' }}>
                  <Typography variant="body2">
                    ⚠ 多轮提交：{detail.multiRound.note
                      || '该单据存在撤回后重提，段级时间口径以最早一轮为准'}
                  </Typography>
                </Box>
              )}
              {detail.rootCause && (
                <Box sx={{ mb: 2, p: 1.5, borderRadius: 1, bgcolor: 'action.hover' }}>
                  <Typography variant="subtitle2" sx={{ mb: 0.5 }}>核心原因（因果链）</Typography>
                  <Typography variant="body2">
                    {[detail.rootCause.dept, detail.rootCause.seg,
                      detail.rootCause.days != null
                        ? `${detail.rootCause.days} 天` : '',
                      detail.rootCause.reason || detail.rootCause.note]
                      .filter(Boolean).join(' · ') || '—'}
                  </Typography>
                </Box>
              )}
              {detail.assessment && (
                <Box sx={{ mb: 2, p: 1.5, borderRadius: 1,
                  border: '1px dashed', borderColor: 'divider' }}>
                  <Typography variant="subtitle2" sx={{ mb: 0.5 }}>损益推算</Typography>
                  <Typography variant="body2">
                    评级 {detail.assessment.grade || '—'} ·
                    推迟 {detail.assessment.delayDays ?? '—'} 天 ·
                    少卖 {detail.assessment.qtyMin ?? '—'}~{detail.assessment.qtyMax ?? '—'} 件 ·
                    金额 {detail.assessment.amountMin == null
                      ? '—'
                      : `¥${detail.assessment.amountMin}~¥${detail.assessment.amountMax}`}
                    {(detail.assessment.degraded || []).length
                      ? `（${detail.assessment.degraded.join('；')}）` : ''}
                  </Typography>
                </Box>
              )}
              {detail.sampleCycle && (
                <Box sx={{ mb: 2, p: 1.5, borderRadius: 1,
                  border: '1px dashed', borderColor: 'divider' }}>
                  <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                    样机流转：签样 → 拍摄交付
                  </Typography>
                  {detail.sampleCycle.days == null ? (
                    <Typography variant="body2" color="text.secondary">
                      实际日期缺失{detail.sampleCycle.missing?.length
                        ? `（${detail.sampleCycle.missing.join('、')} 没有系统业务日期）` : ''}
                      ，间隔不计算 —— 不拿 audit 补录时间冒充实际
                    </Typography>
                  ) : (
                    <Typography variant="body2">
                      {detail.sampleCycle.start} → {detail.sampleCycle.end}
                      {' · '}
                      <strong>{detail.sampleCycle.days} 天</strong>
                      {' · '}
                      <Box component="span" sx={{
                        fontWeight: 600,
                        color: detail.sampleCycle.ok ? SEMANTIC.success : SEMANTIC.danger,
                      }}>
                        {detail.sampleCycle.ok ? '✓ 合格' : '✗ 超线'}
                      </Box>
                      {`（合格线 < ${detail.sampleCycle.limit} 天）`}
                    </Typography>
                  )}
                </Box>
              )}
              {/* 段级表 10 列：加 minWidth 防止窄屏把「部门/执行人」压成竖排字 */}
              <Table size="small" sx={{ minWidth: 860 }}>
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>部门</TableCell>
                    <TableCell>环节</TableCell>
                    <TableCell>起点 → 终点</TableCell>
                    <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>实际(天)</TableCell>
                    <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>audit(天)</TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>双轨</TableCell>
                    <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>目标(天)</TableCell>
                    <TableCell>判定</TableCell>
                    <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>贡献(天)</TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>执行人</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {detail.segments.map((s, i) => (
                    <TableRow key={i} sx={s.gap ? { bgcolor: 'action.hover' } : undefined}>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>{s.gap ? '留空' : s.dept}</TableCell>
                      <TableCell sx={{ fontWeight: 600 }}>{s.seg}</TableCell>
                      <TableCell>{s.start || '—'} → {s.end || '—'}</TableCell>
                      <TableCell align="right">{s.gap ? '—' : (s.days ?? '—')}</TableCell>
                      <TableCell align="right" sx={{
                        color: s.auditDays == null ? 'text.disabled' : 'text.secondary',
                      }}
                        title={`${s.auditFrom || '?'} → ${s.auditTo || '?'}`
                          + (s.dualGap == null ? '' : `（比系统轨 ${s.dualGap > 0 ? '+' : ''}${s.dualGap} 天）`)
                          + ((s.auditMissing || []).length
                            ? `　缺 audit：${s.auditMissing.join('、')}` : '')}>
                        {s.auditDays ?? '—'}
                      </TableCell>
                      <TableCell>
                        {s.dualVerdict === 'agree' ? (
                          <Typography variant="caption" color="text.secondary">一致</Typography>
                        ) : (s.dualVerdict === 'system_only'
                          || s.dualVerdict === 'audit_worse') ? (
                          <Typography variant="caption"
                            sx={{ color: SEMANTIC.warning, fontWeight: 600 }}>
                            {s.dualVerdictText}
                          </Typography>
                        ) : (
                          <Typography variant="caption" color="text.disabled">—</Typography>
                        )}
                      </TableCell>
                      <TableCell align="right">
                        {s.gap || !s.assessed
                          ? <Typography variant="caption">{s.gap ? '—' : '不考核'}</Typography>
                          : s.target}
                      </TableCell>
                      <TableCell>
                        <Typography variant="caption" sx={{
                          fontWeight: 600,
                          color: String(s.judge || '').startsWith('✗') ? SEMANTIC.danger
                            : String(s.judge || '').startsWith('✓') ? SEMANTIC.success
                              : String(s.judge || '').startsWith('⚠') ? SEMANTIC.warning : 'text.secondary',
                        }}>
                          {s.judge || '—'}
                        </Typography>
                      </TableCell>
                      <TableCell align="right"
                        sx={{ color: s.contribution == null ? 'inherit'
                          : s.contribution >= 0 ? SEMANTIC.success : SEMANTIC.danger }}>
                        {s.gap ? '—' : (s.contribution == null ? '—' : s.contribution)}
                      </TableCell>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>{s.executor || '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <Typography variant="caption" color="text.secondary"
                sx={{ display: 'block', mt: 0.5 }}>
                audit(天) = 该段起点、终点都取审批上报时间算出的耗时，只做交叉核对，
                判定仍只看「实际(天)」（系统轨）。「仅系统轨超线」表示换成审批时间算并不超
                —— 超的是系统日期，值得回查填报。
              </Typography>
              <Typography variant="subtitle2" sx={{ mt: 2, mb: 1 }}>
                节点取数核对（系统业务日期 vs audit 上报）
              </Typography>
              <Table size="small" sx={{ minWidth: 760 }}>
                <TableHead>
                  <TableRow>
                    <TableCell>节点</TableCell>
                    <TableCell>系统业务日期</TableCell>
                    <TableCell>audit 上报</TableCell>
                    <TableCell align="right">差异(天)</TableCell>
                    <TableCell>实际采用</TableCell>
                    <TableCell>来源</TableCell>
                    <TableCell>上报/执行人</TableCell>
                    <TableCell>最新审批人</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {Object.entries(detail.nodes || {}).map(([name, n]) => (
                    <TableRow key={name}>
                      <TableCell sx={{ fontWeight: 600 }}>{name}</TableCell>
                      <TableCell sx={{ color: n.sys ? 'inherit' : 'text.disabled',
                        fontStyle: n.sys ? 'normal' : 'italic' }}>
                        {n.sys || '空'}
                      </TableCell>
                      <TableCell sx={{ color: n.audit ? 'inherit' : 'text.disabled',
                        fontStyle: n.audit ? 'normal' : 'italic' }}>
                        {n.audit || '空'}
                      </TableCell>
                      <TableCell align="right" sx={{
                        color: n.diffDays == null ? 'text.disabled'
                          : Math.abs(n.diffDays) >= 3 ? SEMANTIC.warning : 'text.secondary',
                        fontWeight: Math.abs(n.diffDays || 0) >= 3 ? 600 : 400,
                      }}
                        title="audit − 系统业务日期：正数=上报晚于单据日期（补录常态），负数=系统日期填晚了">
                        {n.diffDays == null ? '—'
                          : `${n.diffDays > 0 ? '+' : ''}${n.diffDays}`}
                      </TableCell>
                      <TableCell>{n.at || '—'}</TableCell>
                      <TableCell>{n.src || '—'}</TableCell>
                      <TableCell>{n.auditOp || n.actionOp || '—'}</TableCell>
                      <TableCell sx={{ color: n.latestOp ? 'inherit' : 'text.disabled' }}>
                        {n.latestOp || '—'}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <Typography variant="caption" color="text.secondary">
                系统业务日期 = 钉钉表单里人填的日期（人填才有，空就是空）；audit = 审批时
                带 audit 备注的上报时间，属补录，常晚于实际发生。实际采用按
                「系统 &gt; audit &gt; 动作」取 —— 所以会出现「系统空、实际却是 audit」的节点
                （如 G600 的寄送：表单没这列，只有 11-05 的补录）。看时效时以系统日期为准。
                差异(天) = audit − 系统业务日期：正数是补录常态，负数说明系统日期填晚了。
              </Typography>
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
                          sx={{ bgcolor: s.noShip ? SEMANTIC.dangerBg : 'inherit' }}>
                          <TableCell sx={{ fontFamily: 'monospace' }}>{s.trk || '—'}</TableCell>
                          <TableCell>{s.hub || '—'}</TableCell>
                          <TableCell>
                            {s.ship || '—'}
                            {s.shipAudit && (
                              <Typography variant="caption"
                                sx={{ display: 'block', color: 'text.disabled' }}>
                                {`audit ${s.shipAudit}`}
                                {s.shipGap == null ? ''
                                  : `（${s.shipGap > 0 ? '+' : ''}${s.shipGap} 天）`}
                              </Typography>
                            )}
                          </TableCell>
                          <TableCell>
                            {s.arr || '—'}
                            {s.arrAudit && (
                              <Typography variant="caption"
                                sx={{ display: 'block', color: 'text.disabled' }}>
                                {`audit ${s.arrAudit}`}
                                {s.arrGap == null ? ''
                                  : `（${s.arrGap > 0 ? '+' : ''}${s.arrGap} 天）`}
                              </Typography>
                            )}
                          </TableCell>
                          <TableCell align="right">{s.days ?? '—'}</TableCell>
                          <TableCell align="right">{s.target}</TableCell>
                          <TableCell>
                            <Typography variant="caption" sx={{
                              fontWeight: 600,
                              color: String(s.judge || '').startsWith('✗') ? SEMANTIC.danger
                                : String(s.judge || '').startsWith('✓') ? SEMANTIC.success : 'text.secondary',
                            }}>
                              {s.judge}
                            </Typography>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  <Typography variant="caption" color="text.secondary">
                    灰色 audit 行 = 这件货自己的发货/到货审批上报时间，仅作对照；运输(天) 与判定
                    仍按业务日期算。负数表示审批时间早于业务日期（业务日期填晚了）。
                  </Typography>
                </>
              )}
            </>
          )}
        </DialogContent>
      </Dialog>
    </Box>
  );
}
