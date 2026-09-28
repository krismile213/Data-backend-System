import React, { Suspense, lazy, useEffect, useState } from 'react';
import {
  Box,
  AppBar,
  Toolbar,
  Typography,
  Drawer,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Divider,
  CircularProgress,
} from '@mui/material';
import DashboardIcon from '@mui/icons-material/Dashboard';
import TableChartIcon from '@mui/icons-material/TableChart';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import CompareArrowsIcon from '@mui/icons-material/CompareArrows';
import ManageSearchIcon from '@mui/icons-material/ManageSearch';
import AssessmentIcon from '@mui/icons-material/Assessment';
import NotificationImportantIcon from '@mui/icons-material/NotificationImportant';
import SettingsIcon from '@mui/icons-material/Settings';
import TimelineIcon from '@mui/icons-material/Timeline';
import HubIcon from '@mui/icons-material/Hub';
import AltRouteIcon from '@mui/icons-material/AltRoute';
import LocalShippingIcon from '@mui/icons-material/LocalShipping';
import CampaignIcon from '@mui/icons-material/Campaign';
import EditNoteIcon from '@mui/icons-material/EditNote';
import InsightsIcon from '@mui/icons-material/Insights';
import StorageIcon from '@mui/icons-material/Storage';
import MenuBookIcon from '@mui/icons-material/MenuBook';
import PendingActionsIcon from '@mui/icons-material/PendingActions';
import FactCheckIcon from '@mui/icons-material/FactCheck';
import BatchSelector from './components/BatchSelector.jsx';
import CategorySelector from './components/CategorySelector.jsx';
import { getCategory, setCategory } from './category';

// 按页面懒加载：首屏只加载看板，避免把 15 个页面（含 2.6MB ECharts）一次性打进首包
const Dashboard = lazy(() => import('./pages/Dashboard.jsx'));
const Records = lazy(() => import('./pages/Records.jsx'));
const Anomalies = lazy(() => import('./pages/Anomalies.jsx'));
const Compare = lazy(() => import('./pages/Compare.jsx'));
const Skus = lazy(() => import('./pages/Skus.jsx'));
const Review = lazy(() => import('./pages/Review.jsx'));
const Alerts = lazy(() => import('./pages/Alerts.jsx'));
const Rules = lazy(() => import('./pages/Rules.jsx'));
const Gantt = lazy(() => import('./pages/Gantt.jsx'));
const Scenarios = lazy(() => import('./pages/Scenarios.jsx'));
const Routes = lazy(() => import('./pages/Routes.jsx'));
const Transport = lazy(() => import('./pages/Transport.jsx'));
const Watch = lazy(() => import('./pages/Watch.jsx'));
const Fillins = lazy(() => import('./pages/Fillins.jsx'));
const History = lazy(() => import('./pages/History.jsx'));
const DataAdmin = lazy(() => import('./pages/DataAdmin.jsx'));
const Manual = lazy(() => import('./pages/Manual.jsx'));
const InflightDaily = lazy(() => import('./pages/InflightDaily.jsx'));
const Assessment = lazy(() => import('./pages/Assessment.jsx'));

const DRAWER_WIDTH = 220;

const NAV_ITEMS = [
  { key: 'dashboard', label: '时效看板', icon: <DashboardIcon /> },
  { key: 'inflight', label: '事中日报', icon: <PendingActionsIcon /> },
  { key: 'review', label: '复盘总览', icon: <AssessmentIcon /> },
  { key: 'assessment', label: '事后评估', icon: <FactCheckIcon /> },
  { key: 'gantt', label: '流程甘特', icon: <TimelineIcon /> },
  { key: 'scenarios', label: '场景诊断', icon: <HubIcon /> },
  { key: 'routes', label: '路线达标', icon: <AltRouteIcon /> },
  { key: 'transport', label: '货件运输', icon: <LocalShippingIcon /> },
  { key: 'alerts', label: '加急预警', icon: <NotificationImportantIcon /> },
  { key: 'watch', label: '联动提醒', icon: <CampaignIcon /> },
  { key: 'fillins', label: '手工填报', icon: <EditNoteIcon /> },
  { key: 'history', label: '趋势周报', icon: <InsightsIcon /> },
  { key: 'compare', label: '批次对比', icon: <CompareArrowsIcon /> },
  { key: 'skus', label: 'SKU 档案', icon: <ManageSearchIcon /> },
  { key: 'records', label: '明细查询', icon: <TableChartIcon /> },
  { key: 'anomalies', label: '异常排查', icon: <WarningAmberIcon /> },
  { key: 'rules', label: '规则配置', icon: <SettingsIcon /> },
  { key: 'manual', label: '操作手册', icon: <MenuBookIcon /> },
  { key: 'dataadmin', label: '数据管理', icon: <StorageIcon /> },
];

const PAGES = {
  dashboard: Dashboard,
  inflight: InflightDaily,
  review: Review,
  assessment: Assessment,
  gantt: Gantt,
  scenarios: Scenarios,
  routes: Routes,
  transport: Transport,
  alerts: Alerts,
  watch: Watch,
  fillins: Fillins,
  history: History,
  compare: Compare,
  skus: Skus,
  records: Records,
  anomalies: Anomalies,
  rules: Rules,
  manual: Manual,
  dataadmin: DataAdmin,
};

