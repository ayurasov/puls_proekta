import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { api, BASE, closeBrowser, login, newPage, shot, waitSaved } from '../helpers.mjs'

// Jira Server на этом же компьютере: /myself, /serverInfo, /search (key in …), /issue/KEY, /field
const JIRA_PORT = 5099
const JIRA = `http://127.0.0.1:${JIRA_PORT}`
const issues = {
  'PULS-1': { summary: 'Согласование ТЗ', status: { name: 'В работе', statusCategory: { key: 'indeterminate' } }, assignee: { displayName: 'Иванов И.' }, duedate: '2026-06-12', timespent: 7200, timeestimate: 7200 },
  'PULS-2': { summary: 'Прототип интерфейса', status: { name: 'Открыта', statusCategory: { key: 'new' } }, duedate: '2026-06-20' },
  'PULS-3': { summary: 'Приёмка заказчиком', status: { name: 'Готово', statusCategory: { key: 'done' } }, duedate: '2026-06-05', resolutiondate: '2026-06-04T11:00:00.000+0300' },
}
let jiraServer
function startJira() {
  jiraServer = createServer((req, res) => {
    let body = ''
    req.on('data', (chunk) => { body += chunk })
    req.on('end', () => {
      const send = (status, data) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(data)) }
      const path = req.url.split('?')[0].replace('/rest/api/2/', '')
      if (path === 'myself') return send(200, { displayName: 'Сервисная учётная запись' })
      if (path === 'field') return send(200, [{ id: 'duedate', name: 'Срок', schema: { type: 'date' } }])
      if (path === 'serverInfo') return send(200, { version: '8.20.10', deploymentType: 'Server' })
      if (path === 'search') {
        const { jql, validateQuery } = JSON.parse(body || '{}')
        // Как Jira Server: validateQuery — только true или false
        if (validateQuery !== undefined && typeof validateQuery !== 'boolean') {
          return send(400, { errorMessages: [`Can not construct instance of java.lang.Boolean from String value '${validateQuery}'`] })
        }
        const keys = jql.startsWith('key in') ? jql.slice(jql.indexOf('(') + 1, jql.lastIndexOf(')')).split(',').map((k) => k.trim()) : Object.keys(issues)
        const found = keys.filter((k) => issues[k]).map((key) => ({ key, fields: issues[key] }))
        return send(200, { issues: found, total: found.length })
      }
      const key = path.startsWith('issue/') ? path.split('/')[1] : null
      if (key && issues[key]) return send(200, { key, fields: issues[key] })
      return send(404, { errorMessages: ['Not found'] })
    })
  })
  return new Promise((resolve) => jiraServer.listen(JIRA_PORT, '127.0.0.1', resolve))
}

let page
let planId

before(async () => {
  await startJira()
  page = await newPage({ width: 1600, height: 950 })
  await login(page)
})
after(async () => {
  await api(page, 'DELETE', '/settings/jira')
  await closeBrowser()
  jiraServer.close()
})

async function planItems() {
  return (await api(page, 'GET', `/plans/${planId}`)).body.items
}

test('администратор подключает Jira в «Интеграциях»', async () => {
  await page.goto(`${BASE}/admin/integrations`)
  await page.fill('[data-testid=jira-url]', JIRA)
  await page.fill('[data-testid=jira-login]', 'svc-puls')
  await page.fill('[data-testid=jira-token]', 'пароль-сервиса')
  await page.click('[data-testid=jira-test]')
  await page.locator('[data-testid=jira-status]', { hasText: 'Соединение установлено: Сервисная учётная запись · Jira Server 8.20.10 · поиск задач работает' }).waitFor()
  await page.click('[data-testid=jira-save]')
  await page.locator('[data-testid=jira-status]', { hasText: 'Подключение сохранено' }).waitFor()
  await shot(page, '06-jira-settings')
  const saved = await api(page, 'GET', '/settings/jira')
  assert.equal(saved.body.baseUrl, JIRA)
  assert.equal(saved.body.hasToken, true)
  assert.ok(!JSON.stringify(saved.body).includes('пароль-сервиса'))
})

test('ключ задачи в карточке строки: данные из Jira и перенос в план', async () => {
  const created = await api(page, 'POST', '/projects', { title: 'E2E Jira' })
  planId = created.body.plans[0].id
  await api(page, 'PATCH', `/plans/${planId}`, { settings: { range: { start: '2026-05-01', end: '2026-08-31' } } })
  await page.goto(`${BASE}/projects/${created.body.id}`)
  await page.fill('[data-testid=quick-add]', 'ТЗ')
  await page.press('[data-testid=quick-add]', 'Enter')
  await waitSaved(page)
  await page.locator('.grid-row', { hasText: 'ТЗ' }).locator('.open-btn').click()
  await page.fill('[data-testid=item-jira]', 'puls-1')
  await page.press('[data-testid=item-jira]', 'Enter')
  const card = page.locator('[data-testid=jira-card]')
  await card.locator('.summary', { hasText: 'Согласование ТЗ' }).waitFor()
  await card.locator('text=Иванов И.').waitFor()
  await shot(page, '06-jira-card')
  await card.locator('[data-testid=jira-apply-all]').click()
  await waitSaved(page)
  const item = (await planItems())[0]
  assert.equal(item.jiraUrl, `${JIRA}/browse/PULS-1`)
  assert.equal(item.name, 'Согласование ТЗ')
  assert.equal(item.status, 'inwork')
  assert.equal(item.end, '2026-06-12')
  assert.equal(item.progress, 50)
  await card.locator('text=Совпадает со строкой').waitFor()
})

