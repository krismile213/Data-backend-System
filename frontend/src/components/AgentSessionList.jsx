import React, { useState } from 'react';
import {
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  IconButton,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import DriveFileRenameOutlineIcon from '@mui/icons-material/DriveFileRenameOutline';
import { isValidSessionName } from '../agentSession.js';

/**
 * AgentSessionList —— 助手会话侧栏（列表 / 切换 / 新建 / 重命名 / 删除）
 *
 * 纯展示 + 回调组件：所有副作用（API 调用、切换会话）由父组件 AgentPanel 承担，
 * 这里只负责 UI 与本地弹窗状态（重命名 / 删除确认）。
 *
 * props:
 *   sessions      — [{name, size, mtime}]（已按最近活动倒序）
 *   current       — 当前会话名
 *   loading       — 列表是否加载中
 *   onSelect(name)— 切换到某会话
 *   onCreate()    — 新建会话
 *   onRename(name, newName) — 重命名
 *   onDelete(name)          — 删除
 */
export default function AgentSessionList({
  sessions = [],
  current = '',
  loading = false,
  onSelect,
  onCreate,
  onRename,
  onDelete,
}) {
  const [renameTarget, setRenameTarget] = useState(null); // {name} | null
  const [renameValue, setRenameValue] = useState('');
  const [deleteTarget, setDeleteTarget] = useState(null); // {name} | null

  const openRename = (name) => {
    setRenameTarget({ name });
    setRenameValue(name);
  };

  const closeRename = () => {
    setRenameTarget(null);
    setRenameValue('');
  };

  const renameInvalid = renameValue.length > 0 && !isValidSessionName(renameValue);
  const renameEmpty = renameValue.length === 0;

  const submitRename = () => {
    const target = renameTarget;
    if (!target) return;
    const next = renameValue.trim();
    if (renameEmpty || renameInvalid) return;
    if (next === target.name) { closeRename(); return; }
    closeRename();
    onRename?.(target.name, next);
  };

  const submitDelete = () => {
    const target = deleteTarget;
    setDeleteTarget(null);
    if (target) onDelete?.(target.name);
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <Box sx={{ display: 'flex', alignItems: 'center', px: 1.5, py: 1 }}>
        <Typography variant="subtitle2" sx={{ flexGrow: 1, fontWeight: 700 }}>
          会话
        </Typography>
        <Tooltip title="新建会话">
          <IconButton size="small" onClick={() => onCreate?.()} aria-label="新建会话">
            <AddIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>

      {loading && sessions.length === 0 ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
          <CircularProgress size={20} />
        </Box>
      ) : sessions.length === 0 ? (
        <Typography variant="caption" color="text.secondary" sx={{ px: 1.5, py: 1 }}>
          暂无会话。点右上「+」新建。
        </Typography>
      ) : (
        <List dense sx={{ overflow: 'auto', flexGrow: 1, py: 0 }}>
          {sessions.map((s) => {
            const name = s && s.name ? s.name : '';
            const selected = name === current;
            return (
              <ListItem
                key={name}
                disablePadding
                secondaryAction={(
                  <Stack direction="row" spacing={0}>
                    <Tooltip title="重命名">
                      <IconButton
                        size="small"
                        edge="end"
                        aria-label={`重命名 ${name}`}
                        onClick={() => openRename(name)}
                      >
                        <DriveFileRenameOutlineIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="删除">
                      <IconButton
                        size="small"
                        edge="end"
                        aria-label={`删除 ${name}`}
                        onClick={() => setDeleteTarget({ name })}
                      >
                        <DeleteOutlineIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </Stack>
                )}
              >
                <ListItemButton
                  selected={selected}
                  onClick={() => onSelect?.(name)}
                  sx={{ pr: 9 }}
                >
                  <ListItemText
                    primary={name}
                    secondary={s && s.mtime ? s.mtime : ''}
                    primaryTypographyProps={{ noWrap: true, fontSize: 14 }}
                    secondaryTypographyProps={{ fontSize: 11 }}
                  />
                </ListItemButton>
              </ListItem>
            );
          })}
        </List>
      )}

      {/* 重命名弹窗（不用 window.prompt，保持 MUI 一致） */}
      <Dialog open={Boolean(renameTarget)} onClose={closeRename} fullWidth maxWidth="xs">
        <DialogTitle>重命名会话</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 1 }}>
            只能用字母、数字、`-`、`_`（其它字符会被 Agent 清洗掉，导致找不到会话）。
          </DialogContentText>
          <TextField
            autoFocus
            fullWidth
            size="small"
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            error={renameInvalid}
            helperText={renameInvalid ? '仅允许字母、数字、- 和 _' : ' '}
            onKeyDown={(e) => { if (e.key === 'Enter') submitRename(); }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={closeRename}>取消</Button>
          <Button
            variant="contained"
            disabled={renameEmpty || renameInvalid}
            onClick={submitRename}
          >
            确定
          </Button>
        </DialogActions>
      </Dialog>

      {/* 删除确认 */}
      <Dialog open={Boolean(deleteTarget)} onClose={() => setDeleteTarget(null)} fullWidth maxWidth="xs">
        <DialogTitle>删除会话</DialogTitle>
        <DialogContent>
          <DialogContentText>
            将删除会话「{deleteTarget ? deleteTarget.name : ''}」及其历史，不可恢复。
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteTarget(null)}>取消</Button>
          <Button color="error" variant="contained" onClick={submitDelete}>删除</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
