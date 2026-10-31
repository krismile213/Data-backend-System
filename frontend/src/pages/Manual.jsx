import React, { useMemo, useState } from 'react';
import {
  Box,
  Paper,
  Typography,
  List,
  ListItemButton,
  ListItemText,
  Divider,
} from '@mui/material';
import { fetchManuals, fetchManual } from '../api.js';
import PageHeader from '../components/PageHeader.jsx';
import { LoadingState, ErrorState } from '../components/StateViews.jsx';
import { SEMANTIC } from '../theme.js';
import useApi from '../hooks/useApi.js';

/* ------------------------------------------------------------------
 * 轻量 markdown 渲染（零依赖）
 * 仅覆盖本项目文档实际用到的语法：标题 / 表格 / 列表（含复选框）/
 * 引用 / 代码块 / 分隔线 / 粗体 / 行内代码 / <br>
 * ------------------------------------------------------------------ */

/** 行内元素：**粗体** 与 `行内代码` */
function renderInline(text, keyPrefix = '') {
  const nodes = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let m;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith('**')) {
      nodes.push(<strong key={`${keyPrefix}-b${i}`}>{tok.slice(2, -2)}</strong>);
    } else {
      nodes.push(
        <Box
          component="code"
          key={`${keyPrefix}-c${i}`}
          sx={{
            bgcolor: 'action.hover',
            px: 0.5,
            borderRadius: 0.5,
            fontFamily: 'monospace',
            fontSize: '0.9em',
          }}
        >
          {tok.slice(1, -1)}
        </Box>
      );
    }
    i += 1;
    last = m.index + tok.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

/** 单元格/段落：把 <br> 还原成换行 */
function renderRich(text, keyPrefix = '') {
  const segs = String(text).split(/<br\s*\/?>/i);
  return segs.map((s, i) => (
    <React.Fragment key={`${keyPrefix}-s${i}`}>
      {i > 0 && <br />}
      {renderInline(s, `${keyPrefix}-${i}`)}
    </React.Fragment>
  ));
}

function parseMarkdown(md) {
  const lines = md.split(/\r?\n/);
  const blocks = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim().startsWith('```')) {
      const buf = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        buf.push(lines[i]);
        i += 1;
      }
      i += 1;
      blocks.push({ type: 'code', text: buf.join('\n') });
      continue;
    }

    if (/^---+$/.test(line.trim())) {
      blocks.push({ type: 'hr' });
      i += 1;
      continue;
    }

    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      blocks.push({ type: 'h', level: heading[1].length, text: heading[2] });
      i += 1;
      continue;
    }

    if (line.trim().startsWith('>')) {
      const buf = [];
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        buf.push(lines[i].trim().replace(/^>\s?/, ''));
        i += 1;
      }
      blocks.push({ type: 'quote', lines: buf });
      continue;
    }

    if (
      line.trim().startsWith('|') &&
      i + 1 < lines.length &&
      /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])
    ) {
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        rows.push(
          lines[i]
            .trim()
            .replace(/^\||\|$/g, '')
            .split('|')
            .map((s) => s.trim())
        );
        i += 1;
      }
      blocks.push({ type: 'table', header: rows[0], body: rows.slice(2) });
      continue;
    }

    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ''));
        i += 1;
      }
      blocks.push({ type: 'list', items });
      continue;
    }

    if (!line.trim()) {
      i += 1;
      continue;
    }

    const buf = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^(\s*([-*]|\d+\.)\s|#{1,4}\s|>|\||```)/.test(lines[i])
    ) {
      buf.push(lines[i].trim());
      i += 1;
    }
    if (buf.length) blocks.push({ type: 'p', text: buf.join(' ') });
    else i += 1;
  }

  return blocks;
}

