import React from 'react';
import ReactDOM from 'react-dom/client';
import { ThemeProvider, createTheme, CssBaseline } from '@mui/material';
import App from './App.jsx';
import './index.css';

const theme = createTheme({
  palette: {
    mode: 'light',
    primary: { main: '#1e5eff' },
    secondary: { main: '#c62828' },
    background: { default: '#f4f6f8' },
  },
  typography: {
    fontFamily:
      "'Microsoft YaHei', 'PingFang SC', 'Helvetica Neue', Arial, sans-serif",
  },
});

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <App />
    </ThemeProvider>
  </React.StrictMode>
);
