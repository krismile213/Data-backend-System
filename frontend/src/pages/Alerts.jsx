import React, { useMemo, useState } from 'react';
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
  MenuItem,
  Chip,
  Tooltip,
  Collapse,
  IconButton,
} from '@mui/material';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import KeyboardArrowUpIcon from '@mui/icons-material/KeyboardArrowUp';
import DownloadIcon from '@mui/icons-material/Download';
import Button from '@mui/material/Button';
import { fetchAlerts, buildAlertsExportUrl } from '../api.js';
import KpiCard from '../components/KpiCard.jsx';
import PageHeader from '../components/PageHeader.jsx';
import { LoadingState, ErrorState } from '../components/StateViews.jsx';
import { SEMANTIC } from '../theme.js';
import useApi from '../hooks/useApi.js';

const PRIO_COLOR = {
  P0: { bgcolor: SEMANTIC.danger, label: 'P0 立即处理' },
  P1: { bgcolor: SEMANTIC.warning, label: 'P1 重点跟进' },
  P2: { bgcolor: SEMANTIC.slate, label: 'P2 推进中' },
};

function fmtDays(d) {
  return d === null || d === undefined ? '—' : `${Math.round(d)}天`;
}

/** 判定符号 → 颜色（✗/差 红、⚠/良 橙、✓/优 绿） */
function judgeColor(judge) {
  if (!judge) return 'text.secondary';
  if (judge.startsWith('✗') || judge === '差') return SEMANTIC.danger;
  if (judge.startsWith('⚠') || judge === '良') return SEMANTIC.warning;
  if (judge.startsWith('✓') || judge === '优') return SEMANTIC.success;
  return 'text.secondary';
}

function worstLoc(detail) {
  if (!detail || !detail.length || !detail[0].arr) return null;
  const w = detail[0];
  return `${w.hub} · 货件${w.trk}（${(w.arr || '').slice(0, 10)}到货，取数起${w.fetchDays === null ? '—' : Math.round(w.fetchDays)}天）`;
}

/** 逐票判定 → 颜色（未到货/未发货按橙处理） */
function rowJudgeColor(judge) {
  if (judge === '差') return SEMANTIC.danger;
  if (judge === '优') return SEMANTIC.success;
  return SEMANTIC.warning;
}

