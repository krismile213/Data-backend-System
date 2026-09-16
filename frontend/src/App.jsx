import React, { useEffect } from 'react';
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
import BatchSelector from './components/BatchSelector.jsx';
import Dashboard from './pages/Dashboard.jsx';
import Records from './pages/Records.jsx';
import Anomalies from './pages/Anomalies.jsx';
import Compare from './pages/Compare.jsx';
import Skus from './pages/Skus.jsx';
import Review from './pages/Review.jsx';
import Alerts from './pages/Alerts.jsx';
import Rules from './pages/Rules.jsx';
import Gantt from './pages/Gantt.jsx';
import Scenarios from './pages/Scenarios.jsx';
import Routes from './pages/Routes.jsx';
import Transport from './pages/Transport.jsx';
import Watch from './pages/Watch.jsx';
import Fillins from './pages/Fillins.jsx';
import History from './pages/History.jsx';

const DRAWER_WIDTH = 220;

const NAV_ITEMS = [
  { key: 'dashboard', label: '时效看板', icon: <DashboardIcon /> },
  { key: 'review', label: '复盘总览', icon: <AssessmentIcon /> },
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
];

export default function App() {
  const [page, setPage] = React.useState('dashboard');
  const [batch, setBatch] = React.useState('');
  const [batches, setBatches] = React.useState([]);

  // 初次加载：拉取批次列表，默认选第一个
  useEffect(() => {
    fetch('/api/batches')
      .then((r) => r.json())
      .then((data) => {
        const list = data.batches || [];
        setBatches(list);
        if (list.length > 0) setBatch(list[list.length - 1].name);
      })
      .catch(() => setBatches([]));
  }, []);

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
          <BatchSelector batches={batches} batch={batch} onChange={setBatch} />
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
              onClick={() => setPage(item.key)}
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
        {page === 'dashboard' && <Dashboard batch={batch} />}
        {page === 'review' && <Review />}
        {page === 'gantt' && <Gantt />}
        {page === 'scenarios' && <Scenarios />}
        {page === 'routes' && <Routes />}
        {page === 'transport' && <Transport />}
        {page === 'alerts' && <Alerts />}
        {page === 'watch' && <Watch />}
        {page === 'fillins' && <Fillins />}
        {page === 'history' && <History />}
        {page === 'rules' && <Rules />}
        {page === 'compare' && <Compare batches={batches} />}
        {page === 'skus' && <Skus />}
        {page === 'records' && <Records batch={batch} batches={batches} />}
        {page === 'anomalies' && <Anomalies batch={batch} />}
      </Box>
    </Box>
  );
}
