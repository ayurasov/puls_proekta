/**
 * Запуск e2e-тестов: база SQLite с перенесённым снимком 1.0.1, Flask на :5055, собранный
 * клиент (vite preview) на :4173 с проксированием /api, тесты node:test + Playwright.
 *
 *   npm run build && npm run e2e
 * Переменные: PYTHON (по умолчанию python3), E2E_KEEP=1 — не останавливать стенд,
 * E2E_API_PORT и E2E_WEB_PORT — порты стенда (по умолчанию 5055 и 4173).
 */
import { spawn, spawnSync } from 'node:child_process'
import { mkdtempSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(fileURLToPath(import.meta.url), '../..')
const python = process.env.PYTHON || 'python3'
const dir = mkdtempSync(join(tmpdir(), 'puls-e2e-'))
const dbUrl = `sqlite:///${join(dir, 'e2e.db')}`
const apiPort = process.env.E2E_API_PORT || '5055'
const webPort = process.env.E2E_WEB_PORT || '4173'
const env = { ...process.env, SQLALCHEMY_DATABASE_URI: dbUrl, FLASK_ENV: 'e2e', RATELIMIT_ENABLED: 'false' }

const seed = spawnSync(python, ['scripts/e2e_seed.py', join(root, 'legacy/snapshot/puls-2026-10-30.db')], { cwd: join(root, 'backend'), env, stdio: 'inherit' })
if (seed.status !== 0) process.exit(seed.status || 1)

const children = []
// Вывод серверов — в консоль (E2E_VERBOSE=1) или в никуда, но не в непрочитанный канал:
// переполненный канал останавливает процесс сервера.
function start(cmd, args, options) {
  const output = process.env.E2E_VERBOSE ? 'inherit' : 'ignore'
  const child = spawn(cmd, args, { ...options, stdio: ['ignore', output, output] })
  children.push(child)
  return child
}
start(python, ['-m', 'flask', '--app', 'wsgi', 'run', '--host', '127.0.0.1', '--port', apiPort], { cwd: join(root, 'backend'), env })
start('npx', ['vite', 'preview', '--host', '127.0.0.1', '--port', webPort, '--strictPort'], { cwd: root, env: { ...process.env, VITE_BACKEND: `http://127.0.0.1:${apiPort}` } })

async function waitFor(url) {
  for (let i = 0; i < 120; i += 1) {
    try { const res = await fetch(url); if (res.ok) return } catch { /* ещё не поднялся */ }
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error(`${url} не ответил`)
}

let code = 1
try {
  await waitFor(`http://127.0.0.1:${apiPort}/api/health/ready`)
  await waitFor(`http://127.0.0.1:${webPort}/`)
  // E2E_ONLY=04,05 — только наборы с такими префиксами
  const only = (process.env.E2E_ONLY || '').split(',').filter(Boolean)
  const files = readdirSync(join(root, 'e2e/tests')).filter((f) => f.endsWith('.test.mjs'))
    .filter((f) => !only.length || only.some((prefix) => f.startsWith(prefix))).sort().map((f) => join(root, 'e2e/tests', f))
  const run = spawn(process.execPath, ['--test', '--test-concurrency=1', '--test-timeout=120000', ...files], {
    cwd: root, stdio: 'inherit', env: { ...process.env, E2E_BASE: `http://127.0.0.1:${webPort}`, E2E_SHOTS: process.env.E2E_SHOTS || join(dir, 'shots') },
  })
  code = await new Promise((resolveExit) => run.on('exit', (status) => resolveExit(status ?? 1)))
} finally {
  if (!process.env.E2E_KEEP) for (const child of children) child.kill('SIGTERM')
}
process.exit(code)
