import React, { useState } from 'react';
import { LoadingState, ErrorState, EmptyState } from '../components/StateViews.jsx';
import useApi from '../hooks/useApi.js';
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
  Switch,
  FormControlLabel,
  Button,
  Alert,
  Tooltip,
  Stack,
  Chip,
} from '@mui/material';
import SaveIcon from '@mui/icons-material/Save';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import { fetchRules, putRules, resetRules } from '../api.js';

const DEPT_COLOR = {
  产品: 'primary',
  运营: 'success',
  后勤: 'warning',
};

const SIMPLE_ORDER = [
  'product_target_generic', 'product_target_non_generic', 'ops_target',
  'proposal_stale_days', 'air_good', 'air_limit', 'sea_good', 'sea_limit',
  'country_min', 'stuck_days',
];

// 事中预警 / 上报 / 损益口径 的数值项（P1-2 / P1-3 / P0-7）
const INFLIGHT_NUM = [
  'inflight_warn_ratio', 'report_retention_days', 'loss_route_limit', 'mail_port',
];
// 文本项（原样提交；空串表示未配置）
const TEXT_KEYS = [
  'products_xlsx_path', 'registry_xlsx_path', 'mail_to', 'mail_host', 'mail_user', 'mail_auth_code',
];
// 敏感字段渲染成密码框
const SECRET_KEYS = new Set(['mail_auth_code']);

function NumField({ label, desc, value, onChange, error }) {
  return (
    <Tooltip title={desc} placement="top">
      <TextField
        size="small"
        type="number"
        label={label}
        value={value}
        error={!!error}
        helperText={error || ''}
        onChange={(e) => onChange(e.target.value)}
        sx={{ width: '100%' }}
      />
    </Tooltip>
  );
}

