import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  // 产物落在 web/dist，app/config.py 的 WEB_DIST 指向这里，FastAPI 挂根路径
  base: '/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '0.0.0.0',
    allowedHosts: true,
    port: 5173,
    // 开发时同源访问，音频 Range 请求和轮询都不需要额外 CORS
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
