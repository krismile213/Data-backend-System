import React, { useMemo, useState } from 'react';
import {
  Box, Button, Chip, MenuItem, TextField, ToggleButton, ToggleButtonGroup,
  Typography,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import PageHeader from '../components/PageHeader.jsx';
import KpiCard from '../components/KpiCard.jsx';
import SectionCard from '../components/SectionCard.jsx';
import { ErrorState, LoadingState } from '../components/StateViews.jsx';
import useApi from '../hooks/useApi.js';
import { fetchAssessment, buildAssessmentExportUrl } from '../api.js';
import { SEMANTIC } from '../theme.js';

const DIR_COLOR = { loss: SEMANTIC.danger, gain: SEMANTIC.success };

const FMT = new Intl.NumberFormat('zh-CN',
  { style: 'currency', currency: 'CNY', maximumFractionDigits: 0 });

function money(v) {
  return v == null ? '—' : FMT.format(v);
}

export default function Assessment() {
  const { data, error, loading } = useApi(() => fetchAssessment(), []);
  const [dir, setDir] = useState('all');
  const [grade, setGrade] = useState('all');

  const items = data?.items || [];
  const summary = data?.summary || {};

  const gradeOptions = useMemo(
    () => [...new Set(items.map((x) => x.grade).filter(Boolean))].sort(),
    [items]);

  const filtered = items.filter((x) =>
    (dir === 'all' || x.direction === dir)
    && (grade === 'all' || x.grade === grade));

  return (
    <>
      <PageHeader
        title="事后评估"
        subtitle={`损益三指标：时效 / 销量 / 金额（时钟起点 = ${data?.startNode || '起点'}，`
          + '推迟天数按「最晚空运到货 − 60 天底线」口径）'}
        dense
        actions={<>
          {data?.targetsProvisional && (
            <Chip size="small" color="warning" variant="outlined"
              sx={{ mr: 1 }} label="评级线为名义值（待业务校准）" />
          )}
          <Button size="small" variant="outlined" startIcon={<DownloadIcon />}
            href={buildAssessmentExportUrl()}>
            导出三指标 xlsx
          </Button>
        </>}
      />

      {loading && <LoadingState label="计算损益三指标…" />}
      {error && <ErrorState error={error} />}

      {data && !error && (
        <>
          <Box sx={{ display: 'grid',
            gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(4, 1fr)' },
            gap: 2, mb: 2 }}>
            <KpiCard label="可评估 SKU" value={summary.assessedCount ?? 0}
              accent={SEMANTIC.brand} sub="三部门全完成" />
            <KpiCard label="亏损 SKU" value={summary.lossCount ?? 0}
              color={SEMANTIC.danger} accent={SEMANTIC.danger} />
            <KpiCard label="亏损金额（区间）"
              value={`${money(summary.lossTotalMin)} ~ ${money(summary.lossTotalMax)}`}
              color={SEMANTIC.danger} sub="推迟天数 × 日均区间 × 单价" />
            <KpiCard label="增益金额（区间）"
              value={`${money(summary.gainTotalMin)} ~ ${money(summary.gainTotalMax)}`}
              color={SEMANTIC.success} sub="仅提前完成才计增益" />
          </Box>

          <SectionCard title="口径说明" sx={{ mb: 2 }}>
            <Typography variant="body2" color="text.secondary" component="div">
              ① 时钟起点 = {data?.startNode || '起点'}；② 推迟天数 = 起点→最晚空运到货 −
              60 天底线；③ 亏损 = 推迟天数 × 评级日均区间 × 单价，日均区间按评级档推算
              （「暂无销售数据」档走兜底区间并在行内标注）；④ 增益仅提前完成才算。
              金额为区间估计，非精确值。
            </Typography>
          </SectionCard>

          <SectionCard title={`三指标明细（${filtered.length}/${items.length}）`}
            actions={<Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
              <ToggleButtonGroup size="small" exclusive value={dir}
                onChange={(_, v) => v && setDir(v)}>
                <ToggleButton value="all">全部</ToggleButton>
                <ToggleButton value="loss">亏损</ToggleButton>
                <ToggleButton value="gain">增益</ToggleButton>
              </ToggleButtonGroup>
              <TextField select size="small" label="评级档" sx={{ minWidth: 110 }}
                value={grade}
                onChange={(e) => setGrade(e.target.value)}>
                <MenuItem value="all">全部档位</MenuItem>
                {gradeOptions.map((g) => (
                  <MenuItem key={g} value={g}>{g}</MenuItem>
                ))}
              </TextField>
            </Box>}>
            <Box sx={{ overflowX: 'auto' }}>
              <Box component="table" sx={{
                width: '100%', borderCollapse: 'collapse', fontSize: 13,
                '& th, & td': {
                  border: '1px solid', borderColor: 'divider', p: 1,
                  textAlign: 'left', whiteSpace: 'nowrap',
                } }}>
                <thead>
                  <tr>
                    <th>SKU</th><th>评级</th><th>方向</th><th>起点日</th>
                    <th>跨度</th><th>底线</th><th>推迟</th>
                    <th>少卖件数</th><th>损益金额</th><th>备注</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((x) => (
                    <tr key={x.sku}>
                      <td>{x.sku}</td>
                      <td>{x.grade || '—'}</td>
                      <td>
                        <Box component="span" sx={{
                          color: DIR_COLOR[x.direction] || SEMANTIC.text,
                          fontWeight: 600 }}>
                          {x.direction === 'loss' ? '亏损'
                            : x.direction === 'gain' ? '增益' : '持平'}
                        </Box>
                      </td>
                      <td>{x.startAt || '—'}</td>
                      <td>{x.spanDays ?? '—'} 天</td>
                      <td>{x.limit ?? '—'}</td>
                      <td>
                        <Box component="span" sx={{
                          color: (x.delayDays || 0) > 0
                            ? SEMANTIC.danger : SEMANTIC.success }}>
                          {x.delayDays ?? '—'} 天
                        </Box>
                      </td>
                      <td>{x.qtyMin ?? '—'} ~ {x.qtyMax ?? '—'}</td>
                      <td>{x.direction === 'gain'
                        ? `+${money(x.amountMin)} ~ +${money(x.amountMax)}`
                        : `${x.amountMin == null ? '—' : `-${money(x.amountMin)} ~ -${money(x.amountMax)}`}`}
                      </td>
                      <td>{(x.degraded || []).join('；') || (x.basis || '')}</td>
                    </tr>
                  ))}
                  {!filtered.length && (
                    <tr><td colSpan={10}>无符合条件的记录</td></tr>
                  )}
                </tbody>
              </Box>
            </Box>
          </SectionCard>
        </>
      )}
    </>
  );
}
