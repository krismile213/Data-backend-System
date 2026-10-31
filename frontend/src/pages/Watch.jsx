import React, { useState } from 'react';
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
  Chip,
  Button,
  Tooltip,
} from '@mui/material';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import CheckIcon from '@mui/icons-material/Check';
import { fetchWatch } from '../api.js';
import KpiCard from '../components/KpiCard.jsx';
import PageHeader from '../components/PageHeader.jsx';
import { LoadingState, ErrorState, EmptyState } from '../components/StateViews.jsx';
import { SEMANTIC } from '../theme.js';
import useApi from '../hooks/useApi.js';

/** 停滞天数 → Chip 色：≥3×阈值 红、≥阈值 橙、其余中性 */
function stallColor(d, threshold) {
  if (d === null || d === undefined) return 'default';
  if (d >= 3 * threshold) return 'error';
  if (d >= threshold) return 'warning';
  return 'default';
}

export default function Watch() {
  const { data, error, loading } = useApi(() => fetchWatch(), []);
  const [copied, setCopied] = useState('');
  const [copyErr, setCopyErr] = useState('');

  const copyText = async (owner, text) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(owner);
      setCopyErr('');
      setTimeout(() => setCopied(''), 2000);
    } catch (e) {
      // 剪贴板不可用（非 https / 无权限）时降级提示用户手动复制
      setCopyErr('复制失败，请手动选中文本复制');
    }
  };

  if (error && !data) return <ErrorState prefix="联动预警加载失败" error={error} />;
  if (!data) return <LoadingState />;
  const th = data.thresholdDays;

  return (
    <Box>
      <PageHeader
        title="跨环节联动预警 · 页面内版"
        subtitle={`Part5 发货停滞 → 卡 Part6 素材 → 页面 coming soon。素材与发货解耦，按人提醒、一键复制文案 —— 数据时间 ${data.computedAt}`}
        actions={
          copyErr ? (
            <Typography variant="caption" sx={{ color: SEMANTIC.danger }}>
              {copyErr}
            </Typography>
          ) : null
        }
      />

      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid item xs={6} md={3}>
          <KpiCard
            label="联动提醒 SKU"
            value={data.kpi.watchCount}
            sub={`R1 可启动素材 ${data.kpi.r1Count} · R2 签样缺失 ${data.kpi.r2Count}`}
          />
        </Grid>
        <Grid item xs={6} md={3}>
          <KpiCard label="涉及责任人" value={data.kpi.ownerCount} sub="按实际执行人分组" />
        </Grid>
        <Grid item xs={6} md={3}>
          <KpiCard
            label="最长发货停滞"
            value={`${data.kpi.maxStallDays} 天`}
            sub={`提醒阈值 ${th} 天（规则 stuck_days）`}
            color={SEMANTIC.warning}
          />
        </Grid>
        <Grid item xs={6} md={3}>
          <KpiCard
            label="严重停滞（≥3×阈值）"
            value={data.kpi.severeCount}
            color={data.kpi.severeCount ? SEMANTIC.danger : SEMANTIC.text}
            sub="优先处理"
          />
        </Grid>
      </Grid>

      {data.items.length === 0 ? (
        <EmptyState severity="success">
          当前没有需要联动的 SKU —— 发货停滞超 {th} 天且素材未启动的清单为空。
        </EmptyState>
      ) : (
        <>
          {/* 按责任人分组：一键复制提醒文案 */}
          <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 1 }}>
            按责任人复制提醒文案
          </Typography>
          <Grid container spacing={2} sx={{ mb: 3 }}>
            {data.groups.map((g) => (
              <Grid item xs={12} md={6} key={g.owner}>
                <Paper sx={{ p: 2, height: '100%' }}>
                  <Box
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      mb: 1,
                    }}
                  >
                    <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
                      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                        {g.owner}
                      </Typography>
                      <Chip size="small" label={`${g.count} 个 SKU`} />
                    </Box>
                    <Button
                      size="small"
                      variant="contained"
                      startIcon={copied === g.owner ? <CheckIcon /> : <ContentCopyIcon />}
                      onClick={() => copyText(g.owner, g.remindText)}
                    >
                      {copied === g.owner ? '已复制' : '复制文案'}
                    </Button>
                  </Box>
                  <Paper
                    variant="outlined"
                    sx={{
                      p: 1.5,
                      bgcolor: SEMANTIC.codeBg,
                      whiteSpace: 'pre-wrap',
                      fontFamily: 'monospace',
                      fontSize: 12,
                    }}
                  >
                    {g.remindText}
                  </Paper>
                </Paper>
              </Grid>
            ))}
          </Grid>

          {/* 明细表 */}
          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2" gutterBottom>
              明细（按停滞天数降序）
            </Typography>
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>类型</TableCell>
                    <TableCell>SKU / 型号</TableCell>
                    <TableCell>发货停滞</TableCell>
                    <TableCell>责任人</TableCell>
                    <TableCell>判定说明</TableCell>
                    <TableCell>建议动作</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {data.items.map((it) => (
                    <TableRow key={`${it.kind}-${it.sku}`} hover>
                      <TableCell>
                        <Chip
                          size="small"
                          color={it.kind === 'R1' ? 'primary' : 'warning'}
                          label={it.kind}
                        />
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2">
                          <b>{it.sku}</b>　{it.model}
                        </Typography>
                        {it.hubs.length > 0 && (
                          <Typography variant="caption" color="text.secondary">
                            渠道：{it.hubs.join('、')}
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell>
                        <Tooltip title={`阈值 ${th} 天`}>
                          <Chip
                            size="small"
                            color={stallColor(it.stallDays, th)}
                            label={
                              it.stallDays !== null && it.stallDays !== undefined
                                ? `${it.stallDays} 天`
                                : '—'
                            }
                          />
                        </Tooltip>
                      </TableCell>
                      <TableCell>{it.owner}</TableCell>
                      <TableCell sx={{ maxWidth: 320 }}>
                        <Typography variant="body2">{it.why}</Typography>
                      </TableCell>
                      <TableCell sx={{ maxWidth: 260 }}>
                        <Typography variant="body2">{it.action}</Typography>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </Paper>
        </>
      )}

      <Paper sx={{ p: 2, mt: 2 }}>
        <Typography variant="subtitle2" gutterBottom>
          判定口径（可核对）
        </Typography>
        {data.rules.map((r) => (
          <Typography key={r.key} variant="body2" color="text.secondary">
            · <b>{r.key}</b>：{r.rule}
          </Typography>
        ))}
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
          后续接通知渠道（钉钉机器人/邮件）后，此清单可直接推送；数据口径不变。
        </Typography>
      </Paper>
    </Box>
  );
}