function MarkdownView({ content }) {
  const blocks = useMemo(() => parseMarkdown(content), [content]);

  return (
    <Box>
      {blocks.map((b, idx) => {
        if (b.type === 'h') {
          const variant = b.level === 1 ? 'h5' : b.level === 2 ? 'h6' : 'subtitle1';
          return (
            <Typography
              key={idx}
              variant={variant}
              sx={{
                fontWeight: 700,
                mt: b.level === 1 ? 0.5 : 3,
                mb: 1,
                pb: b.level <= 2 ? 0.5 : 0,
                borderBottom: b.level <= 2 ? '1px solid' : 'none',
                borderColor: 'divider',
              }}
            >
              {renderInline(b.text, `h${idx}`)}
            </Typography>
          );
        }
        if (b.type === 'hr') return <Divider key={idx} sx={{ my: 2 }} />;
        if (b.type === 'code') {
          return (
            <Paper
              key={idx}
              variant="outlined"
              sx={{ p: 1.5, my: 1.5, bgcolor: SEMANTIC.codeBg, overflowX: 'auto' }}
            >
              <Box component="pre" sx={{ m: 0, fontFamily: 'monospace', fontSize: 12 }}>
                {b.text}
              </Box>
            </Paper>
          );
        }
        if (b.type === 'quote') {
          return (
            <Box
              key={idx}
              sx={{
                borderLeft: `3px solid ${SEMANTIC.muted}`,
                pl: 2,
                my: 1.5,
                bgcolor: SEMANTIC.neutralBg,
                py: 1,
                pr: 1,
              }}
            >
              {b.lines.map((t, j) => (
                <Typography key={j} variant="body2" color="text.secondary" sx={{ lineHeight: 1.8 }}>
                  {renderRich(t, `q${idx}-${j}`)}
                </Typography>
              ))}
            </Box>
          );
        }
        if (b.type === 'table') {
          return (
            <Paper key={idx} variant="outlined" sx={{ my: 1.5, overflowX: 'auto' }}>
              <Box component="table" sx={{ borderCollapse: 'collapse', width: '100%' }}>
                <Box component="thead">
                  <Box component="tr" sx={{ bgcolor: SEMANTIC.surface }}>
                    {b.header.map((h, j) => (
                      <Box
                        component="th"
                        key={j}
                        sx={{
                          textAlign: 'left',
                          p: 1,
                          fontSize: 13,
                          fontWeight: 700,
                          borderBottom: '1px solid',
                          borderColor: 'divider',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {renderRich(h, `th${idx}-${j}`)}
                      </Box>
                    ))}
                  </Box>
                </Box>
                <Box component="tbody">
                  {b.body.map((row, r) => (
                    <Box
                      component="tr"
                      key={r}
                      sx={{ '&:hover': { bgcolor: 'action.hover' } }}
                    >
                      {row.map((cell, c) => (
                        <Box
                          component="td"
                          key={c}
                          sx={{
                            p: 1,
                            fontSize: 13,
                            verticalAlign: 'top',
                            borderBottom: '1px solid',
                            borderColor: 'divider',
                          }}
                        >
                          {renderRich(cell, `td${idx}-${r}-${c}`)}
                        </Box>
                      ))}
                    </Box>
                  ))}
                </Box>
              </Box>
            </Paper>
          );
        }
        if (b.type === 'list') {
          return (
            <Box component="ul" key={idx} sx={{ pl: 3, my: 1 }}>
              {b.items.map((it, j) => {
                const cb = /^\[([ xX])\]\s*(.*)$/.exec(it);
                return (
                  <Box component="li" key={j} sx={{ mb: 0.5 }}>
                    <Typography variant="body2" sx={{ lineHeight: 1.8 }}>
                      {cb && <Box component="span" sx={{ mr: 0.5 }}>{cb[1].trim() ? '☑' : '☐'}</Box>}
                      {renderRich(cb ? cb[2] : it, `li${idx}-${j}`)}
                    </Typography>
                  </Box>
                );
              })}
            </Box>
          );
        }
        return (
          <Typography key={idx} variant="body2" sx={{ my: 1, lineHeight: 1.8 }}>
            {renderRich(b.text, `p${idx}`)}
          </Typography>
        );
      })}
    </Box>
  );
}

/* ------------------------------------------------------------------ */

export default function Manual() {
  const [active, setActive] = useState('');

  // 统一请求 hook：手册目录 + 当前手册正文
  const { data: list, error: listErr } = useApi(
    () => fetchManuals().then((d) => d.items || []),
    [],
    {
      initialData: [],
      // 默认选中第一份已存在的手册
      onSuccess: (items) => {
        const first = items.find((i) => i.exists);
        if (first) setActive(first.key);
      },
    },
  );

  const { data: doc, loading, error: docErr } = useApi(
    () => fetchManual(active),
    [active],
    { enabled: !!active },
  );

  const error = listErr || docErr;

  return (
    <Box>
      <PageHeader
        title="操作手册"
        subtitle="SOP 与岗位交接文档 —— 直接编辑仓库 docs/ 目录下的 .md 文件即可更新，刷新页面生效"
      />

      {error && <ErrorState error={error} sx={{ mt: 2 }} />}

      <Box sx={{ display: 'flex', gap: 2, alignItems: 'flex-start', mt: 2 }}>
        <Paper sx={{ width: 250, flexShrink: 0, p: 1 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 700, px: 1, py: 0.5 }}>
            手册目录
          </Typography>
          <List dense disablePadding>
            {list.map((it) => (
              <ListItemButton
                key={it.key}
                selected={active === it.key}
                disabled={!it.exists}
                onClick={() => setActive(it.key)}
                sx={{ borderRadius: 1, alignItems: 'flex-start' }}
              >
                <ListItemText
                  primary={it.title}
                  secondary={it.exists ? `更新于 ${it.updatedAt}` : '文件缺失'}
                  primaryTypographyProps={{ fontSize: 13, fontWeight: 500 }}
                  secondaryTypographyProps={{ fontSize: 11 }}
                />
              </ListItemButton>
            ))}
          </List>
        </Paper>

        <Paper sx={{ flexGrow: 1, minWidth: 0, p: 3 }}>
          {loading && <LoadingState size={26} />}
          {!loading && doc && <MarkdownView content={doc.content} />}
        </Paper>
      </Box>
    </Box>
  );
}