function AlertRow({ r }) {
  const [open, setOpen] = useState(false);
  const pc = PRIO_COLOR[r.priority];
  return (
    <>
      <TableRow hover sx={{ '& > *': { borderBottom: 'unset' } }}>
        <TableCell>
          <IconButton size="small" onClick={() => setOpen(!open)}>
            {open ? <KeyboardArrowUpIcon /> : <KeyboardArrowDownIcon />}
          </IconButton>
        </TableCell>
        <TableCell>
          <Chip size="small" label={r.priority} sx={{ bgcolor: pc.bgcolor, color: SEMANTIC.onColor, fontWeight: 700 }} />
        </TableCell>
        <TableCell sx={{ fontWeight: 600 }}>{r.sku}</TableCell>
        <TableCell>{r.model || '—'}</TableCell>
        <TableCell>
          {r.pendingPart ? (
            <Tooltip title={`最近动态 ${r.lastDynamic || '—'}`}>
              <span>{r.pendingPartName || r.pendingPart}</span>
            </Tooltip>
          ) : '—'}
        </TableCell>
        <TableCell align="right"
          sx={{ color: (r.stuckDays || 0) > 7 ? SEMANTIC.danger : 'inherit', fontWeight: 600 }}>
          {r.stuckDays === null || r.stuckDays === undefined ? '—' : r.stuckDays}
        </TableCell>
        <TableCell>
          <Tooltip title={r.prodSeg.basis}>
            <span style={{ color: judgeColor(r.prodSeg.judge) }}>
              {r.prodSeg.days === null ? '—' : `${Math.round(r.prodSeg.days)}/${r.prodSeg.target}`} {r.prodSeg.judge}
            </span>
          </Tooltip>
        </TableCell>
        <TableCell>
          <Tooltip title={r.opsSeg.seg}>
            <span style={{ color: judgeColor(r.opsSeg.judge) }}>
              {r.opsSeg.days === null ? '—' : `${Math.round(r.opsSeg.days)}/${r.opsSeg.target}`} {r.opsSeg.judge}
            </span>
          </Tooltip>
        </TableCell>
        <TableCell>
          <Tooltip title={worstLoc(r.air.detail) || '无空运货件'}>
            <span style={{ color: judgeColor(r.air.judge) }}>
              {fmtDays(r.air.days)} {r.air.judge}
            </span>
          </Tooltip>
        </TableCell>
        <TableCell>
          <Tooltip title={worstLoc(r.sea.detail) || '无海运货件'}>
            <span style={{ color: judgeColor(r.sea.judge) }}>
              {fmtDays(r.sea.days)} {r.sea.judge}
            </span>
          </Tooltip>
        </TableCell>
        <TableCell align="right"
          sx={{ color: r.proposal.stale ? SEMANTIC.danger : 'inherit' }}>
          {r.proposal.days === null ? '—' : Math.round(r.proposal.days)}
        </TableCell>
        <TableCell align="right"
          sx={{ color: r.countries < r.countryMin ? SEMANTIC.warning : SEMANTIC.success }}>
          {r.countries}/{r.countryMin}
        </TableCell>
        <TableCell>
          <Typography variant="caption"
            sx={{ display: 'block', maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {r.nextSteps.join('；')}
          </Typography>
        </TableCell>
      </TableRow>
      <TableRow>
        <TableCell colSpan={13} sx={{ py: 0, bgcolor: SEMANTIC.neutralBg }}>
          <Collapse in={open} unmountOnExit>
            <Box sx={{ p: 2 }}>
              <Grid container spacing={2}>
                <Grid item xs={12} md={6}>
                  <Typography variant="subtitle2" gutterBottom>加急原因</Typography>
                  {r.reasons.map((x, i) => (
                    <Typography key={i} variant="body2" sx={{ color: SEMANTIC.danger }}>• {x}</Typography>
                  ))}
                </Grid>
                <Grid item xs={12} md={6}>
                  <Typography variant="subtitle2" gutterBottom>下一步</Typography>
                  {r.nextSteps.map((x, i) => (
                    <Typography key={i} variant="body2">• {x}</Typography>
                  ))}
                  {r.missingHubs.length > 0 && (
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                      未到货渠道：{r.missingHubs.join('、')}
                    </Typography>
                  )}
                </Grid>
                <Grid item xs={12}>
                  <Typography variant="subtitle2" gutterBottom>
                    运输明细（逐货件 · 按到货时间倒序，红色=超底线，即空运/海运耗时的实际来源）
                  </Typography>
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>货件号</TableCell>
                        <TableCell>到货渠道（国家）</TableCell>
                        <TableCell>运输方式</TableCell>
                        <TableCell>发货日期</TableCell>
                        <TableCell>到货日期</TableCell>
                        <TableCell align="right">发货→到货(天)</TableCell>
                        <TableCell align="right">取数→到货(天)</TableCell>
                        <TableCell>判定</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {[...(r.air.detail || []), ...(r.sea.detail || [])]
                        .sort((a, b) => (b.arr || '').localeCompare(a.arr || ''))
                        .map((s, i) => (
                        <TableRow key={`${s.trk}-${i}`}
                          sx={{ bgcolor: s.judge === '差' ? SEMANTIC.dangerBg : 'inherit' }}>
                          <TableCell sx={{ fontFamily: 'monospace' }}>{s.trk}</TableCell>
                          <TableCell>{s.hub || '—'}</TableCell>
                          <TableCell>{s.mode}</TableCell>
                          <TableCell>{(s.ship || '').slice(0, 10) || '—'}</TableCell>
                          <TableCell>{(s.arr || '').slice(0, 10) || '—'}</TableCell>
                          <TableCell align="right">{s.transit === null ? '—' : Math.round(s.transit)}</TableCell>
                          <TableCell align="right"
                            sx={{ fontWeight: s.judge === '差' ? 700 : 400 }}>
                            {s.fetchDays === null ? '—' : Math.round(s.fetchDays)}
                          </TableCell>
                          <TableCell>
                            <span style={{
                              color: rowJudgeColor(s.judge),
                              fontWeight: s.judge === '差' ? 700 : 400,
                            }}>
                              {s.judge === '未到货' || s.judge === '未发货'
                                ? `⏳ ${s.judge}` : s.judge}
                            </span>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  {!(r.air.detail || []).length && !(r.sea.detail || []).length && (
                    <Typography variant="caption" color="text.secondary">无货件运输记录</Typography>
                  )}
                </Grid>
              </Grid>
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}

export default function Alerts() {
  const { data, error } = useApi(() => fetchAlerts(), []);
  const [q, setQ] = useState('');
  const [prio, setPrio] = useState('');

  const rows = useMemo(() => {
    if (!data) return [];
    return data.rows.filter((r) => {
      if (prio && r.priority !== prio) return false;
      if (q && !(r.sku.toLowerCase().includes(q.toLowerCase())
        || (r.model || '').toLowerCase().includes(q.toLowerCase()))) return false;
      return true;
    });
  }, [data, q, prio]);

  if (error) return <ErrorState prefix="加急预警加载失败" error={error} />;
  if (!data) return <LoadingState />;

  // 口径元信息来自后端（不硬编码）：产品段/运营段的边界串。缺失时退化为不追加。
  const segDef = data.segDef;

  return (
    <Box>
      <PageHeader
        title="加急预警与盯数清单"
        subtitle={`口径：route_check_sys 系统口径 · 只列未闭环 SKU，停滞越久越靠前 · 末端终止（作废单）不催办、单列备案 · 已换号（旧号→新号）不催办、单列备案 · 已豁免（人工登记·业务上不需走完）不催办、单列备案 · 计算于 ${data.computedAt}${segDef ? ` · 产品段 ${segDef.prodSeg} · 运营段 ${segDef.opsSeg}` : ''}`}
      />

      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid item xs={6} sm={4} md={3}>
          <KpiCard label="覆盖 SKU" value={data.totalSkus} sub="全部批次去重" />
        </Grid>
        <Grid item xs={6} sm={4} md={3}>
          <KpiCard label="未闭环（预警中）" value={data.alertCount}
            sub="已闭环 SKU 不列入" color={SEMANTIC.warning} />
        </Grid>
        <Grid item xs={4} sm={4} md={3}>
          <KpiCard label="P0 立即处理" value={data.p0}
            sub="空/海超底线 · 提案超期 · Part7停滞>7天" color={SEMANTIC.danger} />
        </Grid>
        <Grid item xs={4} sm={4} md={3}>
          <KpiCard label="P1 重点跟进" value={data.p1}
            sub="产品段超时 · 停滞>7天 · 渠道缺口" color={SEMANTIC.warning} />
        </Grid>
        <Grid item xs={4} sm={4} md={3}>
          <KpiCard label="已终止·不催办" value={data.voidCount ?? 0}
            sub="末端终止的作废单，单列备查" color={SEMANTIC.neutral} />
        </Grid>
        <Grid item xs={4} sm={4} md={3}>
          <KpiCard label="已换号·不催办" value={data.aliasCount ?? 0}
            sub="流程中途换编码，旧号不催办" color={SEMANTIC.neutral} />
        </Grid>
        <Grid item xs={4} sm={4} md={3}>
          <KpiCard label="已豁免·不催办" value={data.holdCount ?? 0}
            sub="人工登记：业务上已不需走完流程" color={SEMANTIC.neutral} />
        </Grid>
        <Grid item xs={6} sm={4} md={3}>
          <KpiCard label="数据新鲜度" value={data.dataLagDays === null ? '—' : `滞后 ${data.dataLagDays} 天`}
            sub={`全库最新动态 ${data.latestDynamic || '—'}`}
            color={data.dataLagDays >= 3 ? SEMANTIC.danger : SEMANTIC.success} />
        </Grid>
      </Grid>

      <Paper sx={{ mb: 2 }}>
        <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', p: 2, flexWrap: 'wrap' }}>
          <Typography variant="subtitle2">加急明细（P0 → P2，点击行首箭头展开详情）</Typography>
          <Box sx={{ flexGrow: 1 }} />
          <TextField size="small" label="搜索 SKU / 型号" value={q}
            onChange={(e) => setQ(e.target.value)} sx={{ width: 220 }} />
          <TextField size="small" select label="优先级" value={prio}
            onChange={(e) => setPrio(e.target.value)} sx={{ width: 160 }}>
            <MenuItem value="">全部</MenuItem>
            <MenuItem value="P0">P0 立即处理</MenuItem>
            <MenuItem value="P1">P1 重点跟进</MenuItem>
            <MenuItem value="P2">P2 推进中</MenuItem>
          </TextField>
          <Button variant="outlined" size="small" startIcon={<DownloadIcon />}
            href={buildAlertsExportUrl(prio)}>
            导出 xlsx
          </Button>
        </Box>
        <TableContainer sx={{ maxHeight: 560 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell width={50} />
                <TableCell>优先级</TableCell>
                <TableCell>SKU</TableCell>
                <TableCell>型号</TableCell>
                <TableCell>卡点环节</TableCell>
                <TableCell align="right">停滞(天)</TableCell>
                <TableCell>
                  <Tooltip title={`产品段 = ${segDef?.prodSeg || ''}（素材交付 = Part6 产品营销素材加工，audit 时间）`}>
                    <span>产品段</span>
                  </Tooltip>
                </TableCell>
                <TableCell>
                  <Tooltip title={`运营段 = ${segDef?.opsSeg || ''}`}>
                    <span>运营段</span>
                  </Tooltip>
                </TableCell>
                <TableCell>空运</TableCell>
                <TableCell>海运</TableCell>
                <TableCell align="right">提案(天)</TableCell>
                <TableCell align="right">到货国</TableCell>
                <TableCell>下一步</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((r) => <AlertRow key={r.sku} r={r} />)}
              {rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={13} align="center">
                    <Typography variant="body2" color="text.secondary" sx={{ py: 3 }}>
                      当前筛选条件下没有预警 SKU 🎉
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>

      {!!(data.voidRows || []).length && (
        <Paper sx={{ mb: 2 }}>
          <Box sx={{ p: 2 }}>
            <Typography variant="subtitle2" gutterBottom>
              已终止 · 不催办（{data.voidRows.length} 个）
            </Typography>
            <Typography variant="caption" color="text.secondary"
              sx={{ display: 'block', mb: 1 }}>
              判据：末尾 Part 单据<strong>全部「终止」</strong>且全 SKU 无「审批中」单据
              —— 流程已作废（撤回 / 填错重开 / 型号取消），不进入后续环节，故不计入催办。
              「承接单」= 同型号、在终止之后重开的那张单。
            </Typography>
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>SKU</TableCell>
                    <TableCell>型号</TableCell>
                    <TableCell>终止环节</TableCell>
                    <TableCell>终止时间</TableCell>
                    <TableCell align="right">停滞(天)</TableCell>
                    <TableCell>承接单</TableCell>
                    <TableCell>结论</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {data.voidRows.map((r) => (
                    <TableRow key={r.sku} hover>
                      <TableCell sx={{ fontWeight: 600 }}>{r.sku}</TableCell>
                      <TableCell>{r.model || '—'}</TableCell>
                      <TableCell>{r.partName ? `${r.part} · ${r.partName}` : (r.part || '—')}</TableCell>
                      <TableCell>{(r.terminatedAt || '').slice(0, 16) || '—'}</TableCell>
                      <TableCell align="right">{r.stuckDays ?? '—'}</TableCell>
                      <TableCell>
                        {r.successor
                          ? `${r.successor.sku}（${r.successor.valid ? '已闭环' : '进行中'}）`
                          : <span style={{ color: SEMANTIC.danger }}>无</span>}
                      </TableCell>
                      <TableCell>
                        <Typography variant="caption"
                          sx={{ color: r.successor ? 'text.secondary' : SEMANTIC.danger }}>
                          {r.successorNote}
                        </Typography>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </Box>
        </Paper>
      )}

      {!!(data.aliasRows || []).length && (
        <Paper sx={{ mb: 2 }}>
          <Box sx={{ p: 2 }}>
            <Typography variant="subtitle2" gutterBottom>
              已换号 · 不催办（{data.aliasRows.length} 个）
            </Typography>
            <Typography variant="caption" color="text.secondary"
              sx={{ display: 'block', mb: 1 }}>
              判据：流程中途把编码换了（旧号停用、新号承接），旧号流程必然停在中途，
              报「缺节点」属正常，<strong>不计入催办</strong>。真源 = SKU登记表.xlsx
              <strong>「SKU换号」</strong>子表（业务登记，非自动推断）
              {data.aliasDef?.registered != null
                ? ` · 全库登记 ${data.aliasDef.registered} 对，本品类命中 ${data.aliasDef?.hitInCategory ?? data.aliasRows.length} 个`
                : ''}。
            </Typography>
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>旧 SKU</TableCell>
                    <TableCell>型号</TableCell>
                    <TableCell>新 SKU</TableCell>
                    <TableCell>换号说明</TableCell>
                    <TableCell>生效日期</TableCell>
                    <TableCell align="right">旧号停滞(天)</TableCell>
                    <TableCell>旧号最近动态</TableCell>
                    <TableCell>结论</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {data.aliasRows.map((r) => (
                    <TableRow key={r.sku} hover>
                      <TableCell sx={{ fontWeight: 600 }}>{r.sku}</TableCell>
                      <TableCell>{r.model || '—'}</TableCell>
                      <TableCell sx={{ fontWeight: 600, color: SEMANTIC.success }}>
                        {r.newSku || '—'}
                      </TableCell>
                      <TableCell>
                        <Typography variant="caption" color="text.secondary">{r.note || '—'}</Typography>
                      </TableCell>
                      <TableCell>{(r.effectiveAt || '').slice(0, 10) || '—'}</TableCell>
                      <TableCell align="right">{r.stuckDays ?? '—'}</TableCell>
                      <TableCell>{(r.lastDynamic || '').slice(0, 16) || '—'}</TableCell>
                      <TableCell>
                        <Typography variant="caption"
                          sx={{ color: r.newHasFlow ? 'text.secondary' : SEMANTIC.warning }}>
                          {r.conclusion}
                        </Typography>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </Box>
        </Paper>
      )}

      {!!(data.holdRows || []).length && (
        <Paper sx={{ mb: 2 }}>
          <Box sx={{ p: 2 }}>
            <Typography variant="subtitle2" gutterBottom>
              已豁免 · 不催办（{data.holdRows.length} 个）
            </Typography>
            <Typography variant="caption" color="text.secondary"
              sx={{ display: 'block', mb: 1 }}>
              判据：业务上<strong>已决定不再走完流程</strong>（渠道取消 / 并入其它型号 /
              末端环节实际不执行），人工登记后流程仍停在中途属正常，
              <strong>不计入催办</strong>。真源 = SKU登记表.xlsx<strong>「人工登记」</strong>子表
              {data.holdDef?.registered != null
                ? ` · 全库登记 ${data.holdDef.registered} 个，本品类命中 ${data.holdDef?.hitInCategory ?? data.holdRows.length} 个`
                : ''}。
            </Typography>
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>SKU</TableCell>
                    <TableCell>型号</TableCell>
                    <TableCell>卡点环节</TableCell>
                    <TableCell align="right">停滞(天)</TableCell>
                    <TableCell>最近动态</TableCell>
                    <TableCell>登记日期</TableCell>
                    <TableCell>豁免说明</TableCell>
                    <TableCell>结论</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {data.holdRows.map((r) => (
                    <TableRow key={r.sku} hover>
                      <TableCell sx={{ fontWeight: 600 }}>{r.sku}</TableCell>
                      <TableCell>{r.model || '—'}</TableCell>
                      <TableCell>
                        {r.pendingPart
                          ? `${r.pendingPart}·${r.pendingPartName || ''}`
                          : '—'}
                      </TableCell>
                      <TableCell align="right">{r.stuckDays ?? '—'}</TableCell>
                      <TableCell>{(r.lastDynamic || '').slice(0, 16) || '—'}</TableCell>
                      <TableCell>{(r.registeredAt || '').slice(0, 10) || '—'}</TableCell>
                      <TableCell>
                        <Typography variant="caption" color="text.secondary">
                          {r.reason || '—'}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Typography variant="caption" color="text.secondary">
                          {r.conclusion}
                        </Typography>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </Box>
        </Paper>
      )}

      <Paper>
        <Box sx={{ p: 2 }}>
          <Typography variant="subtitle2" gutterBottom>
            盯数清单（下次去钉钉导数时，优先重导以下卡点单据）
          </Typography>
          <Grid container spacing={1}>
            {data.watchList.map((w) => (
              <Grid item xs={12} sm={6} md={4} key={w.part}>
                <Paper variant="outlined" sx={{ p: 1.5 }}>
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {w.part} · {w.partName}
                    </Typography>
                    <Chip size="small" label={`${w.skuCount} 个`} />
                  </Box>
                  <Typography variant="caption" color="text.secondary"
                    sx={{ wordBreak: 'break-all' }}>
                    {w.skus.slice(0, 12).join('、')}{w.skus.length > 12 ? ` …共${w.skus.length}个` : ''}
                  </Typography>
                </Paper>
              </Grid>
            ))}
          </Grid>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5 }}>
            用法：按上面环节去钉钉重导对应审批单 → 重跑 ETL（或等数据文件更新）→ 本看板自动刷新预警状态
          </Typography>
        </Box>
      </Paper>
    </Box>
  );
}
