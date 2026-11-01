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
import UploadFileIcon from '@mui/icons-material/UploadFile';
import {
  fetchEtlScan,
  runEtlImport,
  fetchBackups,
  runBackup,
  fetchRegistryStatus,
  runRegistryImport,
} from '../api.js';
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

  // 统一请求 hook：批次扫描 + 已有备份列表 + 登记表状态一次拉取
  // （登记表接口取不到时降级为 null，不影响批次导入/备份这两块主功能）
  const { data: payload, error, refetch: load } = useApi(
    async () => {
      const [s, b, rg] = await Promise.all([
        fetchEtlScan(),
        fetchBackups(),
        fetchRegistryStatus().catch(() => null),
      ]);
      return { scan: s, backups: b.items || [], registry: rg };
    },
    [],
  );
  const scan = payload ? payload.scan : null;
  const backups = payload ? payload.backups : [];
  const registry = payload ? payload.registry : null;

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

  // 登记表导入：改完 SKU登记表.xlsx 的「人工登记」/「SKU换号」子表后点一下即可生效。
  // status 语义见 backend/etl/products_loader.import_registry：
  //   imported 已导入 / skipped 指纹未变 / warned 护栏拦截（文件·子表·表头缺失，未清库）
  const doRegistry = async (force) => {
    setBusy(force ? 'registry-force' : 'registry');
    setResult(null);
    try {
      const r = await runRegistryImport(force);
      const st = r.status;
      const head =
        st === 'imported'
          ? `登记表导入完成：人工登记 ${r.holdAfter ?? r.holdTotal} 条（原 ${r.holdBefore ?? '—'}）、`
            + `SKU换号 ${r.aliasAfter ?? r.aliasTotal} 条（原 ${r.aliasBefore ?? '—'}）`
          : st === 'skipped'
            ? '登记表指纹未变化，已跳过（未改动数据库；如需强制重导请点「强制重导」）'
            : `⚠ 登记表未导入（status=${st}）：${r.lastError || '见下方提示'}`;
      setResult({
        severity: st === 'imported' ? 'success' : st === 'skipped' ? 'info' : 'warning',
        text: `${head}，耗时 ${r.elapsed} 秒`,
        notes: r.notes || [],
        rows: [],
      });
      await load();
    } catch (e) {
      setResult({ severity: 'error', text: `登记表导入失败：${e.message}`, rows: [] });
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

      {/* 只对「批次导入」显示这条长提示：备份 / 登记表导入各有自己的按钮 spinner，
          共用 busy 的话会把 registry 当成批次名写进文案（「正在导入「registry」」）。 */}
      {busy && busy !== 'backup' && busy !== 'registry' && busy !== 'registry-force' && (
        <Alert severity="info" sx={{ mb: 2 }}>
          正在导入「{busy === 'all' ? '全部变更批次' : busy}」…… 大批次可能需要几十秒，请勿关闭页面。
        </Alert>
      )}

      {result && (
        <Alert severity={result.severity} sx={{ mb: 2 }} onClose={() => setResult(null)}>
          <Typography variant="body2">{result.text}</Typography>
          {(result.notes || []).length > 0 && (
            <Box sx={{ mt: 1 }}>
              {result.notes.map((n, i) => (
                <Typography key={i} variant="caption" sx={{ display: 'block' }}>
                  · {n}
                </Typography>
              ))}
            </Box>
          )}
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
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
          <Typography variant="subtitle2">登记表（SKU换号 / 人工豁免）</Typography>
          <Box sx={{ flex: 1 }} />
          <Tooltip title="改完 SKU登记表.xlsx 的『人工登记』或『SKU换号』子表后点这里，立即生效（无需重启服务）">
            <span>
              <Button
                size="small"
                variant="contained"
                startIcon={busy === 'registry' ? <CircularProgress size={14} color="inherit" /> : <UploadFileIcon />}
                onClick={() => doRegistry(false)}
                disabled={!!busy || (registry && registry.xlsxExists === false)}
              >
                导入登记表
              </Button>
            </span>
          </Tooltip>
          <Tooltip title="文件指纹没变时会被跳过；改过内容但想强制重导（或怀疑库里数据不对）时用这个">
            <span>
              <Button
                size="small"
                variant="outlined"
                startIcon={busy === 'registry-force' ? <CircularProgress size={14} color="inherit" /> : <RefreshIcon />}
                onClick={() => doRegistry(true)}
                disabled={!!busy || (registry && registry.xlsxExists === false)}
              >
                强制重导
              </Button>
            </span>
          </Tooltip>
        </Stack>

        {!registry && (
          <Typography variant="caption" color="text.secondary">
            登记表状态接口不可用（/api/etl/registry）—— 批次导入与备份不受影响。
          </Typography>
        )}

        {registry && (
          <Box>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
              文件：{registry.xlsxPath}
              {registry.xlsxExists === false && (
                <Box component="span" sx={{ color: SEMANTIC.danger, fontWeight: 600 }}>
                  　（文件不存在）
                </Box>
              )}
            </Typography>
            <Stack direction="row" spacing={3} sx={{ mt: 0.5, flexWrap: 'wrap' }}>
              <Typography variant="caption">
                人工登记 <b>{registry.holdTotal}</b> 条
                <Box component="span" sx={{ color: 'text.secondary' }}>
                  　（子表「{registry.holdSheet}」，命中即不催办）
                </Box>
              </Typography>
              <Typography variant="caption">
                SKU换号 <b>{registry.aliasTotal}</b> 条
                <Box component="span" sx={{ color: 'text.secondary' }}>
                  　（子表「{registry.aliasSheet}」）
                </Box>
              </Typography>
              <Typography variant="caption" color="text.secondary">
                上次导入：{registry.importedAt || '从未导入'}
              </Typography>
            </Stack>
            {(registry.notes || []).slice(0, 6).map((n, i) => (
              <Typography
                key={i}
                variant="caption"
                sx={{ display: 'block', mt: 0.5, color: 'text.secondary' }}
              >
                · {n}
              </Typography>
            ))}
            {registry.lastError && (
              <Typography variant="caption" sx={{ display: 'block', mt: 0.5, color: SEMANTIC.danger }}>
                {registry.lastError}
              </Typography>
            )}
          </Box>
        )}
      </Paper>

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
