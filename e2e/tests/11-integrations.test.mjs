import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { api, BASE, closeBrowser, login, newPage, PASSWORD, shot, waitSaved } from '../helpers.mjs'
import { startJiraStub } from '../jiraStub.mjs'

/**
 * 0.5.0: блок «Интеграции»; Jira — диагностика, другие адреса, поиск в проекте без JQL, группы по спринтам, связи
 * «блокирует», релизы → вехи, источник задач плана, подсказки, Jira Cloud; «По Делу» — обмен ключами, API «Пульса»
 * для «По Делу» с правами сотрудника, задачи «По Делу» на проекте и в карточке строки; читаемость подписей.
 */
const PODELU_PORT = 5092
const PODELU = `http://127.0.0.1:${PODELU_PORT}`
const PODELU_KEY = 'podelu_' + 'e2e'.repeat(15)
let jira
let cloud
let podelu
let page
let projectId
let planId
const podeluTasks = []
const podeluRequests = []

function startPodelu() {
  podelu = createServer((req, res) => {
    const url = new URL(req.url, PODELU)
    podeluRequests.push({ path: url.pathname, query: Object.fromEntries(url.searchParams), auth: req.headers.authorization })
    const send = (code, body) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)) }
    if (req.headers.authorization !== `Bearer ${PODELU_KEY}`) return send(401, { error: 'unauthorized', message: 'Неверный ключ' })
    if (url.pathname === '/api/integrations/puls/ping') return send(200, { ok: true, app: 'podelu', name: 'По Делу', version: '1.5.0', linksEnabled: true })
    const m = /^\/api\/integrations\/puls\/projects\/(\d+)\/tasks$/.exec(url.pathname)
    if (m) {
      const item = url.searchParams.get('item')
      const tasks = podeluTasks.filter((t) => t.projectId === Number(m[1]) && (!item || t.itemKey === item))
      return send(200, { tasks, total: tasks.length, truncated: false })
    }
    if (url.pathname === '/api/integrations/puls/summary') {
      const counts = {}
      for (const id of (url.searchParams.get('projects') || '').split(',').map(Number)) {
        const tasks = podeluTasks.filter((t) => t.projectId === id)
        if (tasks.length) counts[id] = { total: tasks.length, open: tasks.filter((t) => !t.done).length }
      }
      return send(200, { counts })
    }
    return send(404, { error: 'not_found' })
  })
  return new Promise((resolve) => podelu.listen(PODELU_PORT, '127.0.0.1', resolve))
}

before(async () => {
  jira = await startJiraStub({ port: 5094, flavor: 'server' })
  cloud = await startJiraStub({ port: 5093, flavor: 'cloud', login: 'svc@company.ru', password: 'cloud-token' })
  await startPodelu()
  page = await newPage({ width: 1500, height: 950 })
  await login(page)
})
after(async () => {
  await api(page, 'DELETE', '/settings/jira')
  await api(page, 'DELETE', '/settings/podelu/links')
  await closeBrowser()
  await jira.close()
  await cloud.close()
  podelu.close()
})

async function planItems() {
  return (await api(page, 'GET', `/plans/${planId}`)).body.items
}

test('блок «Интеграции»: обзор и отдельный раздел меню только у администратора', async () => {
  await page.goto(`${BASE}/admin/integrations`)
  for (const id of ['jira', 'podelu', 'ldap']) await page.locator(`[data-testid=integration-${id}]`).waitFor()
  await page.locator('[data-testid=integration-jira] .state', { hasText: 'Не подключена' }).waitFor()
  await page.locator('[data-testid=sidebar-integrations] [data-testid=nav-integration-jira]').waitFor()
  await shot(page, '11-integrations-hub')
  const editor = await newPage()
  await login(editor, 'e2e.editor', PASSWORD)
  assert.equal(await editor.locator('[data-testid=sidebar-integrations]').count(), 0)
  await editor.context().close()
})

