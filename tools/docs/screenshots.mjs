/**
 * Снимки экранов для руководств на демонстрационных данных (tools/demo/seed_demo.py).
 *
 *   npm run build && node tools/docs/screenshots.mjs [user|admin ...]
 *
 * Стенд: временная база SQLite с демо-данными, Flask на :5077 и собранный клиент (vite preview)
 * на :4177, заглушка Jira на :5098 (задачи проекта «Портал поставщиков») и «По Делу» на :5096 (вход сервисной
 * учётной записи). Снимки 1440×900 при масштабе
 * 1,5 сохраняются в docs/manuals/images/{user,admin}/ и затем сжимаются
 * `python3 tools/docs/optimize_png.py docs/manuals/images/user docs/manuals/images/admin`.
 * Переменные: PYTHON (по умолчанию python3), SHOTS_API_PORT, SHOTS_WEB_PORT, SHOTS_JIRA_PORT, SHOTS_PODELU_PORT —
 * другие порты.
 */
import { spawn, spawnSync } from 'node:child_process'
import { createServer } from 'node:http'
import { mkdirSync, mkdtempSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { startJiraStub } from '../../e2e/jiraStub.mjs'

const require = createRequire(import.meta.url)
const { chromium } = require('playwright')
const root = resolve(fileURLToPath(import.meta.url), '../../..')
const python = process.env.PYTHON || 'python3'
const API_PORT = process.env.SHOTS_API_PORT || '5077'
const WEB_PORT = process.env.SHOTS_WEB_PORT || '4177'
const JIRA_PORT = Number(process.env.SHOTS_JIRA_PORT || 5098)
const BASE = `http://127.0.0.1:${WEB_PORT}`
const JIRA = `http://127.0.0.1:${JIRA_PORT}`
const PODELU_PORT = Number(process.env.SHOTS_PODELU_PORT || 5096)
const PODELU = `http://127.0.0.1:${PODELU_PORT}`
const PASSWORD = 'demo-password-1'
const only = process.argv.slice(2)
const dir = mkdtempSync(join(tmpdir(), 'puls-shots-'))
const env = {
  ...process.env, SQLALCHEMY_DATABASE_URI: `sqlite:///${join(dir, 'demo.db')}`, FLASK_ENV: 'e2e', RATELIMIT_ENABLED: 'false',
  DEMO_JIRA_URL: JIRA,
}

// Заглушка Jira Data Center (e2e/jiraStub.mjs): проект портала поставщиков MER с релизами, спринтами и связями
// (сегодня по сценарию — 30.10.2026, как в seed_demo.py)
const day = (offset) => new Date(Date.UTC(2026, 9, 30 + offset)).toISOString().slice(0, 10)
const status = (name, key) => ({ name, statusCategory: { key } })
const TYPE = { task: { name: 'Задача', hierarchyLevel: 0 }, story: { name: 'История', hierarchyLevel: 0 }, epic: { name: 'Эпик', hierarchyLevel: 1 } }
const jiraData = {
  projects: [{ id: '10010', key: 'MER', name: 'Портал поставщиков «Меридиан»' }, { id: '10011', key: 'OPS', name: 'Эксплуатация' }],
  versions: { MER: [
    { id: '30001', name: 'Этап 2 — разработка', released: false, releaseDate: day(52), startDate: day(-9) },
    { id: '30002', name: 'Опытная эксплуатация', released: false, releaseDate: day(105) },
    { id: '30000', name: 'Этап 1 — ТЗ', released: true, releaseDate: day(-12) },
  ], OPS: [] },
  boards: [{ id: 1, name: 'Доска MER', type: 'scrum', location: { projectKey: 'MER' } }],
  sprints: { 1: [
    { id: 11, name: 'Спринт 11', state: 'active', startDate: `${day(-10)}T09:00:00.000+03:00`, endDate: `${day(4)}T18:00:00.000+03:00` },
    { id: 12, name: 'Спринт 12', state: 'future', startDate: `${day(4)}T09:00:00.000+03:00`, endDate: `${day(18)}T18:00:00.000+03:00` },
  ] },
  issues: {
    'MER-12': { summary: 'Техническое задание', issuetype: TYPE.task, status: status('Готово', 'done'), assignee: { displayName: 'Ковалёва Мария' }, duedate: day(-25), resolutiondate: `${day(-22)}T17:40:00.000+0300`, fixVersions: ['30000'] },
    'MER-30': { summary: 'Кабинет поставщика', issuetype: TYPE.epic, status: status('В работе', 'indeterminate') },
    'MER-31': { summary: 'Личный кабинет поставщика', issuetype: TYPE.story, status: status('В работе', 'indeterminate'), assignee: { displayName: 'Лебедев Дмитрий' }, duedate: day(30), timespent: 3600 * 132, timeestimate: 3600 * 68, epic: 'MER-30', sprint: 11, fixVersions: ['30001'] },
    'MER-32': { summary: 'Интеграция с ERP заказчика', issuetype: TYPE.task, status: status('Ожидание заказчика', 'indeterminate'), assignee: { displayName: 'Лебедев Дмитрий' }, duedate: day(54), timespent: 3600 * 40, timeestimate: 3600 * 120, priority: { name: 'Высокий' }, fixVersions: ['30001'] },
    'MER-35': { summary: 'Электронная подпись документов (КЭП)', issuetype: TYPE.story, status: status('В работе', 'indeterminate'), assignee: { displayName: 'Лебедев Дмитрий' }, duedate: day(35), timespent: 3600 * 10, timeestimate: 3600 * 70, epic: 'MER-30', sprint: 11 },
    'MER-40': { summary: 'Отчёт о поставках для заказчика', issuetype: TYPE.story, status: status('Открыта', 'new'), duedate: day(48), epic: 'MER-30', sprint: 12, fixVersions: ['30001'], links: { blockedBy: ['MER-31'] } },
    'MER-41': { summary: 'Уведомления поставщиков по почте', issuetype: TYPE.story, status: status('Открыта', 'new'), duedate: day(52), epic: 'MER-30', sprint: 12 },
    'MER-42': { summary: 'Нагрузочное тестирование портала', issuetype: TYPE.task, status: status('Открыта', 'new'), duedate: day(60), fixVersions: ['30002'], links: { blockedBy: ['MER-40'] } },
  },
}
const jira = await startJiraStub({ port: JIRA_PORT, flavor: 'server', token: 'demo-token', data: jiraData })

// Задачи «По Делу», которые ссылаются на портал поставщиков (проект 1, основной план 1)
const PODELU_KEY = 'podelu_' + 'demo'.repeat(11)
const PODELU_TASKS = [
  { id: 'p1', title: 'Согласовать с заказчиком доступ к тестовой ERP', status: 'in_progress', statusLabel: 'В работе', done: false, assignee: { name: 'Морозов Алексей' }, due: day(-1), path: '/my-tasks?task=p1', planId: 1, itemKey: 't22', itemName: 'Интеграция с ERP заказчика' },
  { id: 'p2', title: 'Подготовить сертификаты КЭП для стенда', status: 'open', statusLabel: 'Открыта', done: false, assignee: { name: 'Лебедев Дмитрий' }, due: day(6), path: '/my-tasks?task=p2', planId: 1, itemKey: 't23', itemName: 'Электронная подпись документов (КЭП)' },
  { id: 'p3', title: 'Еженедельный статус для АО «Меридиан»', status: 'open', statusLabel: 'Открыта', done: false, assignee: { name: 'Морозов Алексей' }, due: day(4), path: '/my-tasks?task=p3', planId: null, itemKey: null },
  { id: 'p4', title: 'Протокол встречи по ТЗ', status: 'done', statusLabel: 'Выполнена', done: true, assignee: { name: 'Ковалёва Мария' }, due: day(-20), path: '/my-tasks?task=p4', planId: 1, itemKey: 't13', itemName: 'Техническое задание' },
]
function podeluTasksFor(path) { return path.includes('/projects/1/') ? PODELU_TASKS : [] }
function podeluCounts() { return { 1: { total: PODELU_TASKS.length, open: PODELU_TASKS.filter((t) => !t.done).length } } }

// Заглушка «По Делу»: CSRF-токен, вход сервисной учётной записи и /api/auth/me — для проверки подключения
const podeluAccount = { id: 'svc', name: 'Сервис «Пульса»', login: 'svc-puls', globalRole: 'admin', capabilities: [], isActive: true }
const podelu = createServer((req, res) => {
  req.resume()
  req.on('end', () => {
    const send = (code, data, headers = {}) => { res.writeHead(code, { 'Content-Type': 'application/json', ...headers }); res.end(JSON.stringify(data)) }
    const cookie = { 'Set-Cookie': 'todo_planner_session=demo; HttpOnly; Path=/; SameSite=Lax' }
    const path = req.url.split('?')[0]
    if (path === '/api/auth/csrf-token') return send(200, { csrfToken: 'demo-csrf' }, cookie)
    if (path === '/api/auth/login') return send(200, { user: podeluAccount }, cookie)
    if (path === '/api/auth/logout') return send(200, { message: 'logged_out' })
    if (path === '/api/auth/me') return send(200, podeluAccount)
    // Ответная интеграция (ключ «По Делу» для «Пульса»): проверка связи и задачи, связанные с проектами
    if (req.headers.authorization === `Bearer ${PODELU_KEY}`) {
      if (path === '/api/integrations/puls/ping') return send(200, { ok: true, app: 'podelu', name: 'По Делу', version: '1.5.0', linksEnabled: true })
      if (/^\/api\/integrations\/puls\/projects\/\d+\/tasks$/.test(path)) return send(200, { tasks: podeluTasksFor(path), truncated: false })
      if (path === '/api/integrations/puls/summary') return send(200, { counts: podeluCounts() })
    }
    return send(404, { error: 'not_found' })
  })
})
await new Promise((resolveListen) => podelu.listen(PODELU_PORT, '127.0.0.1', resolveListen))

const seed = spawnSync(python, [join(root, 'tools/demo/seed_demo.py')], { cwd: join(root, 'backend'), env, stdio: 'inherit' })
if (seed.status !== 0) process.exit(seed.status || 1)

const children = [
  spawn(python, ['-m', 'flask', '--app', 'wsgi', 'run', '--host', '127.0.0.1', '--port', API_PORT], { cwd: join(root, 'backend'), env, stdio: 'ignore' }),
  // vite — напрямую, а не через npx: иначе после снимков сервер остаётся работать
  spawn(process.execPath, [join(root, 'node_modules/vite/bin/vite.js'), 'preview', '--host', '127.0.0.1', '--port', WEB_PORT, '--strictPort'], { cwd: root, env: { ...process.env, VITE_BACKEND: `http://127.0.0.1:${API_PORT}` }, stdio: 'ignore' }),
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
  // Обратно к неделям в масштабе 100 % (вид сохраняется у сотрудника)
  await page.click('.segmented button:has-text("Недели")')
  await page.fill('.zoom-input', '100%')
  await page.press('.zoom-input', 'Enter')

  // Совместная работа: коллега открыл тот же план — появляется в «Сейчас на плане» после опроса
  const colleague = await session('lebedev')
  await openProject(colleague, PORTAL)
  await page.waitForSelector('.presence-avatar', { timeout: 15000 })
  await shot(page, '21-presence', { clip: { x: 0, y: 0, width: 1440, height: 160 } })
  await colleague.close()

  const viewer = await session('volkov')
  await openProject(viewer, PORTAL)
  await shot(viewer, '22-viewer')
  await viewer.click('.user-card')
  await viewer.waitForSelector('.modal')
  await shot(viewer, '23-profile')

  // Jira: задача в карточке строки, сверка, задачи из Jira
  await openProject(page, PORTAL)
  await page.locator('.grid-row', { hasText: 'Интеграция с ERP заказчика' }).locator('.open-btn').click()
  await page.waitForSelector('[data-testid=jira-card] .summary')
  await shot(page, '24-jira-card')
  await closeOverlays(page)
  await page.click('[data-testid=jira-menu]')
  await page.click('[data-testid=jira-sync-open]')
  await page.waitForSelector('[data-testid=jira-sync-table]')
  await shot(page, '25-jira-sync')
  await closeOverlays(page)
  await page.click('[data-testid=jira-menu]')
  await page.click('[data-testid=jira-import-open]')
  await page.selectOption('[data-testid=jira-import-project]', 'MER')
  await page.waitForSelector('[data-testid=jira-import-list] .issue')
  await page.click('[data-testid=jira-import-grouping] button:has-text("По спринтам")')
  await page.waitForTimeout(800)
  await shot(page, '26-jira-import')
  await closeOverlays(page)

  // Структура компании
  await page.goto(BASE + '/org?view=tree')
  await page.locator('.node-name', { hasText: 'Разработка' }).click()
  await page.locator('[data-testid=org-unit] .person-row', { hasText: 'Лебедев' }).first().click()
  await page.waitForSelector('[data-testid=org-person]')
  await shot(page, '27-org')

  // Оргсхема — вид по умолчанию: подразделения карточками с линиями подчинения
  await page.goto(BASE + '/org?view=chart')
  await page.waitForSelector('[data-testid=org-chart]')
  await page.waitForTimeout(600)
  await shot(page, '28-org-chart')
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

  await page.goto(BASE + '/admin/integrations/ldap')
  await page.waitForSelector('[data-testid=ldap-settings]')
  await page.waitForTimeout(500)
  await shot(page, '07-settings-ldap')
  await page.goto(BASE + '/admin/settings')
  await page.waitForSelector('[data-testid=calendar-settings]')
  await page.waitForTimeout(500)
  await shot(page, '08-calendar')

  await page.goto(BASE + '/admin/trash')
  await page.waitForTimeout(600)
  await shot(page, '09-trash')

  await page.goto(BASE + '/about')
  await page.waitForTimeout(600)
  await shot(page, '10-about')

  await page.goto(BASE + '/admin/structure')
  await page.waitForSelector('[data-testid=dep-row]')
  await shot(page, '11-structure')

  await page.goto(BASE + '/admin/org-transfer')
  await page.waitForTimeout(800)
  await shot(page, '12-org-transfer')

  // Интеграции: обзор, Jira (проверка и диагностика), «По Делу» (обмен ключами и структура)
  await page.goto(BASE + '/admin/integrations/podelu')
  await page.click('[data-testid=podelu-links-issue]')
  await page.fill('[data-testid=podelu-links-url]', PODELU)
  await page.fill('[data-testid=podelu-links-key]', PODELU_KEY)
  await page.click('[data-testid=podelu-links-check]')
  await page.locator('[data-testid=podelu-links-status]', { hasText: 'Связь установлена' }).waitFor()
  await page.locator('[data-testid=podelu-links-enabled]').click()
  await page.waitForTimeout(500)
  await page.locator('[data-testid=podelu-settings]').scrollIntoViewIfNeeded()
  await page.fill('[data-testid=podelu-url]', PODELU)
  await page.fill('[data-testid=podelu-login]', 'svc-puls')
  await page.fill('[data-testid=podelu-password]', 'пароль-сервиса')
  await page.click('[data-testid=podelu-test]')
  await page.locator('[data-testid=podelu-status]', { hasText: 'Соединение установлено' }).waitFor()
  await page.click('[data-testid=podelu-save]')
  await page.locator('[data-testid=podelu-status]', { hasText: 'Подключение сохранено' }).waitFor()
  await page.locator('[data-testid=podelu-links]').scrollIntoViewIfNeeded()
  await page.locator('[data-testid=podelu-links]').screenshot({ path: join(root, 'docs/manuals/images/admin/15-podelu.png') })
  // Для руководства пользователя: задачи «По Делу» на проекте
  await openProject(page, PORTAL)
  await page.click('[data-testid=podelu-tasks-open]')
  await page.waitForSelector('[data-testid=podelu-tasks-list]')
  await page.waitForTimeout(400)
  await page.screenshot({ path: join(root, 'docs/manuals/images/user/29-podelu-tasks.png') })
  await closeOverlays(page)

  await page.goto(BASE + '/admin/integrations')
  await page.waitForSelector('[data-testid=integration-podelu] .state-ok')
  await shot(page, '13-integrations')

  await page.goto(BASE + '/admin/integrations/jira')
  await page.waitForSelector('[data-testid=jira-settings]')
  await page.fill('[data-testid=jira-diag-key]', 'MER-31')
  await page.click('[data-testid=jira-diagnose]')
  await page.waitForSelector('[data-testid=jira-diag-steps]')
  await page.click('[data-testid=jira-test]')
  await page.locator('[data-testid=jira-status]', { hasText: 'Соединение установлено' }).waitFor()
  await page.locator('[data-testid=jira-settings]').screenshot({ path: join(root, 'docs/manuals/images/admin/16-jira.png') })
  await page.locator('[data-testid=jira-diagnostics]').screenshot({ path: join(root, 'docs/manuals/images/admin/17-jira-diagnostics.png') })

  await page.goto(BASE + '/admin/access')
  await page.click('.segmented button:has-text("Правила")')
  await page.waitForSelector('[data-testid=dep-rule]')
  await shot(page, '14-access-departments')
}

let code = 0
try {
  await waitFor(`http://127.0.0.1:${API_PORT}/api/health/ready`)
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
  jira.close()
  podelu.close()
}
process.exit(code)
