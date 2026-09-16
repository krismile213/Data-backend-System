import React, { useEffect, useMemo, useState } from 'react';
import {
  Card,
  CardContent,
  Alert,
  CircularProgress,
  Box,
  Tabs,
  Tab,
  TextField,
  MenuItem,
  Button,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  TableSortLabel,
  Paper,
  IconButton,
  Collapse,
  Tooltip,
  Typography,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import SearchIcon from '@mui/icons-material/Search';
import { fetchBatches, fetchRecords, buildExportUrl } from '../api.js';

/** 可排序列（时间与编号等） */
const SORTABLE = ['审批编号', '创建时间', '完成时间', '更新时间', '审批状态', '创建人'];

/** 单元格显示：长文本截断，审批记录列单独处理 */
function renderCell(value) {
  if (value === null || value === undefined || value === '') return '—';
  const s = String(value);
  return s.length > 60 ? `${s.slice(0, 60)}…` : s;
}

function RecordRow({ row, columns }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <TableRow hover>
        <TableCell padding="checkbox">
          {row._nodes && row._nodes.length > 0 && (
            <IconButton size="small" onClick={() => setOpen(!open)}>
              {open ? <ExpandLessIcon /> : <ExpandMoreIcon />}
            </IconButton>
          )}
        </TableCell>
        {columns.map((c) => (
          <TableCell key={c} sx={{ maxWidth: 260 }}>
            {renderCell(row[c])}
          </TableCell>
        ))}
      </TableRow>
      <TableRow>
        <TableCell colSpan={columns.length + 1} sx={{ py: 0, border: 0 }}>
          <Collapse in={open} timeout="auto" unmountOnExit>
            <Box sx={{ m: 1 }}>
              <Typography variant="subtitle2" gutterBottom>
                审批记录节点明细（{row._nodes ? row._nodes.length : 0} 个节点）
              </Typography>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>操作人</TableCell>
                    <TableCell>动作</TableCell>
                    <TableCell>时间</TableCell>
                    <TableCell>备注</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {(row._nodes || []).map((n, i) => (
                    <TableRow key={i}>
                      <TableCell>{n.operator || '—'}</TableCell>
                      <TableCell>{n.action}</TableCell>
                      <TableCell>{n.time || '—'}</TableCell>
                      <TableCell>{n.remark || '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}

export default function Records({ batch }) {
  const [parts, setParts] = useState([]);
  const [part, setPart] = useState('');
  const [tab, setTab] = useState(0);

  const [filters, setFilters] = useState({
    approvalNo: '',
    creator: '',
    status: '',
    keyword: '',
  });
  const [applied, setApplied] = useState({}); // 已提交的筛选条件

  const [data, setData] = useState(null);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(20);
  const [sortField, setSortField] = useState('');
  const [sortOrder, setSortOrder] = useState('asc');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // 批次变化：重拉 Part 列表，默认选第一个 Part，重置筛选
  useEffect(() => {
    if (!batch) return;
    fetchBatches()
      .then((res) => {
        const b = (res.batches || []).find((x) => x.name === batch);
        const list = b ? b.parts : [];
        setParts(list);
        if (list.length) {
          setPart(list[0].part);
          setTab(0);
        } else {
          setPart('');
        }
      })
      .catch(() => setParts([]));
    setFilters({ approvalNo: '', creator: '', status: '', keyword: '' });
    setApplied({});
    setPage(0);
    setSortField('');
  }, [batch]);

  // 查询（批次/Part/分页/排序/筛选变化均触发）
  useEffect(() => {
    if (!batch || !part) return;
    setLoading(true);
    setError('');
    fetchRecords(batch, part, {
      page: page + 1,
      pageSize,
      sortField: sortField || undefined,
      sortOrder,
      ...applied,
    })
      .then(setData)
      .catch((e) => setError(e.message || '查询失败'))
      .finally(() => setLoading(false));
  }, [batch, part, page, pageSize, sortField, sortOrder, applied]);

  const handleSearch = () => {
    setApplied({ ...filters });
    setPage(0);
  };

  const handleReset = () => {
    const empty = { approvalNo: '', creator: '', status: '', keyword: '' };
    setFilters(empty);
    setApplied({});
    setPage(0);
  };

  const handleSort = (field) => {
    if (sortField === field) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortOrder('asc');
    }
    setPage(0);
  };

  const handleExport = () => {
    if (!batch || !part) return;
    window.open(buildExportUrl(batch, part, applied), '_blank');
  };

  const columns = useMemo(() => (data ? data.columns : []), [data]);

  if (!batch) {
    return <Alert severity="info">请先在右上角选择数据批次</Alert>;
  }

  return (
    <Card>
      <CardContent>
        {/* Part 切换 */}
        <Tabs
          value={tab}
          onChange={(e, v) => {
            setTab(v);
            setPart(parts[v] ? parts[v].part : '');
            setPage(0);
          }}
          variant="scrollable"
          scrollButtons="auto"
          sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}
        >
          {parts.map((p) => (
            <Tab key={p.part} label={`${p.part} ${p.name}`} />
          ))}
        </Tabs>

        {/* 筛选区 */}
        <Box className="flex flex-wrap items-center gap-2 mb-3">
          <TextField
            size="small"
            label="审批编号"
            value={filters.approvalNo}
            onChange={(e) => setFilters({ ...filters, approvalNo: e.target.value })}
            sx={{ width: 220 }}
          />
          <TextField
            size="small"
            label="创建人"
            value={filters.creator}
            onChange={(e) => setFilters({ ...filters, creator: e.target.value })}
            sx={{ width: 140 }}
          />
          <TextField
            select
            size="small"
            label="审批状态"
            value={filters.status}
            onChange={(e) => setFilters({ ...filters, status: e.target.value })}
            sx={{ width: 140 }}
          >
            <MenuItem value="">全部</MenuItem>
            {(data ? data.statusOptions : []).map((s) => (
              <MenuItem key={s} value={s}>
                {s}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            size="small"
            label="关键字搜索（全字段）"
            value={filters.keyword}
            onChange={(e) => setFilters({ ...filters, keyword: e.target.value })}
            sx={{ width: 240 }}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          />
          <Button variant="contained" startIcon={<SearchIcon />} onClick={handleSearch}>
            查询
          </Button>
          <Button variant="outlined" onClick={handleReset}>
            重置
          </Button>
          <Tooltip title="导出当前筛选结果为 xlsx（审批编号按文本写出）">
            <Button variant="outlined" startIcon={<DownloadIcon />} onClick={handleExport}>
              导出 xlsx
            </Button>
          </Tooltip>
        </Box>

        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        {!part && !error && <Alert severity="info">该批次无数据文件</Alert>}

        {loading && (
          <Box sx={{ display: 'flex', justifyContent: 'center', my: 4 }}>
            <CircularProgress size={28} />
          </Box>
        )}

        {data && !loading && (
          <>
            <TableContainer component={Paper} sx={{ maxHeight: 620 }}>
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow>
                    <TableCell padding="checkbox" />
                    {columns.map((c) => (
                      <TableCell key={c}>
                        {SORTABLE.includes(c) ? (
                          <TableSortLabel
                            active={sortField === c}
                            direction={sortField === c ? sortOrder : 'asc'}
                            onClick={() => handleSort(c)}
                          >
                            {c}
                          </TableSortLabel>
                        ) : (
                          c
                        )}
                      </TableCell>
                    ))}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {data.rows.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={columns.length + 1} align="center">
                        无匹配数据
                      </TableCell>
                    </TableRow>
                  )}
                  {data.rows.map((row, i) => (
                    <RecordRow key={i} row={row} columns={columns} />
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
            <TablePagination
              component="div"
              count={data.total}
              page={page}
              onPageChange={(e, p) => setPage(p)}
              rowsPerPage={pageSize}
              onRowsPerPageChange={(e) => {
                setPageSize(parseInt(e.target.value, 10));
                setPage(0);
              }}
              rowsPerPageOptions={[10, 20, 50, 100]}
              labelRowsPerPage="每页行数"
              labelDisplayedRows={({ from, to, count }) =>
                `第 ${from}-${to} 条，共 ${count} 条`
              }
            />
          </>
        )}
      </CardContent>
    </Card>
  );
}
