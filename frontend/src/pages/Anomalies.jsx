import React, { useEffect, useMemo, useState } from 'react';
import {
  Card,
  CardContent,
  Alert,
  CircularProgress,
  Box,
  Chip,
  TextField,
  MenuItem,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Typography,
  Button,
} from '@mui/material';
import FileDownloadIcon from '@mui/icons-material/FileDownload';
import { fetchAnomalies, buildAnomalyExportUrl } from '../api.js';

/** 异常类型 → 标签颜色（严重程度） */
const TYPE_COLORS = {
  '审批编号缺失': 'error',
  '审批编号重复': 'error',
  '审批记录为空': 'error',
  '渠道未发货(超时)': 'error',
  '渠道未发货': 'warning',
  '已结束但流程未到终节点': 'warning',
  '业务时间列缺失': 'warning',
  '长期未完成(停滞)': 'warning',
  '环节耗时超长': 'warning',
  '分段耗时异常小(疑似补录)': 'info',
  '流程回退/重做': 'info',
  '节点时间乱序': 'info',
  '终止单据': 'default',
};

export default function Anomalies({ batch }) {
  const [data, setData] = useState(null);
  const [type, setType] = useState('');
  const [part, setPart] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!batch) return;
    setLoading(true);
    setError('');
    fetchAnomalies(batch, { type: type || undefined, part: part || undefined })
      .then(setData)
      .catch((e) => setError(e.message || '加载失败'))
      .finally(() => setLoading(false));
  }, [batch, type, part]);

  const partOptions = useMemo(() => {
    if (!data) return [];
    return [...new Set(data.items.map((i) => i.Part))].sort();
  }, [data]);

  if (!batch) {
    return <Alert severity="info">请先在右上角选择数据批次</Alert>;
  }
  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', mt: 8 }}>
        <CircularProgress />
      </Box>
    );
  }
  if (error) {
    return <Alert severity="error">加载失败：{error}</Alert>;
  }
  if (!data) return null;

  return (
    <div className="space-y-4">
      {/* 异常类型汇总卡片 */}
      <Card>
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            异常类型汇总（当前批次共 {data.total} 条记录）
          </Typography>
          <Box className="flex flex-wrap gap-1.5">
            <Chip
              label={`全部（${data.typeSummary.reduce((a, b) => a + b.count, 0)}）`}
              color={type === '' ? 'primary' : 'default'}
              onClick={() => setType('')}
            />
            {data.typeSummary.map((t) => (
              <Chip
                key={t.type}
                label={`${t.type}（${t.count}）`}
                color={type === t.type ? 'primary' : TYPE_COLORS[t.type] || 'default'}
                variant={type === t.type ? 'filled' : 'outlined'}
                onClick={() => setType(type === t.type ? '' : t.type)}
              />
            ))}
          </Box>
        </CardContent>
      </Card>

      {/* 筛选 + 列表 */}
      <Card>
        <CardContent>
          <Box className="flex items-center gap-2 mb-3">
            <TextField
              select
              size="small"
              label="环节（Part）"
              value={part}
              onChange={(e) => setPart(e.target.value)}
              sx={{ width: 200 }}
            >
              <MenuItem value="">全部环节</MenuItem>
              {partOptions.map((p) => (
                <MenuItem key={p} value={p}>
                  {p}
                </MenuItem>
              ))}
            </TextField>
            {(type || part) && (
              <Chip
                label="清除筛选"
                onDelete={() => {
                  setType('');
                  setPart('');
                }}
              />
            )}
            <Box sx={{ ml: 'auto' }}>
              <a
                href={buildAnomalyExportUrl(batch, { type, part })}
                style={{ textDecoration: 'none' }}
              >
                <Button
                  variant="outlined"
                  size="small"
                  startIcon={<FileDownloadIcon />}
                  disabled={data.items.length === 0}
                >
                  导出 xlsx（{data.items.length} 条）
                </Button>
              </a>
            </Box>
          </Box>

          <TableContainer component={Paper} sx={{ maxHeight: 620 }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell>环节</TableCell>
                  <TableCell>异常类型</TableCell>
                  <TableCell>审批编号</TableCell>
                  <TableCell>SKU</TableCell>
                  <TableCell>新机型号</TableCell>
                  <TableCell>审批状态</TableCell>
                  <TableCell>说明</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {data.items.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={7} align="center">
                      未发现该类异常
                    </TableCell>
                  </TableRow>
                )}
                {data.items.map((a, i) => (
                  <TableRow key={i} hover>
                    <TableCell>{`${a.Part} ${a.环节名称}`}</TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={a.异常类型}
                        color={TYPE_COLORS[a.异常类型] || 'default'}
                      />
                    </TableCell>
                    <TableCell sx={{ fontFamily: 'monospace' }}>
                      {a.审批编号 || '—'}
                    </TableCell>
                    <TableCell>{a.SKU || '—'}</TableCell>
                    <TableCell>{a.新机型号 || '—'}</TableCell>
                    <TableCell>{a.审批状态 || '—'}</TableCell>
                    <TableCell sx={{ maxWidth: 420 }}>{a.说明}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </CardContent>
      </Card>
    </div>
  );
}
