import React, { useMemo, useState } from 'react';
import { LoadingState, ErrorState, EmptyState } from '../components/StateViews.jsx';
import useApi from '../hooks/useApi.js';
import {
  Card,
  CardContent,
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
  const [type, setType] = useState('');
  const [part, setPart] = useState('');

  // 统一请求 hook：loading / error / 竞态防护（切筛选时旧请求不会覆盖新结果）
  const { data, error, loading } = useApi(
    () => fetchAnomalies(batch, { type: type || undefined, part: part || undefined }),
    [batch, type, part],
    { enabled: !!batch },
  );

  const partOptions = useMemo(() => {
    if (!data) return [];
    return [...new Set(data.items.map((i) => i.Part))].sort();
  }, [data]);

  if (!batch) {
    return <EmptyState>请先在右上角选择数据批次</EmptyState>;
  }
  if (loading) return<LoadingState />;
  if (error) return <ErrorState prefix="加载失败" error={error} />;
  if (!data) return null;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {/* 异常类型汇总卡片 */}
      <Card>
        <CardContent>
          <Typography variant="subtitle1" gutterBottom>
            异常类型汇总（当前批次共 {data.total} 条记录）
          </Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
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
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5 }}>
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
    </Box>
  );
}
