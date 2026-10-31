import React from 'react';
import { TextField, MenuItem, InputAdornment, Tooltip, Box, Chip }
  from '@mui/material';
import CategoryIcon from '@mui/icons-material/Category';

/**
 * CategorySelector — 顶栏品类下拉（手机膜 / 手机壳）
 *
 * 品类决定后端用哪一套规则（节点链、段目标、路线评级线）与哪些批次数据。
 * 手机膜的考核起点是「取数」，手机壳没有取数节点、起点是「提案」——
 * 两套口径不能混算，所以切换品类必须让所有页面重新取数（由 App 触发整页刷新）。
 *
 * ⚠️ 手机壳的目标天/评级线目前是**继承手机膜的名义值**（`targetsProvisional`），
 * 尚未业务校准。原先把标记只挂在展开后的菜单项里，收起状态完全看不出来——
 * 而顶栏是每一页都看得见的地方，所以这里把标记也放到了**收起态的显示值**上。
 */
export default function CategorySelector({ categories, category, onChange }) {
  const list = (categories && categories.length
    ? categories
    : [{ category: category, categoryLabel: category, targetsProvisional: false }]);

  const current = list.find((c) => (c.category || c.key) === category);
  const provisional = !!current?.targetsProvisional;

  return (
    <Tooltip title="切换品类（决定用哪一套流程规则与哪些批次数据）">
      <TextField
        select
        size="small"
        variant="outlined"
        label="品类"
        value={category}
        onChange={(e) => onChange(e.target.value)}
        sx={{
          minWidth: 220,
          mr: 1.5,
          bgcolor: 'rgba(255,255,255,0.95)',
          borderRadius: 1,
        }}
        SelectProps={{
          renderValue: (v) => {
            const hit = list.find((c) => (c.category || c.key) === v);
            return (
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                {hit?.categoryLabel || v}
                {hit?.targetsProvisional ? (
                  <Tooltip title="该品类的目标天 / 评级线仍是继承手机膜的名义值（待业务校准）——本页所有「目标 / 超期 / 预警 / 评级判定」仅供预警参考，不得当既定 KPI">
                    <Chip size="small" color="warning" variant="outlined"
                      label="待校准" sx={{ height: 19, fontSize: 11 }} />
                  </Tooltip>
                ) : null}
              </Box>
            );
          },
        }}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <CategoryIcon fontSize="small" />
            </InputAdornment>
          ),
        }}
      >
        {list.map((c) => {
          const key = c.category || c.key;
          return (
            <MenuItem key={key} value={key}>
              {c.categoryLabel || key}
              {c.targetsProvisional ? '（目标天待校准）' : ''}
            </MenuItem>
          );
        })}
      </TextField>
    </Tooltip>
  );
}
