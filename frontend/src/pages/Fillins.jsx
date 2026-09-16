import React, { useCallback, useEffect, useMemo, useState } from 'react';
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
  IconButton,
  Tooltip,
  Autocomplete,
  Divider,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import SaveIcon from '@mui/icons-material/Save';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import {
  fetchFillins,
  fetchFillinsSku,
  saveFillin,
  deleteFillin,
  buildFillinsExportUrl,
} from '../api.js';

const OPERATOR_KEY = 'fillins-operator';

/** 差异天数 → 提示色：|d|≤2 正常，>2 提醒人工核对 */
function diffChip(d) {
  if (d === null || d === undefined) return null;
  const color = Math.abs(d) <= 2 ? 'default' : 'warning';
  return (
    <Tooltip title="手工填报与审批口径的日期差（天）。差异大说明审批记录与实际执行有偏差。">
      <Chip size="small" color={color} label={`${d > 0 ? '+' : ''}${d} 天`} />
    </Tooltip>
  );
}

export default function Fillins() {
  const [meta, setMeta] = useState(null);
  const [error, setError] = useState('');
  const [sku, setSku] = useState('');
  const [detail, setDetail] = useState(null);
  const [drafts, setDrafts] = useState({});     // node -> {actualAt, note}
  const [operator, setOperator] = useState(() => localStorage.getItem(OPERATOR_KEY) || '');
  const [saving, setSaving] = useState('');
  const [msg, setMsg] = useState('');

  const loadMeta = useCallback(() => {
    fetchFillins().then(setMeta).catch((e) => setError(e.message));
  }, []);

  useEffect(() => { loadMeta(); }, [loadMeta]);

  // 选中 SKU 后拉取节点档案
  useEffect(() => {
    if (!sku) { setDetail(null); return; }
    setDetail(null);
    fetchFillinsSku(sku).then((d) => {
      setDetail(d);
      const init = {};
      (d.nodes || []).forEach((n) => {
        init[n.node] = { actualAt: n.entry?.actualAt || '', note: n.entry?.note || '' };
      });
      setDrafts(init);
    }).catch((e) => setError(e.message));
  }, [sku]);

  const skuOptions = useMemo(() => {
    if (!meta) return [];
    const set = new Map();
    (meta.openSkus || []).forEach((o) => set.set(o.sku, o));
    (meta.entries || []).forEach((e) => {
      if (!set.has(e.sku)) set.set(e.sku, { sku: e.sku, model: '', invalidReason: '' });
    });
    return [...set.values()];
  }, [meta]);

  const doSave = async (node) => {
    const d = drafts[node] || {};
    if (!d.actualAt) { setMsg(`「${node}」请先选日期`); return; }
    setSaving(node);
    setMsg('');
    try {
      await saveFillin({ sku, node, actualAt: d.actualAt, note: d.note, operator });
      localStorage.setItem(OPERATOR_KEY, operator);
      const fresh = await fetchFillinsSku(sku);
      setDetail(fresh);
      setMeta(await fetchFillins());
      setMsg(`已保存：${sku} · ${node} = ${d.actualAt}`);
    } catch (e) {
      setMsg(`保存失败：${e.message}`);
    } finally {
      setSaving('');
    }
  };

  const doDelete = async (id) => {
    try {
      await deleteFillin(id);
      loadMeta();
      if (sku) setDetail(await fetchFillinsSku(sku));
    } catch (e) {
      setMsg(`删除失败：${e.message}`);
    }
  };

  if (error) return <Alert severity="error">手工填报加载失败：{error}</Alert>;
  if (!meta) return <Box sx={{ display: 'flex', justifyContent: 'center', mt: 8 }}><CircularProgress /></Box>;

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
        <Box>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>
            实际时间手工填报（免登录单机版）
          </Typography>
          <Typography variant="caption" color="text.secondary">
            审批导出缺失/失真的节点时间在这里人工补录，与审批口径自动对比；数据存本机 backend/state/
          </Typography>
        </Box>
        <Button variant="outlined" startIcon={<DownloadIcon />} href={buildFillinsExportUrl()}>
          导出填报 xlsx
        </Button>
      </Box>

      {msg && <Alert severity={msg.startsWith('已保存') ? 'success' : 'warning'} sx={{ mb: 2 }} onClose={() => setMsg('')}>{msg}</Alert>}

      <Grid container spacing={2}>
        {/* 左：SKU 选择与节点填报 */}
        <Grid item xs={12} lg={7}>
          <Paper sx={{ p: 2 }}>
            <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', mb: 1.5, flexWrap: 'wrap' }}>
              <Autocomplete
                size="small"
                sx={{ minWidth: 320 }}
                options={skuOptions}
                getOptionLabel={(o) => (typeof o === 'string' ? o : o.sku)}
                onChange={(e, v) => setSku(v ? (typeof v === 'string' ? v : v.sku) : '')}
                renderOption={(props, o) => (
                  <li {...props} key={o.sku}>
                    <Box>
                      <Typography variant="body2"><b>{o.sku}</b>　{o.model}</Typography>
                      {o.invalidReason && (
                        <Typography variant="caption" color="text.secondary">{o.invalidReason}</Typography>
                      )}
                    </Box>
                  </li>
                )}
                renderInput={(params) => <TextField {...params} placeholder="选择或输入 SKU（优先列出未闭环）" />}
              />
              <TextField
                size="small" label="填报人" value={operator}
                onChange={(e) => setOperator(e.target.value)}
                sx={{ width: 140 }}
              />
              {detail && (
                <Chip
                  size="small" color={detail.valid ? 'success' : 'error'}
                  label={detail.valid ? '已闭环' : '未闭环'}
                />
              )}
            </Box>

            {!sku ? (
              <Typography variant="body2" color="text.secondary" sx={{ py: 6, textAlign: 'center' }}>
                ← 先选择一个 SKU，然后逐节点补录实际时间
              </Typography>
            ) : !detail ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}><CircularProgress size={28} /></Box>
            ) : (
              <>
                <Typography variant="caption" color="text.secondary">
                  {detail.model || '—'} · 已填 {detail.filledCount}/{detail.nodes.length} 个节点
                  {detail.invalidReason ? ` · ${detail.invalidReason}` : ''}
                </Typography>
                <TableContainer sx={{ mt: 1 }}>
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>节点</TableCell>
                        <TableCell>审批口径</TableCell>
                        <TableCell>差异</TableCell>
                        <TableCell width={150}>实际时间</TableCell>
                        <TableCell>备注</TableCell>
                        <TableCell width={70}></TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {detail.nodes.map((n) => {
                        const d = drafts[n.node] || {};
                        return (
                          <TableRow key={n.node} hover>
                            <TableCell>
                              <Typography variant="body2" sx={{ fontWeight: 600 }}>{n.node}</Typography>
                              <Typography variant="caption" color="text.secondary">{n.part}</Typography>
                            </TableCell>
                            <TableCell>
                              <Typography variant="body2">{n.approvalAt || '—缺失—'}</Typography>
                              {n.approvalAt && (
                                <Typography variant="caption" color="text.secondary">{n.approvalSrc}</Typography>
                              )}
                            </TableCell>
                            <TableCell>{n.entry ? diffChip(n.diffDays) : '—'}</TableCell>
                            <TableCell>
                              <TextField
                                size="small" type="date" fullWidth
                                value={d.actualAt || ''}
                                onChange={(e) => setDrafts((s) => ({ ...s, [n.node]: { ...d, actualAt: e.target.value } }))}
                              />
                            </TableCell>
                            <TableCell>
                              <TextField
                                size="small" fullWidth placeholder="备注（选填）"
                                value={d.note || ''}
                                onChange={(e) => setDrafts((s) => ({ ...s, [n.node]: { ...d, note: e.target.value } }))}
                              />
                            </TableCell>
                            <TableCell>
                              <IconButton
                                size="small" color="primary"
                                disabled={saving === n.node}
                                onClick={() => doSave(n.node)}
                              >
                                <Tooltip title="保存（同节点重复保存=更正）"><SaveIcon fontSize="small" /></Tooltip>
                              </IconButton>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </TableContainer>
              </>
            )}
          </Paper>
        </Grid>

        {/* 右：填报记录 */}
        <Grid item xs={12} lg={5}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2" gutterBottom>
              填报记录（{meta.entryCount} 条 · 覆盖 {meta.skuCount} 个 SKU）
            </Typography>
            <Divider sx={{ mb: 1 }} />
            {meta.entries.length === 0 ? (
              <Typography variant="body2" color="text.secondary" sx={{ py: 4, textAlign: 'center' }}>
                暂无记录。左侧选择 SKU 后保存第一条。
              </Typography>
            ) : (
              <TableContainer sx={{ maxHeight: 520 }}>
                <Table size="small" stickyHeader>
                  <TableHead>
                    <TableRow>
                      <TableCell>SKU · 节点</TableCell>
                      <TableCell>实际时间</TableCell>
                      <TableCell>填报人</TableCell>
                      <TableCell width={50}></TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {meta.entries.map((e) => (
                      <TableRow key={e.id} hover>
                        <TableCell onClick={() => setSku(e.sku)} sx={{ cursor: 'pointer' }}>
                          <Typography variant="body2"><b>{e.sku}</b> · {e.node}</Typography>
                          {e.note && <Typography variant="caption" color="text.secondary">{e.note}</Typography>}
                        </TableCell>
                        <TableCell>{e.actualAt}</TableCell>
                        <TableCell>{e.operator || '—'}</TableCell>
                        <TableCell>
                          <IconButton size="small" onClick={() => doDelete(e.id)}>
                            <Tooltip title="删除"><DeleteOutlineIcon fontSize="small" /></Tooltip>
                          </IconButton>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </Paper>
        </Grid>
      </Grid>
    </Box>
  );
}
