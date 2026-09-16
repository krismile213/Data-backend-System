import React, { useEffect, useState } from 'react';
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
  CircularProgress,
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
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState(null); // {severity, text}

  useEffect(() => {
    fetchRules()
      .then((d) => {
        setData(d);
        setDraft(JSON.parse(JSON.stringify(d.rules)));
      })
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <Alert severity="error">规则配置加载失败：{error}</Alert>;
  if (!data || !draft) {
    return <Box sx={{ display: 'flex', justifyContent: 'center', mt: 8 }}><CircularProgress /></Box>;
  }

  const dirty = JSON.stringify(draft) !== JSON.stringify(data.rules);

  const setSimple = (k, v) => {
    setDraft((d) => ({ ...d, [k]: v === '' ? '' : Number(v) }));
  };

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
      // 只提交有变化的键
      const patch = {};
      for (const k of SIMPLE_ORDER) {
        if (draft[k] !== data.rules[k]) patch[k] = draft[k];
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
