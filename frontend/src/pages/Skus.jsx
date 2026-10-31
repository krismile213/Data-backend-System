import React, { useEffect, useMemo, useState } from 'react';
import { LoadingState, ErrorState, EmptyState } from '../components/StateViews.jsx';
import { SEMANTIC } from '../theme.js';
import {
  Box,
  Chip,
  Collapse,
  InputAdornment,
  List,
  ListItemButton,
  ListItemText,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material';
import ManageSearchIcon from '@mui/icons-material/ManageSearch';
import SearchIcon from '@mui/icons-material/Search';
import { fetchSkus, fetchSkuDetail } from '../api.js';
import useApi from '../hooks/useApi.js';

/** 审批状态 → Chip 颜色 */
const STATUS_COLOR = {
  已结束: 'error',      // 项目惯例：完成/正向用红
  审批中: 'warning',
  终止: 'default',
};

/** Part 汇总徽标 */
function PartChips({ partSummary }) {
  return (
    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
      {partSummary.map((p) => (
        <Chip
          key={p.part}
          size="small"
          label={`${p.part} ${p.total}张`}
          color={p.finished === p.total && p.total > 0 ? 'error' : 'default'}
          variant={p.finished === p.total && p.total > 0 ? 'filled' : 'outlined'}
          title={`已结束 ${p.finished} / 审批中 ${p.running} / 终止 ${p.terminated}`}
        />
      ))}
    </Box>
  );
}

/** 节点时间线（展开行内容） */
function NodeTimeline({ nodes }) {
  if (!nodes || nodes.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary" sx={{ p: 1.5 }}>
        （该单据审批记录为空或无法解析）
      </Typography>
    );
  }
  return (
    <Box sx={{ p: 1.5, pl: 3 }}>
      {nodes.map((n, i) => (
        <Box key={i} sx={{ display: 'flex', gap: 1.5, alignItems: 'baseline', py: 0.4 }}>
          <Typography variant="caption" color="text.secondary" sx={{ minWidth: 140 }}>
            {n.time || '—'}
          </Typography>
          <Chip size="small" variant="outlined" label={n.action} sx={{ maxWidth: 220 }} />
          <Typography variant="body2">{n.operator || '—'}</Typography>
          {n.remark && (
            <Typography variant="caption" color="text.secondary">
              {n.remark}
            </Typography>
          )}
        </Box>
      ))}
    </Box>
  );
}

