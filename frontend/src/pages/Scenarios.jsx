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
  Chip,
  CircularProgress,
  Alert,
  Button,
  Tabs,
  Tab,
  Tooltip,
  Divider,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import { fetchScenarios, buildScenariosExportUrl } from '../api.js';

function KpiCard({ label, value, sub, color = '#1a2740' }) {
  return (
    <Paper sx={{ p: 2, height: '100%' }}>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography variant="h5" sx={{ fontWeight: 700, color, mt: 0.5 }}>{value}</Typography>
      {sub && <Typography variant="caption" color="text.secondary">{sub}</Typography>}
    </Paper>
  );
}

/** 停滞天数 → 色标：≥60 红、≥30 橙、其余灰 */
function stallColor(d) {
  if (d === null || d === undefined || d === '') return 'default';
  if (d >= 60) return 'error';
  if (d >= 30) return 'warning';
  return 'default';
}

export default function Scenarios() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [tab, setTab] = useState('ALL');
  const [q, setQ] = useState('');

  useEffect(() => {
    fetchScenarios().then(setData).catch((e) => setError(e.message));
  }, []);

  const overlapMap = useMemo(() => {
    const m = {};
    (data?.overlap || []).forEach((o) => { m[o.sku] = o.codes; });
    return m;
  }, [data]);

  const groups = useMemo(() => {
    if (!data) return [];
    return data.groups
      .filter((g) => tab === 'ALL' || g.code === tab)
      .map((g) => ({
        ...g,
        items: g.items.filter((it) => {
          const kw = q.trim().toLowerCase();
          if (!kw) return true;
          return it.sku.toLowerCase().includes(kw)
            || (it.model || '').toLowerCase().includes(kw);
        }),
      }));
  }, [data, tab, q]);

  if (error) return <Alert severity="error">场景诊断加载失败：{error}</Alert>;
  if (!data) return <Box sx={{ display: 'flex', justifyContent: 'center', mt: 8 }}><CircularProgress /></Box>;

  const countOf = (code) => (data.summary.find((s) => s.code === code) || {}).count || 0;

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
        <Box>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>
            跨环节联动诊断 · 场景 A/B/C
          </Typography>
          <Typography variant="caption" color="text.secondary">
            把「coming soon 待去化」的 SKU 直接列出来 —— 数据时间 {data.computedAt}
          </Typography>
        </Box>
        <Button
          variant="outlined"
          startIcon={<DownloadIcon />}
          href={buildScenariosExportUrl(tab === 'ALL' ? '' : tab)}
        >
          导出{tab === 'ALL' ? '全部' : `场景${tab}`} xlsx
        </Button>
      </Box>

      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid item xs={12} sm={6} md={4}>
          <KpiCard label="纳入场景的 SKU" value={data.involvedSkus} sub={`其中未闭环 ${data.coveredOpenSkus} 个`} />
        </Grid>
        <Grid item xs={12} sm={6} md={4}>
          <KpiCard label="未闭环 / 全部 SKU" value={`${data.openSkus} / ${data.totalSkus}`} sub="闭环 = 三部门终节点全完成" />
        </Grid>
        <Grid item xs={12} sm={4} md={4}>
          <KpiCard label="场景 A · 已首上架·素材未完成" value={countOf('A')} color="#d32f2f" sub="最易 coming soon" />
        </Grid>
        <Grid item xs={12} sm={4} md={4}>
          <KpiCard label="场景 B · 素材完成·最终上架未完成" value={countOf('B')} color="#ed6c02" sub="接近 Ready，只差最后一步" />
        </Grid>
        <Grid item xs={12} sm={4} md={4}>
          <KpiCard label="场景 C · Part5 发货未完成" value={countOf('C')} color="#0277bd" sub="上游停滞，连锁拖住素材/上架" />
        </Grid>
      </Grid>

      <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', mb: 1 }}>
        <Tabs value={tab} onChange={(e, v) => setTab(v)}>
          <Tab value="ALL" label={`全部 ${data.involvedSkus}`} />
          {data.summary.map((s) => (
            <Tab key={s.code} value={s.code} label={`${s.code} · ${s.count}`} />
          ))}
        </Tabs>
        <TextField
          size="small"
          placeholder="按 SKU 或型号搜索"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          sx={{ width: 220 }}
        />
      </Box>

      {groups.map((g) => (
        <Paper key={g.code} sx={{ p: 2, mb: 2 }}>
          <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1.5, flexWrap: 'wrap' }}>
            <Chip size="small" label={`场景 ${g.code}`} sx={{ bgcolor: g.color, color: '#fff', fontWeight: 700 }} />
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>{g.name}</Typography>
            <Typography variant="caption" color="text.secondary">
              {g.items.length} 个 SKU · 最长停滞 {g.items.length ? Math.max(...g.items.map((i) => i.sinceDays || 0)) : 0} 天
            </Typography>
          </Box>
          <Typography variant="body2" sx={{ mt: 0.5 }}>
            <b>影响：</b>{g.impact}
          </Typography>
          <Typography variant="body2" sx={{ color: '#2e7d32' }}>
            <b>去化路径：</b>{g.path}　<b>责任方：</b>{g.owner}
          </Typography>

          <Divider sx={{ my: 1.5 }} />

          {g.items.length === 0 ? (
            <Typography variant="body2" color="text.secondary">当前筛选下无匹配 SKU。</Typography>
          ) : (
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>SKU</TableCell>
                    <TableCell>型号 / 开发类型</TableCell>
                    <TableCell>停滞</TableCell>
                    <TableCell>首次上架</TableCell>
                    <TableCell>素材完成</TableCell>
                    <TableCell>最终上架</TableCell>
                    <TableCell>头号卡点</TableCell>
                    <TableCell>判定说明</TableCell>
                    <TableCell>建议动作</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {g.items.map((it) => {
                    const st = it.stage || {};
                    return (
                      <TableRow key={it.sku} hover>
                        <TableCell>
                          <b>{it.sku}</b>
                          <br />
                          <Chip
                            size="small"
                            variant="outlined"
                            color={it.valid ? 'success' : 'error'}
                            label={it.valid ? '已闭环' : '未闭环'}
                          />
                          {(overlapMap[it.sku] || []).length > 1 && (
                            <Tooltip title={`同时命中场景 ${overlapMap[it.sku].join(' + ')}，属于叠加风险`}>
                              <Chip size="small" sx={{ ml: 0.5 }} label={overlapMap[it.sku].join('+')} />
                            </Tooltip>
                          )}
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2">{it.model || '—'}</Typography>
                          <Typography variant="caption" color="text.secondary">{it.devType}</Typography>
                        </TableCell>
                        <TableCell>
                          <Tooltip title={`${it.sinceLabel}：${it.sinceAt || '—'}`}>
                            <Chip
                              size="small"
                              color={stallColor(it.sinceDays)}
                              label={it.sinceDays !== null && it.sinceDays !== undefined ? `${it.sinceDays} 天` : '—'}
                            />
                          </Tooltip>
                        </TableCell>
                        <TableCell>{st['首次上架'] || '—'}</TableCell>
                        <TableCell>{st['素材加工'] || '—'}</TableCell>
                        <TableCell>{st['最终上架'] || '—'}</TableCell>
                        <TableCell>
                          {it.bottleneck ? (
                            <>
                              <Typography variant="body2">{it.bottleneck.seg}</Typography>
                              <Typography variant="caption" color="text.secondary">
                                {it.bottleneck.dept} · {it.bottleneck.days} 天
                              </Typography>
                            </>
                          ) : '—'}
                        </TableCell>
                        <TableCell sx={{ maxWidth: 300 }}>
                          <Typography variant="body2">{it.why}</Typography>
                          {it.hubs && it.hubs.length > 0 && (
                            <Typography variant="caption" color="text.secondary">
                              渠道：{it.hubs.join('、')}
                            </Typography>
                          )}
                        </TableCell>
                        <TableCell sx={{ maxWidth: 280 }}>
                          <Typography variant="body2">{it.action}</Typography>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </Paper>
      ))}

      <Paper sx={{ p: 2 }}>
        <Typography variant="subtitle2" gutterBottom>判定口径（可核对）</Typography>
        {data.rules.map((r) => (
          <Typography key={r.key} variant="body2" color="text.secondary">
            · <b>{r.key}</b>：{r.rule}
          </Typography>
        ))}
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
          口径来源：流程复盘/流程优化建议.md（核心结论：Part5 发货停滞是链条断点 → 连锁卡住 Part6 素材 → 页面 coming soon）。
        </Typography>
      </Paper>
    </Box>
  );
}