const PAGE_KEYS = NAV_ITEMS.map((i) => i.key);

/** 从 URL 读取页面与批次（非法 page 回退到看板） */
function readUrlState() {
  const p = new URLSearchParams(window.location.search);
  const page = p.get('page');
  return {
    page: PAGE_KEYS.includes(page) ? page : 'dashboard',
    batch: p.get('batch') || '',
  };
}

/** 把页面 / 批次 / 品类写回 URL：用户导航用 push（可后退），程序修正用 replace */
function writeUrl(page, batch, { push = false } = {}) {
  const p = new URLSearchParams(window.location.search);
  p.set('page', page);
  if (batch) p.set('batch', batch);
  else p.delete('batch');
  p.set('category', getCategory());      // 品类进 URL：链接可直接分享给同事
  const url = `${window.location.pathname}?${p.toString()}`;
  if (push) window.history.pushState(null, '', url);
  else window.history.replaceState(null, '', url);
}

export default function App() {
  const initial = readUrlState();
  const [page, setPage] = useState(initial.page);
  const [batch, setBatch] = useState(initial.batch);
  const [batches, setBatches] = useState([]);
  const [category, setCategoryState] = useState(getCategory());
  const [categories, setCategories] = useState([]);

  // 初次加载：拉**当前品类**的批次列表；URL 指定的批次无效时回退到最新批次
  useEffect(() => {
    fetch(`/api/batches?category=${encodeURIComponent(getCategory())}`)
      .then((r) => r.json())
      .then((data) => {
        const list = data.batches || [];
        setBatches(list);
        setCategories(data.categories || []);
        if (list.length === 0) return;
        const fromUrl = readUrlState().batch;
        const valid = list.some((b) => b.name === fromUrl);
        const next = valid ? fromUrl : list[list.length - 1].name;
        setBatch(next);
        writeUrl(readUrlState().page, next);
      })
      .catch(() => setBatches([]));
  }, []);

  // 浏览器前进 / 后退（若回退跨了品类，整页重载让所有页面按新品类重取）
  useEffect(() => {
    const onPop = () => {
      const s = readUrlState();
      const catInUrl = new URLSearchParams(window.location.search).get('category');
      if (catInUrl && catInUrl !== category) {
        setCategory(catInUrl);
        window.location.reload();
        return;
      }
      setPage(s.page);
      if (s.batch) setBatch(s.batch);
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, [category]);

  const goPage = (key) => {
    setPage(key);
    writeUrl(key, batch, { push: true });
  };

  const changeBatch = (name) => {
    setBatch(name);
    writeUrl(page, name, { push: true });
  };

  // 切换品类：规则与批次都换了一套，各页面的请求依赖不会自己变
  // （useApi 的 deps 里没有品类）→ 这里直接整页刷新，最稳也最不容易漏页面。
  const changeCategory = (name) => {
    if (!name || name === category) return;
    setCategory(name);
    setCategoryState(name);
    writeUrl(page, '', { push: true });   // 批次属于旧品类，一并清掉
    window.location.reload();
  };

  const Current = PAGES[page] || Dashboard;

  return (
    <Box sx={{ display: 'flex', height: '100vh' }}>
      <AppBar
        position="fixed"
        sx={{ zIndex: (t) => t.zIndex.drawer + 1, bgcolor: '#1a2740' }}
      >
        <Toolbar>
          <Typography variant="h6" noWrap sx={{ flexGrow: 1 }}>
            新品售前流程数据后台系统
          </Typography>
          <CategorySelector
            categories={categories}
            category={category}
            onChange={changeCategory}
          />
          <BatchSelector batches={batches} batch={batch} onChange={changeBatch} />
        </Toolbar>
      </AppBar>

      <Drawer
        variant="permanent"
        sx={{
          width: DRAWER_WIDTH,
          flexShrink: 0,
          '& .MuiDrawer-paper': {
            width: DRAWER_WIDTH,
            boxSizing: 'border-box',
          },
        }}
      >
        <Toolbar />
        <Divider />
        <List>
          {NAV_ITEMS.map((item) => (
            <ListItemButton
              key={item.key}
              selected={page === item.key}
              onClick={() => goPage(item.key)}
            >
              <ListItemIcon>{item.icon}</ListItemIcon>
              <ListItemText primary={item.label} />
            </ListItemButton>
          ))}
        </List>
        <Divider />
        <Box sx={{ p: 2 }}>
          <Typography variant="caption" color="text.secondary">
            数据仅在本机处理，不出外网
          </Typography>
        </Box>
      </Drawer>

      <Box component="main" sx={{ flexGrow: 1, p: 3, overflow: 'auto' }}>
        <Toolbar />
        <Suspense
          fallback={
            <Box sx={{ display: 'flex', justifyContent: 'center', mt: 8 }}>
              <CircularProgress />
            </Box>
          }
        >
          <Current batch={batch} batches={batches} />
        </Suspense>
      </Box>
    </Box>
  );
}