export default function Skus() {
  const [keyword, setKeyword] = useState('');
  const [selected, setSelected] = useState(null);
  const [expanded, setExpanded] = useState({});

  // 统一请求 hook：SKU 列表 + 选中 SKU 的档案明细
  const { data: list, error: listErr } = useApi(
    () => fetchSkus().then((d) => d.items),
    [],
    {
      // 默认选中第一个 SKU
      onSuccess: (items) => {
        if (items.length > 0) setSelected(items[0].sku);
      },
    },
  );

  const {
    data: detail, loading: detailLoading, error: detailErr, setData: setDetail,
  } = useApi(
    () => fetchSkuDetail(selected),
    [selected],
    { enabled: !!selected },
  );

  const error = listErr || detailErr;

  // 切换 SKU 时清掉上一条明细与展开态
  useEffect(() => {
    setDetail(null);
    setExpanded({});
  }, [selected, setDetail]);

  const filtered = useMemo(() => {
    if (!list) return [];
    const k = keyword.trim().toLowerCase();
    if (!k) return list;
    return list.filter(
      (s) =>
        s.sku.toLowerCase().includes(k) ||
        s.models.some((m) => m.toLowerCase().includes(k)),
    );
  }, [list, keyword]);

  if (error) return <ErrorState prefix="加载失败" error={error} />;
  if (!list) return<LoadingState />;

  return (
    <Box sx={{ display: 'flex', gap: 2, alignItems: 'flex-start' }}>
      {/* 左：SKU 检索列表 */}
      <Paper sx={{ width: 320, flexShrink: 0, overflow: 'hidden' }}>
        <Box sx={{ p: 1.5, borderBottom: `1px solid ${SEMANTIC.border}` }}>
          <TextField
            size="small"
            fullWidth
            placeholder="搜索 SKU / 型号"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon fontSize="small" />
                </InputAdornment>
              ),
            }}
          />
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
            共 {list.length} 个 SKU ｜ 命中 {filtered.length} 个 ｜ 按立项时间从新到久
          </Typography>
        </Box>
        <List dense sx={{ maxHeight: 'calc(100vh - 220px)', overflow: 'auto' }}>
          {filtered.map((s) => (
            <ListItemButton
              key={s.sku}
              selected={selected === s.sku}
              onClick={() => setSelected(s.sku)}
            >
              <ListItemText
                primary={
                  <Box component="span" sx={{ display: 'flex', justifyContent: 'space-between' }}>
                    <Box component="span" sx={{ fontFamily: 'monospace', fontWeight: 600 }}>
                      {s.sku}
                    </Box>
                    <Box component="span" sx={{ color: 'text.secondary', fontSize: 12 }}>
                      {s.docCount} 张
                    </Box>
                  </Box>
                }
                secondary={
                  <Box component="span">
                    {s.models[0] || '—'}
                    {s.firstProposalAt && (
                      <Box component="span" sx={{ color: 'text.secondary', fontSize: 11 }}>
                        {' · 立项 '}{s.firstProposalAt.slice(0, 10)}
                      </Box>
                    )}
                  </Box>
                }
                secondaryTypographyProps={{ noWrap: true, component: 'div' }}
              />
            </ListItemButton>
          ))}
          {filtered.length === 0 && (
            <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
              无匹配 SKU
            </Typography>
          )}
        </List>
      </Paper>

      {/* 右：SKU 档案详情 */}
      <Box sx={{ flexGrow: 1, minWidth: 0 }}>
        {!selected && <EmptyState>请选择左侧 SKU 查看档案</EmptyState>}
        {detailLoading && <LoadingState sx={{ mt: 6 }} />}
        {detail && (
          <>
            {/* 头部信息 */}
            <Paper sx={{ p: 2, mb: 2 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 1.5 }}>
                <ManageSearchIcon color="primary" />
                <Typography variant="h6" sx={{ fontFamily: 'monospace' }}>
                  {detail.sku}
                </Typography>
                {detail.docs
                  .map((d) => d.model)
                  .filter(Boolean)
                  .filter((v, i, a) => a.indexOf(v) === i)
                  .slice(0, 2)
                  .map((m) => (
                    <Chip key={m} size="small" label={m} variant="outlined" />
                  ))}
                <Typography variant="body2" color="text.secondary">
                  共 {detail.docCount} 张单据
                  {detail.duplicatesRemoved > 0 && (
                    <Typography component="span" variant="caption"
                      color="text.secondary" sx={{ ml: 1 }}>
                      （已折叠 {detail.duplicatesRemoved} 张跨批次重复，只保留最新）
                    </Typography>
                  )}
                </Typography>
              </Box>
              <Box sx={{ mt: 1.5 }}>
                <PartChips partSummary={detail.partSummary} />
              </Box>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                覆盖批次：{[...new Set(detail.docs.map((d) => d.batch))].join('、')}
              </Typography>
            </Paper>

            {/* 单据表（可展开节点时间线） */}
            <TableContainer component={Paper} sx={{ maxHeight: 'calc(100vh - 320px)' }}>
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow>
                    <TableCell width={36} />
                    <TableCell>批次</TableCell>
                    <TableCell>环节</TableCell>
                    <TableCell>审批编号</TableCell>
                    <TableCell>状态</TableCell>
                    <TableCell>标题</TableCell>
                    <TableCell>创建人</TableCell>
                    <TableCell>创建时间</TableCell>
                    <TableCell>完成时间</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {detail.docs.map((d, i) => (
                    <React.Fragment key={`${d.batch}-${d.part}-${i}`}>
                      <TableRow
                        hover
                        onClick={() => setExpanded((e) => ({ ...e, [i]: !e[i] }))}
                        sx={{ cursor: 'pointer' }}
                      >
                        <TableCell>
                          <Typography variant="caption" color="text.secondary">
                            {expanded[i] ? '▾' : '▸'} {d.nodes.length}
                          </Typography>
                        </TableCell>
                        <TableCell>{d.batch}</TableCell>
                        <TableCell>{d.part}</TableCell>
                        <TableCell sx={{ fontFamily: 'monospace' }}>
                          {d.approval_no || '—'}
                        </TableCell>
                        <TableCell>
                          <Chip
                            size="small"
                            label={d.status || '—'}
                            color={STATUS_COLOR[d.status] || 'default'}
                            variant="outlined"
                          />
                        </TableCell>
                        <TableCell sx={{ maxWidth: 260 }} noWrap>
                          {d.title || '—'}
                        </TableCell>
                        <TableCell>{d.creator || '—'}</TableCell>
                        <TableCell>{d.create_time || '—'}</TableCell>
                        <TableCell>{d.finish_time || '—'}</TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell colSpan={9} sx={{ py: 0, border: 'none' }}>
                          <Collapse in={!!expanded[i]} unmountOnExit>
                            <NodeTimeline nodes={d.nodes} />
                          </Collapse>
                        </TableCell>
                      </TableRow>
                    </React.Fragment>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </>
        )}
      </Box>
    </Box>
  );
}