test('«Задачи из Jira» добавляет строки одной правкой', async () => {
  await page.click('[data-testid=jira-menu]')
  await page.click('[data-testid=jira-import-open]')
  await page.fill('[data-testid=jira-import-keys]', 'PULS-1, PULS-2 PULS-3 PULS-404')
  await page.click('[data-testid=jira-import-search]')
  const list = page.locator('[data-testid=jira-import-list]')
  await list.locator('text=Приёмка заказчиком').waitFor()
  assert.equal(await list.locator('.badge', { hasText: 'уже в плане' }).count(), 1)
  await page.locator('text=Не найдены или недоступны: PULS-404').waitFor()
  await page.click('[data-testid=jira-import-add]')
  await page.locator('.grid-row .name', { hasText: 'Приёмка заказчиком' }).waitFor()
  await waitSaved(page)
  const items = await planItems()
  assert.deepEqual(items.map((i) => i.name), ['Согласование ТЗ', 'Прототип интерфейса', 'Приёмка заказчиком'])
  const done = items.find((i) => i.name === 'Приёмка заказчиком')
  assert.equal(done.status, 'done')
  assert.equal(done.completedDate, '2026-06-04')
  // одна правка: «Отменить» убирает обе строки
  await page.click('[data-testid=undo]')
  await waitSaved(page)
  assert.equal((await planItems()).length, 1)
  await page.keyboard.press('Control+Shift+Z')
  await waitSaved(page)
  assert.equal((await planItems()).length, 3)
})

test('сверка с Jira после изменений в Jira', async () => {
  issues['PULS-2'].status = { name: 'В работе', statusCategory: { key: 'indeterminate' } }
  issues['PULS-2'].duedate = '2026-06-26'
  await page.click('[data-testid=jira-menu]')
  await page.click('[data-testid=jira-sync-open]')
  await page.click('button:has-text("Обновить из Jira")')
  const table = page.locator('[data-testid=jira-sync-table]')
  await table.locator('text=Прототип интерфейса').waitFor()
  await shot(page, '06-jira-sync')
  await page.click('[data-testid=jira-sync-apply]')
  await waitSaved(page)
  const item = (await planItems()).find((i) => i.name === 'Прототип интерфейса')
  assert.equal(item.status, 'inwork')
  assert.equal(item.end, '2026-06-26')
  assert.deepEqual(page.errors, [])
})

test('фильтры Jira: без задачи, расходятся, статус в Jira', async () => {
  if (await page.locator('.modal').count()) await page.keyboard.press('Escape')
  await page.fill('[data-testid=quick-add]', 'Без Jira')
  await page.press('[data-testid=quick-add]', 'Enter')
  await waitSaved(page)
  issues['PULS-1'].duedate = '2026-06-30'
  await page.click('[data-testid=jira-menu]')
  await page.locator('.menu-item', { hasText: 'Обновить из Jira' }).click()
  await page.locator('.toast', { hasText: 'Данные Jira обновлены' }).waitFor()
  const rows = () => page.locator('.grid-row .name').allTextContents()
  await page.locator('.toolbar button', { hasText: 'Фильтры' }).click()
  await page.click('[data-testid=filter-group-jira]')
  assert.match(await page.textContent('[data-testid=filter-jira-linked]'), /Связаны с Jira\s*3/)
  await page.click('[data-testid=filter-jira-unlinked]')
  assert.deepEqual(await rows(), ['Без Jira'])
  await page.click('[data-testid=filter-jira-unlinked]')
  await page.click('[data-testid=filter-jira-differs]')
  assert.deepEqual(await rows(), ['Согласование ТЗ'])
  await page.click('[data-testid=filter-jira-differs]')
  await page.click('[data-testid="filter-jira-category:done"]')
  assert.deepEqual(await rows(), ['Приёмка заказчиком'])
  assert.deepEqual((await page.locator('.filter-bar .chip').allTextContents()).map((t) => t.trim()), ['Jira: Готово'])
  await shot(page, '06-jira-filters')
  await page.keyboard.press('Escape')
  await page.locator('.filter-bar button', { hasText: 'Сбросить' }).click()
  assert.deepEqual(page.errors, [])
})
