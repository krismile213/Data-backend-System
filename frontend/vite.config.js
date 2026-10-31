import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 本地数据后台：dev 模式把 /api 代理到 FastAPI 后端（8000 端口）
//
// strictPort: 端口被占用时直接报错退出，而不是悄悄换到 5174。
// 否则 start.bat 仍会打开 5173，用户看到的是上一次残留的旧实例（白屏 / 旧页面）。
//
// API_TARGET: 后端地址可用环境变量覆盖（默认 8000）。用于后端换端口验证
// （如另起一个 8001 实例跑验证链，不打扰用户正在使用的 8000）。
const API_TARGET = process.env.API_TARGET || 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
});
