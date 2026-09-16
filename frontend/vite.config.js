import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 本地数据后台：dev 模式把 /api 代理到 FastAPI 后端（8000 端口）
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
