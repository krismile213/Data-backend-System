import React from 'react';
import { TextField, MenuItem, InputAdornment, Tooltip } from '@mui/material';
import StorageIcon from '@mui/icons-material/Storage';

/**
 * BatchSelector — 顶栏批次下拉选择，切换后全站数据随之刷新
 */
export default function BatchSelector({ batches, batch, onChange }) {
  return (
    <Tooltip title="切换批次（数据目录）">
      <TextField
        select
        size="small"
        variant="outlined"
        label="数据批次"
        value={batch}
        onChange={(e) => onChange(e.target.value)}
        sx={{ minWidth: 220, bgcolor: 'rgba(255,255,255,0.95)', borderRadius: 1 }}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <StorageIcon fontSize="small" />
            </InputAdornment>
          ),
        }}
      >
        {batches.length === 0 && (
          <MenuItem value="" disabled>
            未发现批次
          </MenuItem>
        )}
        {batches.map((b) => (
          <MenuItem key={b.name} value={b.name}>
            {b.name}（{b.fileCount} 个文件）
          </MenuItem>
        ))}
      </TextField>
    </Tooltip>
  );
}
