import React, { useEffect, useMemo, useState } from 'react';
import { LoadingState, ErrorState, EmptyState } from '../components/StateViews.jsx';
import { SEMANTIC } from '../theme.js';
import {
  Autocomplete,
  Box,
  Card,
  CardContent,
  Checkbox,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  TextField,
  Typography,
} from '@mui/material';
import { fetchCompare } from '../api.js';
import EChart from '../components/EChart.jsx';
import useApi from '../hooks/useApi.js';

/** 小时数格式化（与 Dashboard 一致） */
function fmtHours(h) {
  if (h === null || h === undefined) return '—';
  if (h >= 24) return `${(h / 24).toFixed(1)} 天`;
  return `${h.toFixed(1)} 小时`;
}

const BATCH_COLORS = [SEMANTIC.brand, SEMANTIC.danger, SEMANTIC.success, SEMANTIC.warning, SEMANTIC.purple, SEMANTIC.info];

/** KPI 对比行定义 */
const KPI_ROWS = [
  {
    label: '总单据数',
    get: (b) => b.kpi.total,
    render: (v) => v,
  },
  {
    label: '已完成 / 完成率',
    get: (b) => b.kpi.finished,
    render: (v, b) => `${v}（${(b.kpi.finishRate * 100).toFixed(1)}%）`,
  },
  {
    label: '审批中',
    get: (b) => b.kpi.running,
    render: (v) => v,
  },
  {
    label: '终止',
    get: (b) => b.kpi.terminated,
    render: (v) => v,
  },
  {
    label: '平均全流程耗时（已结束）',
    get: (b) => b.kpi.avgTotalHours,
    render: (v) => fmtHours(v),
  },
  {
    label: '异常单据数',
    get: (b) => b.kpi.anomalyDocCount,
    render: (v, b) => `${v}（${b.kpi.anomalyCount} 条记录）`,
  },
];

export default function Compare({ batches }) {
  const [selected, setSelected] = useState([]);

  // 批次列表就绪后，默认选最近两个批次
  useEffect(() => {
    if (batches.length >= 2 && selected.length === 0) {
      setSelected(batches.slice(-2).map((b) => b.name));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batches]);

  // 统一请求 hook：选中不足两个批次时不发请求，竞态由 hook 内部序号防护
  const { data, error, loading, setData } = useApi(
    () => fetchCompare(selected),
    [selected],
    { enabled: selected.length >= 2 },
  );

  // 选择不足两个批次时，清掉上一次的对比结果
  useEffect(() => {
    if (selected.length < 2) setData(null);
  }, [selected, setData]);

  // ---- 各 Part 平均耗时对比（分组柱状图） ----
  const partBarOption = useMemo(() => {
    if (!data) return null;
    const first = data.batches[0].partBar.map((p) => ({
      part: p.part,
      label: `${p.part} ${p.name}`,
    }));
    return {
      title: { text: '各 Part 全流程平均耗时对比（已结束单据，小时）', left: 'center', textStyle: { fontSize: 15 } },
      tooltip: { trigger: 'axis', valueFormatter: (v) => (v == null ? '—' : `${v} 小时`) },
      legend: { top: 28 },
      grid: { top: 70, left: 60, right: 30, bottom: 80 },
      xAxis: { type: 'category', data: first.map((p) => p.label), axisLabel: { rotate: 30, fontSize: 11 } },
      yAxis: { type: 'value', name: '小时' },
      series: data.batches.map((b, i) => ({
        name: b.batch,
        type: 'bar',
        data: first.map((p) => {
          const hit = b.partBar.find((x) => x.part === p.part);
          return hit ? hit.avg : null;
        }),
        itemStyle: { color: BATCH_COLORS[i % BATCH_COLORS.length] },
      })),
    };
  }, [data]);

  // ---- 审批状态分布对比（分组柱状图） ----
  const statusOption = useMemo(() => {
    if (!data) return null;
    const statusSet = [];
    data.batches.forEach((b) => {
      b.statusPie.forEach((s) => {
        if (!statusSet.includes(s.name)) statusSet.push(s.name);
      });
    });
    return {
      title: { text: '审批状态分布对比（张数）', left: 'center', textStyle: { fontSize: 15 } },
      tooltip: { trigger: 'axis' },
      legend: { top: 28 },
      grid: { top: 70, left: 60, right: 30, bottom: 40 },
      xAxis: { type: 'category', data: statusSet },
      yAxis: { type: 'value', name: '单据数', minInterval: 1 },
      series: data.batches.map((b, i) => ({
        name: b.batch,
        type: 'bar',
        data: statusSet.map((st) => {
          const hit = b.statusPie.find((x) => x.name === st);
          return hit ? hit.value : 0;
        }),
        itemStyle: { color: BATCH_COLORS[i % BATCH_COLORS.length] },
      })),
    };
  }, [data]);

  if (batches.length < 2) {
    return <EmptyState>数据目录下至少需要 2 个批次才能对比</EmptyState>;
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {/* 批次选择 */}
      <Card>
        <CardContent>
          <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 1.5 }}>
            <Typography variant="subtitle1">选择要对比的批次（2~6 个）：</Typography>
            <Autocomplete
              multiple
              size="small"
              sx={{ minWidth: 480 }}
              options={batches.map((b) => b.name)}
              value={selected}
              onChange={(_, v) => setSelected(v)}
              disableCloseOnSelect
              renderOption={(props, option, { selected: sel }) => (
                <li {...props} key={option}>
                  <Checkbox size="small" style={{ marginRight: 4 }} checked={sel} />
                  {option}
                </li>
              )}
              renderInput={(params) => <TextField {...params} placeholder="搜索/选择批次" />}
            />
          </Box>
        </CardContent>
      </Card>

      {selected.length < 2 && (
        <EmptyState>请至少选择 2 个批次</EmptyState>
      )}

      {loading && <LoadingState sx={{ mt: 6 }} />}

      {error && <ErrorState prefix="加载失败" error={error} />}

      {!loading && !error && data && selected.length >= 2 && (
        <>
          {/* KPI 对比表 */}
          <Card>
            <CardContent>
              <TableContainer component={Paper}>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>指标</TableCell>
                      {data.batches.map((b, i) => (
                        <TableCell key={b.batch} align="center">
                          <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 1 }}>
                            <Box sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: BATCH_COLORS[i % BATCH_COLORS.length] }} />
                            {b.batch}
                          </Box>
                        </TableCell>
                      ))}
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {KPI_ROWS.map((row) => (
                      <TableRow key={row.label} hover>
                        <TableCell>{row.label}</TableCell>
                        {data.batches.map((b) => (
                          <TableCell key={b.batch} align="center" fontWeight={600}>
                            {row.render(row.get(b), b)}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                各批次数据基准时间：
                {data.batches.map((b) => `${b.batch} ${b.baseTime || '—'}`).join(' ｜ ')}
              </Typography>
            </CardContent>
          </Card>

          {/* Part 耗时对比 */}
          <Card>
            <CardContent>
              {partBarOption ? <EChart option={partBarOption} height={420} /> : <EmptyState>暂无时效数据</EmptyState>}
            </CardContent>
          </Card>

          {/* 状态分布对比 */}
          <Card>
            <CardContent>
              {statusOption ? <EChart option={statusOption} height={380} /> : <EmptyState>暂无状态数据</EmptyState>}
            </CardContent>
          </Card>
        </>
      )}
    </Box>
  );
}
