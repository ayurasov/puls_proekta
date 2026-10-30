import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// В разработке /api проксируется на Flask (backend: flask --app wsgi run --port 5000).
// Руководства (docs/manuals: текст, снимки экранов, PDF, DOCX) входят в сборку как файлы
// с хэшем в имени — см. src/docs/guides.js.
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: { '/api': { target: process.env.VITE_BACKEND || 'http://127.0.0.1:5000', changeOrigin: false } },
  },
  build: { chunkSizeWarningLimit: 900 },
})
