/**
 * Снимки экранов для руководств на демонстрационных данных (tools/demo/seed_demo.py).
 *
 *   npm run build && node tools/docs/screenshots.mjs [user|admin ...]
 *
 * Стенд: временная база SQLite с демо-данными, Flask на :5077 и собранный клиент (vite preview)
 * на :4177. Снимки 1440×900 при масштабе 1,5 сохраняются в docs/manuals/images/{user,admin}/ и
 * затем сжимаются `python3 tools/docs/optimize_png.py docs/manuals/images/user docs/manuals/images/admin`.
 * Переменные: PYTHON (по умолчанию python3).
 */
import { spawn, spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const { chromium } = require('playwright')
const root = resolve(fileURLToPath(import.meta.url), '../../..')
const python = process.env.PYTHON || 'python3'
const BASE = 'http://127.0.0.1:4177'
const PASSWORD = 'demo-password-1'
const only = process.argv.slice(2)
const dir = mkdtempSync(join(tmpdir(), 'puls-shots-'))
const env = { ...process.env, SQLALCHEMY_DATABASE_URI: `sqlite:///${join(dir, 'demo.db')}`, FLASK_ENV: 'e2e', RATELIMIT_ENABLED: 'false' }

const seed = spawnSync(python, [join(root, 'tools/demo/seed_demo.py')], { cwd: join(root, 'backend'), env, stdio: 'inherit' })
if (seed.status !== 0) process.exit(seed.status || 1)

const children = [
  spawn(python, ['-m', 'flask', '--app', 'wsgi', 'run', '--host', '127.0.0.1', '--port', '5077'], { cwd: join(root, 'backend'), env, stdio: 'ignore' }),
  spawn('npx', ['vite', 'preview', '--host', '127.0.0.1', '--port', '4177', '--strictPort'], { cwd: root, env: { ...process.env, VITE_BACKEND: 'http://127.0.0.1:5077' }, stdio: 'ignore' }),
]
async function waitFor(url) {
  for (let i = 0; i < 120; i += 1) {
    try { if ((await fetch(url)).ok) return } catch { /* ещё не поднялся */ }
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error(`${url} не ответил`)
}

let browser
async function session(login, { width = 1440, height = 900 } = {}) {
  const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1.5, locale: 'ru-RU', timezoneId: 'Europe/Moscow' })
  const page = await context.newPage()
  page.on('pageerror', (e) => console.error(`[${login}] ${e.message}`))
  if (login) {
    await page.goto(BASE + '/login')
    await page.fill('[data-testid=login]', login)
    await page.fill('[data-testid=password]', PASSWORD)
    await page.click('[data-testid=login-submit]')
    await page.waitForURL((url) => !url.pathname.startsWith('/login'))
  }
  return page
}

function saver(kind) {
  const folder = join(root, 'docs/manuals/images', kind)
  mkdirSync(folder, { recursive: true })
  return async (page, name, options = {}) => {
    await page.waitForTimeout(350) // анимации появления окон и меню
    await page.screenshot({ path: join(folder, `${name}.png`), ...options })
    console.log(`  ${kind}/${name}.png`)
  }
}

async function openProject(page, title, planTitle) {
  await page.goto(BASE + '/')
  await page.click(`a.card-link[aria-label="${title}"]`)
  await page.waitForSelector('.gantt .grid-row')
  if (planTitle) {
    await page.locator('.plan-tab', { hasText: planTitle }).first().click()
    await page.waitForTimeout(400)
  }
  await page.waitForTimeout(500)
}

async function closeOverlays(page) {
  await page.keyboard.press('Escape')
  await page.waitForTimeout(150)
  await page.keyboard.press('Escape')
}

const PORTAL = 'Портал поставщиков АО «Меридиан»'

