import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { realpathSync } from 'node:fs';

// root 必须用真实路径（realpath）。原因：本机 C:\Users\dell\Desktop 是指向
// D:\Desktop 的 junction。从 C 侧进入本目录时 process.cwd() 是 C 盘路径，但
// Node 解析模块给出的 id 是 D 盘真实路径，build 时 path.relative(C盘 root, D盘 id)
// 跨盘只能返回绝对路径 → rollup 报
//   [vite:build-html] The "fileName" ... must be strings that are neither
//   absolute nor relative paths, received "D:/.../frontend/index.html"
// 统一成 realpath 后两端一致；正常单盘路径下 realpath == cwd，行为不变。
const ROOT = realpathSync(process.cwd());

// 本地数据后台：dev 模式把 /api 代理到 FastAPI 后端（8000 端口）
//
// strictPort: 端口被占用时直接报错退出，而不是悄悄换到 5174。
// 否则 start.bat 仍会打开 5173，用户看到的是上一次残留的旧实例（白屏 / 旧页面）。
//
// API_TARGET: 后端地址可用环境变量覆盖（默认 8000）。用于后端换端口验证
// （如另起一个 8001 实例跑验证链，不打扰用户正在使用的 8000）。
const API_TARGET = process.env.API_TARGET || 'http://127.0.0.1:8000';

export default defineConfig({
  root: ROOT,
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
