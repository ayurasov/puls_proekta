import { createReadStream, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Руководство пользователя (PDF) открывается из «О программе» по адресу /user-guide.pdf: в Docker его отдаёт
// nginx, при разработке и в vite preview — этот обработчик прямо из docs/manuals
const USER_GUIDE = fileURLToPath(new URL('./docs/manuals/user-guide.pdf', import.meta.url))
function userGuide() {
  const serve = (req, res, next) => {
    if (req.url?.split('?')[0] !== '/user-guide.pdf' || !existsSync(USER_GUIDE)) return next()
    res.setHeader('Content-Type', 'application/pdf')
    createReadStream(USER_GUIDE).pipe(res)
  }
  return {
    name: 'puls-user-guide',
    configureServer: (server) => { server.middlewares.use(serve) },
    configurePreviewServer: (server) => { server.middlewares.use(serve) },
  }
}

// В разработке /api проксируется на Flask (backend: flask --app wsgi run --port 5000)
export default defineConfig({
  plugins: [vue(), userGuide()],
  server: {
    port: 5173,
    proxy: { '/api': { target: process.env.VITE_BACKEND || 'http://127.0.0.1:5000', changeOrigin: false } },
  },
  build: { chunkSizeWarningLimit: 900 },
})