async function userGuide() {
  const shot = saver('user')
  const anon = await session(null)
  await anon.goto(BASE + '/login')
  await anon.waitForSelector('[data-testid=login]')
  await shot(anon, '01-login')

  const page = await session('morozov')
  await page.waitForSelector('a.card-link')
  await shot(page, '02-projects')

  await page.click('[data-testid=new-project]')
  await page.fill('[data-testid=project-title]', 'Модернизация склада ТД «Радуга»')
  await shot(page, '03-new-project')
  await closeOverlays(page)

  await openProject(page, PORTAL)
  await page.click('button:has-text("Сегодня")')
  await page.waitForTimeout(300)
  await shot(page, '04-plan')

  await page.locator('.grid-row', { hasText: 'Интеграция с ERP заказчика' }).locator('.open-btn').click()
  await page.waitForSelector('.panel')
  await shot(page, '05-item-panel')
  await page.locator('.panel .tabs button', { hasText: 'Комментарии' }).click()
  await shot(page, '06-item-comments')
  await page.locator('.panel .tabs button', { hasText: 'История' }).click()
  await shot(page, '07-item-history')
  await closeOverlays(page)

  await page.locator('.grid-row', { hasText: 'Приёмка этапа 2' }).locator('.open-btn').click()
  await page.waitForSelector('.panel')
  await shot(page, '08-milestone-panel')
  await closeOverlays(page)

  await page.locator('.grid-row', { hasText: 'Личный кабинет поставщика' }).click({ button: 'right' })
  await shot(page, '09-context-menu')
  await closeOverlays(page)

  await page.click('.split-caret')
  await shot(page, '10-add-menu')
  await closeOverlays(page)

  await page.click('button:has-text("Фильтры")')
  for (const status of ['В работе', 'Заблокирована']) await page.locator('.filter-option', { hasText: status }).locator('input').click()
  await shot(page, '11-filters')
  await closeOverlays(page)
  await page.locator('.filter-bar button', { hasText: 'Сбросить' }).click()

  await page.click('button[title="Вид"]')
  await shot(page, '12-view-menu')
  await closeOverlays(page)

  await page.click('[data-testid=plan-add]')
  await shot(page, '13-plan-tabs')
  await closeOverlays(page)

  await page.click('[data-testid=history]')
  await page.waitForSelector('.modal .event')
  await shot(page, '14-history-journal')
  await page.click('.modal button:has-text("Версии")')
  await page.waitForSelector('.modal .snapshot')
  await shot(page, '15-history-versions')
  await closeOverlays(page)

  await page.click('button[title="Настройки графика"]')
  await page.waitForSelector('.modal .settings')
  await shot(page, '16-plan-settings')
  await closeOverlays(page)

  await page.click('button[title="Исполнители"]')
  await page.waitForSelector('.modal')
  await shot(page, '17-assignees')
  await closeOverlays(page)

  await page.click('button[title="Статистика"]')
  await page.waitForSelector('.modal')
  await shot(page, '18-stats')
  await closeOverlays(page)

  await page.click('button:has-text("Экспорт")')
  await shot(page, '19-export-menu')
  await closeOverlays(page)

  await page.click('.segmented button:has-text("Месяцы")')
  await page.click('button[title="Весь период на ширину экрана"]')
  await shot(page, '20-plan-months')
  await page.click('.segmented button:has-text("Недели")')

  // Совместная работа: коллега открыл тот же план
  const colleague = await session('lebedev')
  await openProject(colleague, PORTAL)
  await page.waitForTimeout(4500)
  await shot(page, '21-presence', { clip: { x: 0, y: 0, width: 1440, height: 160 } })
  await colleague.close()

  const viewer = await session('volkov')
  await openProject(viewer, PORTAL)
  await shot(viewer, '22-viewer')
  await viewer.click('button[title="Профиль"]')
  await viewer.waitForSelector('.modal')
  await shot(viewer, '23-profile')
}

async function adminGuide() {
  const shot = saver('admin')
  const page = await session('admin')
  await page.goto(BASE + '/admin/users')
  await page.waitForSelector('tbody tr td .person')
  await shot(page, '01-users')
  await page.locator('tr', { hasText: 'Ковалёва' }).locator('button[title="Изменить"]').click()
  await page.waitForSelector('.modal')
  await shot(page, '02-user-edit')
  await closeOverlays(page)

  await page.goto(BASE + '/admin/directions')
  await page.waitForTimeout(600)
  await shot(page, '03-directions')

  await page.goto(BASE + '/admin/access')
  await page.waitForTimeout(800)
  await shot(page, '04-access')
  await page.click('.segmented button:has-text("Кто что видит")')
  await page.waitForTimeout(800)
  await shot(page, '05-access-effective')

  await page.goto(BASE + '/admin/audit')
  await page.waitForSelector('tbody tr td')
  await page.waitForTimeout(500)
  await shot(page, '06-audit')

  await page.goto(BASE + '/admin/settings')
  await page.waitForTimeout(800)
  await shot(page, '07-settings-ldap')
  await page.click('.segmented button:has-text("Производственный календарь")')
  await page.waitForTimeout(600)
  await shot(page, '08-calendar')

  await page.goto(BASE + '/admin/trash')
  await page.waitForTimeout(600)
  await shot(page, '09-trash')

  await page.goto(BASE + '/about')
  await page.waitForTimeout(600)
  await shot(page, '10-about')
}

let code = 0
try {
  await waitFor('http://127.0.0.1:5077/api/health/ready')
  await waitFor(BASE + '/')
  // Поля дат браузера — в русском формате, как у сотрудников: Chromium в Linux берёт его из окружения
  browser = await chromium.launch({ env: { ...process.env, LANG: 'ru_RU.UTF-8', LANGUAGE: 'ru_RU:ru', LC_ALL: 'ru_RU.UTF-8' } })
  if (!only.length || only.includes('user')) await userGuide()
  if (!only.length || only.includes('admin')) await adminGuide()
} catch (err) {
  console.error(err)
  for (const context of browser?.contexts() || []) {
    for (const [i, p] of context.pages().entries()) await p.screenshot({ path: join(dir, `failure-${i}.png`) }).catch(() => {})
  }
  console.error(`Снимки в момент ошибки: ${dir}`)
  code = 1
} finally {
  if (browser) await browser.close()
  for (const child of children) child.kill('SIGTERM')
}
process.exit(code)
