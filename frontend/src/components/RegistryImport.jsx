import React, { useState } from 'react';
import {
  Alert, Box, Button, CircularProgress, Paper, Stack, Tooltip, Typography,
} from '@mui/material';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import RefreshIcon from '@mui/icons-material/Refresh';
import { SEMANTIC } from '../theme.js';
import { runRegistryImport } from '../api.js';

/**
 * 登记表导入卡（SKU换号 / 人工登记=人工豁免）。
 *
 * 为什么放在「事中日报」页的次 sheet 而不是「数据管理」页：
 * 这两张子表直接决定日报里哪些 SKU「不催办」（命中即移出追踪），
 * 属于日报口径的一部分；数据管理页管的是批次 ETL 与备份，两件事不同源。
 *
 * 状态语义见 backend/etl/products_loader.import_registry：
 *   imported 已导入 / skipped 指纹未变（没动库）/ warned 护栏拦截（未清库）
 *
 * props:
 *   registry  后端 /api/etl/registry 的状态对象（可为 null = 接口不可用）
 *   onDone    导入成功后回调（刷新状态）
 */
export default function RegistryImport({ registry, onDone }) {
  const [busy, setBusy] = useState('');
  const [result, setResult] = useState(null);

  const doImport = async (force) => {
    setBusy(force ? 'force' : 'normal');
    setResult(null);
    try {
      const r = await runRegistryImport(force);
      const st = r.status;
      const head =
        st === 'imported'
          ? `导入完成：人工登记 ${r.holdAfter ?? r.holdTotal} 条（原 ${r.holdBefore ?? '—'}）、`
            + `SKU换号 ${r.aliasAfter ?? r.aliasTotal} 条（原 ${r.aliasBefore ?? '—'}）`
          : st === 'skipped'
            ? '登记表指纹未变化，已跳过（未改动数据库；如需强制重导请点「强制重导」）'
            : `⚠ 登记表未导入（status=${st}）：${r.lastError || '见下方提示'}`;
      setResult({
        severity: st === 'imported' ? 'success' : st === 'skipped' ? 'info' : 'warning',
        text: `${head}，耗时 ${r.elapsed} 秒`,
        notes: r.notes || [],
      });
      if (onDone) await onDone();
    } catch (e) {
      setResult({ severity: 'error', text: `登记表导入失败：${e.message || e}`, notes: [] });
    } finally {
      setBusy('');
    }
  };

  const disabled = !!busy || (registry && registry.xlsxExists === false);

  return (
    <Paper sx={{ p: 2 }}>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
        <Typography variant="subtitle2">登记表（SKU换号 / 人工豁免）</Typography>
        <Box sx={{ flex: 1 }} />
        <Tooltip title="改完 SKU登记表.xlsx 的『人工登记』或『SKU换号』子表后点这里，立即生效（无需重启服务）">
          <span>
            <Button
              size="small"
              variant="contained"
              startIcon={busy === 'normal' ? <CircularProgress size={14} color="inherit" /> : <UploadFileIcon />}
              onClick={() => doImport(false)}
              disabled={disabled}
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
              startIcon={busy === 'force' ? <CircularProgress size={14} color="inherit" /> : <RefreshIcon />}
              onClick={() => doImport(true)}
              disabled={disabled}
            >
              强制重导
            </Button>
          </span>
        </Tooltip>
      </Stack>

      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
        「人工登记」= 业务上不再走完流程（渠道取消 / 并入其它型号 / 末端实际不执行），
        命中即移出追踪、不催办，并进日报「豁免备查」；「SKU换号」= 旧号停用、新号承接。
        两表都改完存盘后点「导入登记表」。
      </Typography>

      {!registry && (
        <Typography variant="caption" color="text.secondary">
          登记表状态接口不可用（/api/etl/registry）—— 日报其它功能不受影响。
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
          {(registry.notes || []).slice(0, 8).map((n, i) => (
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

      {result && (
        <Alert severity={result.severity} sx={{ mt: 2 }} onClose={() => setResult(null)}>
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
        </Alert>
      )}
    </Paper>
  );
}
