import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5188,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8200', changeOrigin: true },
      // Presets remain in frontend/public/avatars. Only content-addressed uploads live in
      // the backend's persistent directory, so proxy that subset without shadowing presets.
      '^/avatars/u[1-9][0-9]{0,9}-': { target: 'http://127.0.0.1:8200', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        // 大依赖独立分包：echarts 只在图表页用到，路由懒加载后不再进首包
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          echarts: ['echarts', 'echarts-for-react'],
          motion: ['framer-motion'],
        },
      },
    },
  },
})