test('Jira: проверка предупреждает о другом адресе, диагностика объясняет, другие адреса распознают ссылки', async () => {
  // Jira называет свой адрес иначе (как за обратным прокси): ссылки, скопированные из Jira, ведут туда
  await jira.close()
  jira = await startJiraStub({ port: 5094, flavor: 'server', reportedBaseUrl: 'https://jira.company.ru' })
  await page.click('[data-testid=nav-integration-jira]')
  await page.fill('[data-testid=jira-url]', jira.url)
  await page.selectOption('[data-testid=jira-type]', 'server')
  await page.fill('[data-testid=jira-token]', 'pat-token')
  await page.click('[data-testid=jira-test]')
  const status = page.locator('[data-testid=jira-status]')
  await status.locator('text=проектов видно: 2').waitFor()
  await status.locator('text=Подключение ещё не сохранено').waitFor()
  await status.locator('text=https://jira.company.ru').waitFor()
  await page.click('[data-testid=jira-add-link]')
  assert.equal(await page.inputValue('[data-testid=jira-link-urls]'), 'https://jira.company.ru')
  await page.click('[data-testid=jira-save]')
  await status.locator('text=Подключение сохранено').waitFor()
  const saved = (await api(page, 'GET', '/settings/jira')).body
  assert.deepEqual(saved.linkUrls, ['https://jira.company.ru'])

  // Строка ссылается на задачу через адрес из serverInfo — распознаётся; диагностика — по шагам
  const created = await api(page, 'POST', '/projects', { title: 'E2E Интеграции' })
  projectId = created.body.id
  planId = created.body.plans[0].id
  await api(page, 'PATCH', `/plans/${planId}`, { settings: { range: { start: '2026-05-01', end: '2026-12-31' } } })
  await api(page, 'POST', `/plans/${planId}/ops`, { baseRevision: null, ops: [
    { op: 'item.create', item: { key: 'r1', type: 'task', name: 'Согласование', start: '2026-06-01', end: '2026-06-12', jiraUrl: 'https://jira.company.ru/browse/PULS-1' } },
    { op: 'item.create', item: { key: 'r2', type: 'task', name: 'Чужая Jira', start: '2026-06-01', end: '2026-06-05', jiraUrl: 'https://jira.other.ru/browse/OLD-7' } },
  ] })
  await page.fill('[data-testid=jira-diag-key]', 'PULS-2')
  await page.click('[data-testid=jira-diagnose]')
  const steps = page.locator('[data-testid=jira-diag-steps]')
  await steps.locator('text=Задача PULS-2').waitFor()
  await steps.locator('text=«Прототип интерфейса»').waitFor()
  await steps.locator('text=Data Center 9.12.2').waitFor()
  await steps.locator('text=jira.other.ru (1)').waitFor()
  assert.equal(await steps.locator('.is-error').count(), 0)
  await shot(page, '11-jira-diagnostics')
})

test('Jira в плане: ссылка через другой адрес работает, ссылка на чужую Jira — с подсказкой', async () => {
  await page.goto(`${BASE}/projects/${projectId}`)
  await page.locator('.grid-row', { hasText: 'Согласование' }).locator('text=PULS-1: В работе').waitFor()
  await page.click('[data-testid=jira-menu]')
  await page.locator('[data-testid=jira-foreign-note]', { hasText: 'jira.other.ru' }).waitFor()
  await page.keyboard.press('Escape')
  await page.locator('.grid-row', { hasText: 'Чужая Jira' }).locator('.open-btn').click()
  await page.locator('[data-testid=item-jira-foreign]', { hasText: 'jira.other.ru' }).waitFor()
  await page.keyboard.press('Escape')
})

test('«Задачи из Jira»: проект, фильтры, группы по спринтам и связи «блокирует» — одной правкой', async () => {
  await page.click('[data-testid=jira-menu]')
  await page.click('[data-testid=jira-import-open]')
  await page.selectOption('[data-testid=jira-import-project]', 'PULS')
  const list = page.locator('[data-testid=jira-import-list]')
  await list.locator('text=Вход по СМС').waitFor()
  // PULS-1 уже в плане (по другому адресу этой Jira), выполненная PULS-3 не показана (статусы по умолчанию)
  await list.locator('.issue', { hasText: 'PULS-1' }).locator('.badge', { hasText: 'уже в плане' }).waitFor()
  assert.equal(await list.locator('.issue', { hasText: 'PULS-3' }).count(), 0)
  // Только истории
  await page.locator('.modal .chip', { hasText: 'История' }).click()
  await list.locator('.issue', { hasText: 'PULS-13' }).waitFor({ state: 'detached' })
  assert.deepEqual((await list.locator('.key').allTextContents()).sort(), ['PULS-11', 'PULS-12', 'PULS-2'])
  await page.click('[data-testid=jira-import-grouping] button:has-text("По спринтам")')
  await list.locator('.issue', { hasText: 'PULS-2' }).waitFor()
  await shot(page, '11-jira-import-project')
  await page.click('[data-testid=jira-import-add]')
  await page.locator('.grid-row .name', { hasText: 'Спринт 7' }).waitFor()
  await waitSaved(page)
  const items = await planItems()
  const byName = Object.fromEntries(items.map((i) => [i.name, i]))
  assert.equal(byName['Спринт 6'].type, 'group')
  assert.deepEqual(items.filter((i) => i.parentKey === byName['Спринт 6'].key).map((i) => i.name).sort(), ['Вход по СМС', 'Прототип интерфейса'])
  assert.equal(byName['Профиль поставщика'].parentKey, byName['Спринт 7'].key)
  // «блокирует»: PULS-2 ← PULS-1 (строка плана), PULS-12 ← PULS-11 (добавленная строка); автосдвиг
  assert.equal(byName['Прототип интерфейса'].dependencyKey, 'r1')
  assert.equal(byName['Профиль поставщика'].dependencyKey, byName['Вход по СМС'].key)
  assert.ok(byName['Профиль поставщика'].start > byName['Вход по СМС'].end)
  // Проект стал источником задач плана
  const plan = (await api(page, 'GET', `/plans/${planId}`)).body.plan
  assert.deepEqual(plan.settings.jiraSource, { project: 'PULS', jql: '' })
  assert.deepEqual(page.errors, [])
})

