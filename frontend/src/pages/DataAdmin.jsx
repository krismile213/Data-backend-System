import React, { useState } from 'react';
import { LoadingState, ErrorState, EmptyState } from '../components/StateViews.jsx';
import { SEMANTIC } from '../theme.js';
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
  CircularProgress,
  Alert,
  Button,
  Tooltip,
  Stack,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import CloudUploadIcon from '@mui/icons-material/CloudUpload';
import BackupIcon from '@mui/icons-material/Backup';
import { fetchEtlScan, runEtlImport, fetchBackups, runBackup } from '../api.js';
import KpiCard from '../components/KpiCard.jsx';
import PageHeader from '../components/PageHeader.jsx';
import useApi from '../hooks/useApi.js';

const STATE_META = {
  new: { label: '待首次导入', color: 'info', hint: '磁盘上有该批次，数据库里还没有' },
  changed: { label: '文件已变更', color: 'warning', hint: 'Excel 更新过，需要重新导入' },
  imported: { label: '已是最新', color: 'success', hint: '文件指纹与上次导入一致' },
  missing: { label: '数据源缺失', color: 'error', hint: '库里记录过，但磁盘上已找不到该批次目录' },
};

export default function DataAdmin() {
  const [busy, setBusy] = useState('');
  const [result, setResult] = useState(null);

  // 统一请求 hook：批次扫描 + 已有备份列表一次拉取
  const { data: payload, error, refetch: load } = useApi(
    async () => {
      const [s, b] = await Promise.all([fetchEtlScan(), fetchBackups()]);
      return { scan: s, backups: b.items || [] };
    },
    [],
  );
  const scan = payload ? payload.scan : null;
  const backups = payload ? payload.backups : [];

  const doImport = async (batch, force) => {
    setBusy(batch || 'all');
    setResult(null);
    try {
      const r = await runEtlImport(batch, force);
      setResult({
        severity: r.failed ? 'warning' : 'success',
        text: `导入完成：更新 ${r.imported} 个、跳过 ${r.skipped} 个、失败 ${r.failed} 个，耗时 ${r.elapsed} 秒`,
        rows: r.results,
      });
      await load();
    } catch (e) {
      setResult({ severity: 'error', text: `导入失败：${e.message}`, rows: [] });
    } finally {
      setBusy('');
    }
  };

  const doBackup = async () => {
    setBusy('backup');
    setResult(null);
    try {
      const r = await runBackup();
      setResult({
        severity: r.dumpStatus === 'failed' ? 'warning' : 'success',
        text: `备份完成：${r.path}（state ${r.stateFiles.length} 个文件，数据库导出 ${r.dumpStatus}）`,
        rows: [],
      });
      await load();
    } catch (e) {
      setResult({ severity: 'error', text: `备份失败：${e.message}`, rows: [] });
    } finally {
      setBusy('');
    }
  };

  if (error && !scan) return <ErrorState prefix="数据管理加载失败" error={error} />;
  if (!scan) return <LoadingState />;

  const activeBatches = scan.items.filter((i) => i.state !== 'missing').length;

  return (
    <Box>
      <PageHeader
        title="数据管理"
        subtitle={`扫描数据目录 · 一键导入批次 · 备份手工数据　|　数据根目录 ${scan.dataRoot}`}
        actions={
          <Stack direction="row" spacing={1}>
            <Button variant="outlined" startIcon={<RefreshIcon />} onClick={load} disabled={!!busy}>
              重新扫描
            </Button>
            <Button
              variant="contained"
              startIcon={busy === 'all' ? <CircularProgress size={16} color="inherit" /> : <CloudUploadIcon />}
              onClick={() => doImport(null, false)}
              disabled={!!busy || scan.pendingCount === 0}
            >
              导入全部变更（{scan.pendingCount}）
            </Button>
          </Stack>
        }
      />

      {!scan.dataRootExists && (
        <Alert severity="error" sx={{ mb: 2 }}>
          数据根目录不存在：{scan.dataRoot} —— 请确认数据盘已就绪，或在 .env 中修正 DATA_ROOT。
        </Alert>
      )}

      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid item xs={6} md={3}>
          <KpiCard label="扫描到批次" value={activeBatches} sub={`共 ${scan.items.length} 条批次记录`} />
        </Grid>
        <Grid item xs={6} md={3}>
          <KpiCard
            label="待导入 / 待更新"
            value={scan.pendingCount}
            sub={scan.pending.join('、') || '所有批次均已是最新'}
            color={scan.pendingCount ? SEMANTIC.warning : SEMANTIC.text}
          />
        </Grid>
        <Grid item xs={6} md={3}>
          <KpiCard label="库内单据" value={scan.totalDocs.toLocaleString()} sub="part_doc 表" />
        </Grid>
        <Grid item xs={6} md={3}>
          <KpiCard label="库内审批节点" value={scan.totalNodes.toLocaleString()} sub="approval_node 表" />
        </Grid>
      </Grid>

      {busy && busy !== 'backup' && (
        <Alert severity="info" sx={{ mb: 2 }}>
          正在导入「{busy === 'all' ? '全部变更批次' : busy}」…… 大批次可能需要几十秒，请勿关闭页面。
        </Alert>
      )}

      {result && (
        <Alert severity={result.severity} sx={{ mb: 2 }} onClose={() => setResult(null)}>
          <Typography variant="body2">{result.text}</Typography>
          {result.rows.length > 0 && (
            <Box sx={{ mt: 1 }}>
              {result.rows.map((r) => (
                <Typography key={r.batch} variant="caption" sx={{ display: 'block' }}>
                  · {r.batch}：{r.status}
                  {r.docCount ? `　${r.docCount} 单据` : ''}
                  {r.nodeCount ? ` / ${r.nodeCount} 节点` : ''}
                  {r.message ? `　${r.message}` : ''}
                </Typography>
              ))}
            </Box>
          )}
        </Alert>
      )}

      <Paper sx={{ p: 2, mb: 2 }}>
        <Typography variant="subtitle2" gutterBottom>批次导入状态</Typography>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>批次</TableCell>
                <TableCell>状态</TableCell>
                <TableCell align="right">文件数</TableCell>
                <TableCell align="right">库内单据</TableCell>
                <TableCell>上次导入</TableCell>
                <TableCell align="right">操作</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {scan.items.map((it) => {
                const meta = STATE_META[it.state] || {};
                const actionable = it.state === 'new' || it.state === 'changed';
                return (
                  <TableRow key={it.name} hover>
                    <TableCell>
                      <Typography variant="body2"><b>{it.name}</b></Typography>
                      {it.parts.length > 0 && (
                        <Typography variant="caption" color="text.secondary">
                          {it.parts.join(' ')}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <Tooltip title={meta.hint || ''}>
                        <Chip size="small" color={meta.color || 'default'} label={meta.label || it.state} />
                      </Tooltip>
                    </TableCell>
                    <TableCell align="right">{it.fileCount || '—'}</TableCell>
                    <TableCell align="right">
                      {it.docCount ? it.docCount.toLocaleString() : '—'}
                    </TableCell>
                    <TableCell>{it.importedAt || '—'}</TableCell>
                    <TableCell align="right">
                      <Stack direction="row" spacing={1} justifyContent="flex-end">
                        <Button
                          size="small"
                          variant={actionable ? 'contained' : 'outlined'}
                          disabled={!!busy || it.state === 'missing'}
                          startIcon={busy === it.name ? <CircularProgress size={14} color="inherit" /> : null}
                          onClick={() => doImport(it.name, false)}
                        >
                          导入
                        </Button>
                        <Tooltip title="忽略文件指纹，整批删除后重新导入">
                          <span>
                            <Button
                              size="small"
                              color="warning"
                              disabled={!!busy || it.state === 'missing'}
                              onClick={() => doImport(it.name, true)}
                            >
                              强制重导
                            </Button>
                          </span>
                        </Tooltip>
                      </Stack>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
          导入为覆盖式：同批次先删后插，文件指纹未变时自动跳过。「强制重导」用于 Excel 内容改动
          但修改时间未变的场景。
        </Typography>
      </Paper>

      <Paper sx={{ p: 2 }}>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', mb: 1 }}>
          <Box>
            <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>数据备份</Typography>
            <Typography variant="caption" color="text.secondary">
              备份 backend/state（手工填报 + KPI 快照，无法从 Excel 重建）与数据库导出，存放于 backups/
            </Typography>
          </Box>
          <Button
            variant="contained"
            startIcon={busy === 'backup' ? <CircularProgress size={16} color="inherit" /> : <BackupIcon />}
            onClick={doBackup}
            disabled={!!busy}
          >
            立即备份
          </Button>
        </Box>

        {backups.length === 0 ? (
          <EmptyState>还没有备份记录 —— 建议每次批次导入后点一次「立即备份」。</EmptyState>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>备份时间</TableCell>
                  <TableCell>目录</TableCell>
                  <TableCell>包含文件</TableCell>
                  <TableCell align="right">大小</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {backups.map((b) => (
                  <TableRow key={b.name} hover>
                    <TableCell>{b.createdAt}</TableCell>
                    <TableCell><code>{b.name}</code></TableCell>
                    <TableCell>
                      <Typography variant="caption">{b.files.join('、')}</Typography>
                    </TableCell>
                    <TableCell align="right">{b.sizeKB.toLocaleString()} KB</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>
    </Box>
  );
}