export default function Rules() {
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState(null); // {severity, text}

  const { data, setData, error } = useApi(() => fetchRules(), [], {
    // 拉到配置后同时初始化一份可编辑副本（data 存原值，draft 存改动）
    onSuccess: (d) => setDraft(JSON.parse(JSON.stringify(d.rules))),
  });

  if (error) return <ErrorState prefix="规则配置加载失败" error={error} />;
  if (!data || !draft) {
    return <LoadingState />;
  }

  const dirty = JSON.stringify(draft) !== JSON.stringify(data.rules);

  const setSimple = (k, v) => {
    setDraft((d) => ({ ...d, [k]: v === '' ? '' : Number(v) }));
  };

  /** 通用赋值（文本/布尔/数组均原样存） */
  const setVal = (k, v) => setDraft((d) => ({ ...d, [k]: v }));

  /** 日均兜底区间 {min,max} 子字段 */
  const setDaily = (field, v) => {
    setDraft((d) => ({
      ...d,
      loss_daily_default: {
        ...(d.loss_daily_default || {}),
        [field]: v === '' ? '' : Number(v),
      },
    }));
  };

  const meta = (k) => data.meta[k] || { label: k, desc: '' };

  // 只读调度信息（自动管线实际触发时刻，真源 scripts/install_auto_etl.ps1）
  const pipeline = data.pipeline;
  // 只读：规则快照（供离线同源件 流程复盘/route_check_sys.py 读取）的状态
  const snap = data.snapshot;

  const setSegCfg = (name, field, value) => {
    setDraft((d) => {
      const next = JSON.parse(JSON.stringify(d));
      next.seg_targets[name] = {
        ...(next.seg_targets[name] || {}),
        [field]: value,
      };
      return next;
    });
  };

  const doSave = async () => {
    setSaving(true);
    setMsg(null);
    try {
      // 只提交有变化的键（part_targets 是旧口径存档，不回传；seg_targets 单独处理）
      const patch = {};
      const skip = new Set(['seg_targets', 'part_targets']);
      for (const k of Object.keys(draft)) {
        if (skip.has(k)) continue;
        if (JSON.stringify(draft[k]) !== JSON.stringify(data.rules[k])) patch[k] = draft[k];
      }
      const st = {};
      for (const name of Object.keys(draft.seg_targets)) {
        const a = draft.seg_targets[name];
        const b = data.rules.seg_targets[name] || {};
        if (JSON.stringify(a) !== JSON.stringify(b)) st[name] = a;
      }
      if (Object.keys(st).length) patch.seg_targets = st;
      const res = await putRules(patch);
      setData((d) => ({ ...d, version: res.version, rules: res.rules }));
      setDraft(JSON.parse(JSON.stringify(res.rules)));
      setMsg({ severity: 'success', text: `已保存（v${res.version}），复盘与预警已按新规则重新计算` });
    } catch (e) {
      setMsg({ severity: 'error', text: e.message });
    } finally {
      setSaving(false);
    }
  };

  const doReset = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const res = await resetRules();
      setData((d) => ({ ...d, version: res.version, rules: res.rules }));
      setDraft(JSON.parse(JSON.stringify(res.rules)));
      setMsg({ severity: 'info', text: '已恢复默认规则' });
    } catch (e) {
      setMsg({ severity: 'error', text: e.message });
    } finally {
      setSaving(false);
    }
  };

  const simpleErr = (k) => {
    const v = draft[k];
    if (v === '' || v === null || v === undefined) return '不能为空';
    if (!Number.isFinite(Number(v)) || Number(v) < 0 || Number(v) > 3650) return '需为 0~3650';
    return '';
  };

  /** 预警系数是比例（0~1]，与天数类字段的 0~3650 校验不同 */
  const ratioErr = (k) => {
    const v = draft[k];
    if (v === '' || v === null || v === undefined) return '不能为空';
    if (!Number.isFinite(Number(v)) || Number(v) <= 0 || Number(v) > 1) return '需为 0~1 的比例';
    return '';
  };

  const segs = (data.segs || []).filter((s) => !s.gap);

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 2, flexWrap: 'wrap', gap: 2 }}>
        <Typography variant="h6">
          时效规则配置
          <Typography variant="caption" color="text.secondary" sx={{ ml: 2 }}>
            当前 v{data.version} · 保存后复盘总览与加急预警立即按新规则重算
          </Typography>
        </Typography>
        <Box sx={{ flexGrow: 1 }} />
        <Stack direction="row" spacing={1}>
          <Button variant="outlined" startIcon={<RestartAltIcon />}
            onClick={doReset} disabled={saving}>
            恢复默认
          </Button>
          <Button variant="contained" startIcon={<SaveIcon />}
            onClick={doSave} disabled={saving || !dirty}>
            {saving ? '保存中…' : dirty ? '保存修改' : '无修改'}
          </Button>
        </Stack>
      </Box>

      {msg && <Alert severity={msg.severity} sx={{ mb: 2 }} onClose={() => setMsg(null)}>{msg.text}</Alert>}

      {/* 简单阈值 */}
      <Paper sx={{ p: 2, mb: 2 }}>
        <Typography variant="subtitle2" gutterBottom>全局阈值</Typography>
        <Grid container spacing={2}>
          {SIMPLE_ORDER.map((k) => (
            <Grid item xs={12} sm={6} md={3} key={k}>
              <NumField
                label={data.meta[k]?.label || k}
                desc={data.meta[k]?.desc || ''}
                value={draft[k]}
                onChange={(v) => setSimple(k, v)}
                error={simpleErr(k)}
              />
            </Grid>
          ))}
        </Grid>
      </Paper>

      {/* 事中预警 / 上报 / 损益口径 */}
      <Paper sx={{ p: 2, mb: 2 }}>
        <Typography variant="subtitle2" gutterBottom>事中预警 / 上报 / 损益口径</Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
          预警系数=环节工期的百分比（段已进行天数 &gt; 目标×系数 且未完成 → 即将超期）；
          损益推迟天数按空运到货口径（到达即可开卖）计算；评级日均区间与单价来自商品资料表
          （A1 为档位说明，导入入口在数据管理页）。邮件默认关闭，开启后仅发下列收件人。
        </Typography>
        <Grid container spacing={2}>
          {INFLIGHT_NUM.map((k) => (
            <Grid item xs={12} sm={6} md={3} key={k}>
              <NumField
                label={meta(k).label}
                desc={meta(k).desc}
                value={draft[k]}
                onChange={(v) => setVal(k, v === '' ? '' : Number(v))}
                error={k === 'inflight_warn_ratio' ? ratioErr(k) : simpleErr(k)}
              />
            </Grid>
          ))}
          {TEXT_KEYS.map((k) => (
            <Grid item xs={12} sm={6} md={4} key={k}>
              <Tooltip title={meta(k).desc} placement="top">
                <TextField
                  size="small"
                  fullWidth
                  type={SECRET_KEYS.has(k) ? 'password' : 'text'}
                  label={meta(k).label}
                  value={draft[k] ?? ''}
                  onChange={(e) => setVal(k, e.target.value)}
                />
              </Tooltip>
            </Grid>
          ))}
          <Grid item xs={12} sm={6} md={3}>
            <Tooltip title={meta('loss_daily_default').desc} placement="top">
              <TextField size="small" type="number" label="日均兜底·下限"
                value={draft.loss_daily_default?.min ?? ''}
                onChange={(e) => setDaily('min', e.target.value)}
                sx={{ width: '100%' }} />
            </Tooltip>
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <Tooltip title={meta('loss_daily_default').desc} placement="top">
              <TextField size="small" type="number" label="日均兜底·上限"
                value={draft.loss_daily_default?.max ?? ''}
                onChange={(e) => setDaily('max', e.target.value)}
                sx={{ width: '100%' }} />
            </Tooltip>
          </Grid>
          <Grid item xs={12} sm={6} md={3}>
            <FormControlLabel
              control={
                <Switch
                  checked={!!draft.mail_enabled}
                  onChange={(e) => setVal('mail_enabled', e.target.checked)}
                />
              }
              label={meta('mail_enabled').label}
            />
          </Grid>
        </Grid>

        {/* 调度类信息：只读。这里曾是一个可编辑的「自动管线时刻」字段，
            但它不驱动任何调度（真源是 Windows 计划任务），改成如实展示。 */}
        <Alert
          severity={pipeline?.ok ? 'info' : 'warning'}
          sx={{ mt: 2 }}
          icon={false}
        >
          <Typography variant="body2" component="div">
            <strong>自动管线触发时刻（只读）：</strong>
            {pipeline?.ok
              ? `${(pipeline.times || []).join(' / ')}`
              : '读不到计划任务与安装脚本'}
            {`　·　计划任务 ${pipeline?.taskName || 'DataAdmin-AutoPipeline'}`}
          </Typography>
          <Typography variant="caption" color="text.secondary" component="div">
            真源 <code>{pipeline?.source || 'Windows 计划任务 DataAdmin-AutoPipeline'}</code>
            {pipeline?.updatedAt ? `（任务更新于 ${pipeline.updatedAt}）` : ''}
            —— 时刻由 Windows 计划任务决定，在页面上无法修改；要改请改脚本后重新注册
            （<code>powershell -NoProfile -ExecutionPolicy Bypass -File
            scripts\install_auto_etl.ps1</code>），并同步确认上游钉钉同步时间。
          </Typography>
          {/* 脚本默认值 vs 实际任务：两者不一致时必须显式说清，2026-11-05 就是
              页面照脚本显示 09:20/14:50、实际任务早已是 09:00/14:10。 */}
          {pipeline?.ok &&
            (pipeline.scriptTimes || []).length > 0 &&
            (pipeline.scriptTimes || []).join('/') !== (pipeline.times || []).join('/') && (
              <Typography variant="caption" color="warning.main" component="div">
                注意：安装脚本里仍写着 {pipeline.scriptTimes.join(' / ')}，与上面实际任务不一致
                —— 以上面的任务时刻为准；重装任务前先改脚本，否则会被改回去。
              </Typography>
            )}
        </Alert>

        {/* 规则快照：只读。离线同源件 `流程复盘/route_check_sys.py` 启动时读取本文件，
            以此跟随规则页的覆盖值；这里如实展示快照是否在位。路径不可在页面修改。 */}
        <Alert
          severity={snap?.exists ? 'info' : 'warning'}
          sx={{ mt: 2 }}
          icon={false}
        >
          <Typography variant="body2" component="div">
            <strong>规则快照（离线件读取）：</strong>
            {snap?.exists
              ? `已生成 · 规则版本 v${snap.rulesVersion} · 生成于 ${snap.generatedAt}`
              : '尚未生成 —— 离线件将使用代码默认值'}
          </Typography>
          <Typography variant="caption" color="text.secondary" component="div">
            <code>{snap?.path || '资产文件\\规则快照.json'}</code>
            {snap?.exists
              ? ' —— 离线件 流程复盘/route_check_sys.py 运行时会读取本文件；改完规则后重跑该脚本即生效。'
              : ' —— 保存一次规则或重启后端即会生成。'}
          </Typography>
        </Alert>
      </Paper>

      {/* 段级动作映射规则 */}
      <Paper>
        <Box sx={{ p: 2, pb: 0 }}>
          <Typography variant="subtitle2">环节绩效规则（实际动作映射）</Typography>
          <Typography variant="caption" color="text.secondary">
            每段 = 起点动作 → 终点动作；节点时间取值优先级：系统业务时间列 &gt; audit 上报 &gt; 审批动作。
            目标(天)=考核口径，用于复盘贡献计算与超时判定；「考核」关闭后该段不计贡献、不触发超时预警
          </Typography>
        </Box>
        <TableContainer sx={{ maxHeight: 560 }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>部门</TableCell>
                <TableCell>环节</TableCell>
                <TableCell>起点动作</TableCell>
                <TableCell>终点动作</TableCell>
                <TableCell align="right">目标(天)</TableCell>
                <TableCell align="center">是否考核</TableCell>
                <TableCell>说明</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {segs.map((s) => {
                const cfg = draft.seg_targets[s.name] || {};
                return (
                  <TableRow key={s.name}>
                    <TableCell>
                      <Chip size="small" variant="outlined"
                        color={DEPT_COLOR[s.dept] || 'default'}
                        label={s.dept} />
                    </TableCell>
                    <TableCell sx={{ fontWeight: 600 }}>{s.name}</TableCell>
                    <TableCell>
                      {s.from}
                      <Typography variant="caption" color="text.secondary" sx={{ ml: 0.5 }}>
                        ({s.fromPart})
                      </Typography>
                    </TableCell>
                    <TableCell>
                      {s.to}
                      <Typography variant="caption" color="text.secondary" sx={{ ml: 0.5 }}>
                        ({s.toPart})
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      <TextField size="small" type="number"
                        value={cfg.target ?? ''}
                        onChange={(e) => setSegCfg(s.name, 'target',
                          e.target.value === '' ? null : Number(e.target.value))}
                        sx={{ width: 90 }}
                        helperText={(cfg.target === null || cfg.target === undefined)
                          ? '留空 = 该段不设目标（不判超时）' : ''}
                        error={cfg.target !== null && cfg.target !== undefined
                          && (!Number.isFinite(Number(cfg.target)) || cfg.target < 0)} />
                    </TableCell>
                    <TableCell align="center">
                      <FormControlLabel
                        control={
                          <Switch
                            checked={!!cfg.available}
                            onChange={(e) => setSegCfg(s.name, 'available', e.target.checked)}
                          />
                        }
                        label=""
                      />
                    </TableCell>
                    <TableCell>
                      <Typography variant="caption" color="text.secondary">
                        {s.note}{s.owner ? ` · ${s.owner}` : ''}
                      </Typography>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
        <Typography variant="caption" color="text.secondary" sx={{ p: 2, display: 'block' }}>
          运输段（发货→到货）目标按渠道自动判定：空运 15 / 海运 35 / 卡航 35 天，不可在此调整；
          另有「采购→发货末批」段已包含在上表后勤分组中。
        </Typography>
      </Paper>
    </Box>
  );
}