test('источник задач плана: новые задачи в меню J, релизы → вехи', async () => {
  // Новые незавершённые задачи источника, которых нет в плане, — PULS-13 (ошибка); эпик PULS-10 не считается
  await page.reload()
  await page.waitForSelector('.gantt .grid-row')
  await page.locator('[data-testid=jira-menu] .new-dot').waitFor()
  await page.click('[data-testid=jira-menu]')
  await page.locator('[data-testid=jira-new-open]', { hasText: '1' }).waitFor()
  await page.click('[data-testid=jira-versions-open]')
  const versions = page.locator('[data-testid=jira-import-versions]')
  await versions.locator('text=Релиз 2.0').waitFor()
  assert.equal(await versions.locator('label', { hasText: 'Релиз 3.0' }).locator('input').isDisabled(), true)
  await versions.locator('label', { hasText: 'Релиз 2.0' }).locator('input').check()
  await page.click('[data-testid=jira-import-add]')
  await page.locator('.grid-row .name', { hasText: 'Релиз 2.0' }).waitFor()
  await waitSaved(page)
  const milestone = (await planItems()).find((i) => i.name === 'Релиз 2.0')
  assert.equal(milestone.type, 'milestone')
  assert.equal(milestone.date, '2026-12-15')
  assert.equal(milestone.parentKey, null)
  assert.equal(milestone.jiraUrl, `${jira.url}/projects/PULS/versions/20002`)
})

test('подсказки в карточке строки: задача по словам из названия', async () => {
  await page.fill('[data-testid=quick-add]', 'Безопасность входа')
  await page.press('[data-testid=quick-add]', 'Enter')
  await waitSaved(page)
  await page.locator('.grid-row', { hasText: 'Безопасность входа' }).locator('.open-btn').click()
  await page.fill('[data-testid=item-jira]', 'safari')
  const suggest = page.locator('[data-testid=item-jira-suggest]')
  await suggest.locator('text=Ошибка входа через Safari').waitFor()
  await suggest.locator('li', { hasText: 'PULS-13' }).dispatchEvent('mousedown')
  await page.locator('[data-testid=jira-card] .summary', { hasText: 'Ошибка входа через Safari' }).waitFor()
  await waitSaved(page)
  const row = (await planItems()).find((i) => i.name === 'Безопасность входа')
  assert.equal(row.jiraUrl, `${jira.url}/browse/PULS-13`)
  await page.keyboard.press('Escape')
  assert.deepEqual(page.errors, [])
})

test('подписи на полосах — крупные, белые на насыщенных цветах, с обводкой', async () => {
  const label = page.locator('.task .bar-text.on-dark').first()
  await label.waitFor()
  const style = await label.evaluate((el) => ({ size: parseFloat(getComputedStyle(el).fontSize), fill: el.getAttribute('fill'), stroke: getComputedStyle(el).stroke }))
  assert.ok(style.size >= 11.5, `размер ${style.size}`)
  assert.equal(style.fill, '#ffffff')
  assert.notEqual(style.stroke, 'none')
})

test('Jira Cloud: поиск через /search/jql, старый /search не используется', async () => {
  await api(page, 'PUT', '/settings/jira', { baseUrl: cloud.url, instanceType: 'cloud', login: 'svc@company.ru', token: 'cloud-token' })
  const test1 = await api(page, 'POST', '/settings/jira/test', {})
  assert.equal(test1.status, 200, JSON.stringify(test1.body))
  const found = await api(page, 'POST', '/jira/search', { planId, filter: { project: 'PULS', epic: 'PULS-10' }, extended: true })
  assert.equal(found.status, 200, JSON.stringify(found.body))
  assert.deepEqual(found.body.issues.map((i) => i.key).sort(), ['PULS-11', 'PULS-12', 'PULS-13'])
  assert.ok(found.body.issues.every((i) => i.epic === 'PULS-10'))
  assert.ok(cloud.requests.some((r) => r.path === '/rest/api/3/search/jql'))
  assert.ok(!cloud.requests.some((r) => r.path === '/rest/api/3/search'))
  await api(page, 'PUT', '/settings/jira', { baseUrl: jira.url, instanceType: 'server', token: 'pat-token' })
})

