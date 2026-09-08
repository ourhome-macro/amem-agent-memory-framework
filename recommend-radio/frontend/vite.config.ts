import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

const devProxyTarget = process.env.VITE_DEV_PROXY_TARGET || 'http://localhost:5000'
const devSseProxyTarget = process.env.VITE_SSE_PROXY_TARGET || 'http://localhost:18080'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src')
    }
  },
  server: {
    port: 3000,
    proxy: {
      '/api/agent/events': {
        target: devSseProxyTarget,
        changeOrigin: true
      },
      '/api': {
        target: devProxyTarget,
        changeOrigin: true
      },
      '/socket.io': {
        target: devProxyTarget,
        ws: true
      }
    }
  }
})
