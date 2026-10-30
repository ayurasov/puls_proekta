import { createRequire } from 'node:module'
import { mkdirSync } from 'node:fs'

const require = createRequire(import.meta.url)
// playwright ставится в CI отдельно (npm i --no-save), локально — из глобальных модулей (NODE_PATH)
export const { chromium } = require('playwright')
export const BASE = process.env.E2E_BASE || 'http://127.0.0.1:4173'
export const PASSWORD = 'e2e-password-1'
export const SHOTS = process.env.E2E_SHOTS || '/tmp/puls-e2e-shots'
mkdirSync(SHOTS, { recursive: true })

let browser = null
export async function getBrowser() {
  if (!browser) browser = await chromium.launch()
  return browser
}
export async function closeBrowser() {
  if (browser) await browser.close()
  browser = null
}

export async function newPage({ width = 1500, height = 920 } = {}) {
  const b = await getBrowser()
  const context = await b.newContext({ viewport: { width, height }, locale: 'ru-RU', acceptDownloads: true })
  const page = await context.newPage()
  page.errors = []
  page.on('pageerror', (e) => page.errors.push(e.message))
  page.on('console', (m) => { if (m.type() === 'error' && !/401|UNAUTHORIZED/.test(m.text())) page.errors.push(m.text()) })
  return page
}

export async function login(page, login = 'admin', password = PASSWORD) {
  await page.goto(BASE + '/login')
  await page.fill('[data-testid=login]', login)
  await page.fill('[data-testid=password]', password)
  await page.click('[data-testid=login-submit]')
  await page.waitForURL((url) => !url.pathname.startsWith('/login'))
}

export async function api(page, method, path, body) {
  return page.evaluate(async ({ method, path, body }) => {
    const csrf = await (await fetch('/api/auth/csrf')).json()
    const res = await fetch('/api' + path, {
      method, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf.csrfToken },
      body: body ? JSON.stringify(body) : undefined,
    })
    return { status: res.status, body: await res.json().catch(() => null) }
  }, { method, path, body })
}

export async function openProject(page, title) {
  await page.goto(BASE + '/')
  await page.click(`a.card-link[aria-label="${title}"]`)
  await page.waitForSelector('.gantt .grid-row')
}

export async function waitSaved(page) {
  await page.waitForFunction(() => {
    const el = document.querySelector('[data-testid=save-status]')
    return el && /Сохранено/.test(el.textContent)
  }, null, { timeout: 15000 })
}

export function shot(page, name) {
  return page.screenshot({ path: `${SHOTS}/${name}.png` })
}