test('«По Делу»: обмен ключами, проверка связи и включение показа задач', async () => {
  await page.goto(`${BASE}/admin/integrations/podelu`)
  await page.click('[data-testid=podelu-links-issue]')
  const issued = page.locator('[data-testid=podelu-links-issued] code')
  await issued.waitFor()
  const key = (await issued.textContent()).trim()
  assert.match(key, /^puls_[\w-]{40,}$/)
  // Ключ «Пульса» открывает API для «По Делу» — с правами сотрудника
  const anon = await fetch(`${BASE}/api/integration/v1/projects?login=e2e.viewer`, { headers: { Authorization: `Bearer ${key}` } })
  const scoped = await anon.json()
  assert.equal(scoped.userFound, true)
  assert.deepEqual(scoped.projects.map((p) => p.title), ['ИСУП МО - реализация договоров'])
  const items = await (await fetch(`${BASE}/api/integration/v1/projects/${projectId}/items?q=${encodeURIComponent('вход')}`, { headers: { Authorization: `Bearer ${key}` } })).json()
  assert.ok(items.items.some((i) => i.name === 'Вход по СМС' && i.path.startsWith(`/projects/${projectId}/${planId}?item=`)))
  assert.equal((await fetch(`${BASE}/api/integration/v1/ping`)).status, 401)

  // Шаг 2: адрес и ключ «По Делу», проверка, затем включение
  assert.equal(await page.locator('[data-testid=podelu-links-enabled] input').isDisabled(), true)
  await page.fill('[data-testid=podelu-links-url]', PODELU)
  await page.fill('[data-testid=podelu-links-key]', PODELU_KEY)
  await page.click('[data-testid=podelu-links-check]')
  await page.locator('[data-testid=podelu-links-status]', { hasText: 'Связь установлена: По Делу 1.5.0' }).waitFor()
  assert.equal(podeluRequests.at(-1).auth, `Bearer ${PODELU_KEY}`)
  await page.locator('[data-testid=podelu-links-enabled]').click()
  await page.locator('.toast', { hasText: 'Задачи «По Делу» показываются в проектах' }).waitFor()
  await shot(page, '11-podelu-links')
  const settings = (await api(page, 'GET', '/settings/podelu/links')).body
  assert.equal(settings.enabled, true)
  assert.ok(!JSON.stringify(settings).includes(PODELU_KEY))
})

test('задачи «По Делу» на проекте и в карточке строки', async () => {
  const row = (await planItems()).find((i) => i.name === 'Вход по СМС')
  podeluTasks.push(
    { projectId, id: 't1', title: 'Подготовить смету', status: 'in_progress', statusLabel: 'В работе', done: false, assignee: { name: 'Иванов И. И.' }, due: '2026-01-10', path: '/my-tasks?task=t1', planId: null, itemKey: null },
    { projectId, id: 't2', title: 'Тексты СМС', status: 'open', statusLabel: 'Открыта', done: false, due: '2026-12-01', path: '/my-tasks?task=t2', planId, itemKey: row.key, itemName: 'Вход по СМС' },
    { projectId, id: 't3', title: 'Старое согласование', status: 'done', statusLabel: 'Выполнена', done: true, path: '/my-tasks?task=t3', planId, itemKey: row.key },
  )
  await page.goto(`${BASE}/projects/${projectId}`)
  const button = page.locator('[data-testid=podelu-tasks-open]')
  await button.locator('.podelu-count', { hasText: '2/3' }).waitFor()
  await button.click()
  const listEl = page.locator('[data-testid=podelu-tasks-list]')
  await listEl.locator('text=Подготовить смету').waitFor()
  assert.equal(await listEl.locator('text=Старое согласование').count(), 0)
  await listEl.locator('.due.overdue').first().waitFor()
  assert.equal(await listEl.locator('a.title', { hasText: 'Тексты СМС' }).getAttribute('href'), `${PODELU}/my-tasks?task=t2`)
  await page.locator('.modal .seg-option', { hasText: 'Все' }).click()
  await listEl.locator('text=Старое согласование').waitFor()
  await shot(page, '11-podelu-tasks')
  // Переход к строке плана — открывается её карточка с задачами «По Делу» этой строки
  await listEl.locator('.row-link', { hasText: 'Вход по СМС' }).click()
  const section = page.locator('[data-testid=item-podelu]')
  await section.locator('text=Тексты СМС').waitFor()
  await section.locator('text=Старое согласование').waitFor()
  assert.equal(await section.locator('text=Подготовить смету').count(), 0)
  // На главной — число открытых задач «По Делу» на карточке проекта
  await page.goto(`${BASE}/`)
  await page.locator('article', { hasText: 'E2E Интеграции' }).locator('[data-testid=card-podelu]', { hasText: '2' }).waitFor()
  assert.deepEqual(page.errors, [])
})
