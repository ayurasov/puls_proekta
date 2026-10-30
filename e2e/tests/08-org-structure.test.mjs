import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { api, BASE, closeBrowser, login, newPage, shot } from '../helpers.mjs'

let page

before(async () => {
  page = await newPage({ width: 1440, height: 900 })
  await login(page)
})
after(closeBrowser)

async function addUnit(name, parentLabel, kindLabel) {
  await page.fill('[data-testid=dep-name]', name)
  if (parentLabel) await page.selectOption('.create-row select[aria-label="Входит в"]', { label: parentLabel })
  if (kindLabel) await page.selectOption('.create-row select[aria-label="Тип"]', { label: kindLabel })
  await page.click('[data-testid=dep-add]')
  await page.locator('[data-testid=dep-row]', { hasText: name }).waitFor()
}

test('администратор строит структуру и назначает руководителей', async () => {
  // Набор не зависит от того, что в структуре уже есть (набор 07 загружает её из файла)
  await page.goto(`${BASE}/admin/structure`)
  await page.waitForSelector('[data-testid=dep-name]')
  await addUnit('ООО «Пульс»', 'Верхний уровень', 'Компания')
  await addUnit('Проектный офис', 'ООО «Пульс»', 'Отдел')

  const users = (await api(page, 'GET', '/users')).body
  const admin = users.find((u) => u.login === 'admin')
  const editor = users.find((u) => u.login === 'e2e.editor')
  const deps = (await api(page, 'GET', '/org/tree')).body.departments
  const company = deps.find((d) => d.name === 'ООО «Пульс»' && !d.parentId)
  const office = deps.find((d) => d.name === 'Проектный офис' && d.parentId === company.id)

  await page.locator('[data-testid=dep-row]', { hasText: 'Проектный офис' }).locator('[data-testid=dep-heads]').click()
  await page.fill('.modal input[type=search]', admin.name.split(' ')[0])
  await page.locator('.modal .candidate', { hasText: admin.name }).locator('input').check()
  await page.click('[data-testid=heads-save]')
  await page.locator('[data-testid=dep-row]', { hasText: 'Проектный офис' }).locator('.head', { hasText: admin.name }).waitFor()
  await shot(page, '08-structure-admin')

  // Сотрудник в подразделении: руководитель по структуре — руководитель подразделения
  await api(page, 'PATCH', `/users/${editor.id}`, { departmentId: office.id })
  const card = (await api(page, 'GET', `/org/users/${editor.id}`)).body
  assert.equal(card.directManagerId, admin.id)
  assert.equal(card.managerSource, 'structure')
  assert.deepEqual(card.departmentPath, ['ООО «Пульс»', 'Проектный офис'])
})

test('структура компании видна сотрудникам', async () => {
  const viewer = await newPage({ width: 1366, height: 800 })
  await login(viewer, 'e2e.viewer')
  await viewer.click('[data-testid=org-link]')
  await viewer.locator('[data-testid=org-unit]').waitFor()
  // По умолчанию открывается оргсхема; дерево с поиском — вид «Дерево»
  await viewer.click('[data-testid=org-view-tree]')
  await viewer.fill('[data-testid=org-search]', 'Проектный офис')
  await viewer.locator('.node-name', { hasText: 'Проектный офис' }).first().click()
  await viewer.fill('[data-testid=org-search]', '')
  const unit = viewer.locator('[data-testid=org-unit]')
  await unit.locator('h2', { hasText: 'Проектный офис' }).waitFor()
  await unit.locator('.person-row', { hasText: 'E2E editor' }).click()
  const person = viewer.locator('[data-testid=org-person]')
  await person.locator('text=по структуре').waitFor()
  await viewer.fill('[data-testid=org-search]', 'E2E')
  await viewer.locator('.found-person', { hasText: 'E2E viewer' }).waitFor()
  await shot(viewer, '08-structure-org')
  assert.equal(await viewer.locator('a:has-text("Изменить структуру")').count(), 0)
  assert.deepEqual(viewer.errors, [])
  assert.equal((await api(viewer, 'POST', '/departments', { name: 'Чужое' })).status, 403)
})

test('виды структуры: оргсхема, люди, дерево и таблица с выгрузкой CSV', async () => {
  const users = (await api(page, 'GET', '/users')).body
  const editor = users.find((u) => u.login === 'e2e.editor')
  const viewer = users.find((u) => u.login === 'e2e.viewer')
  const deps = (await api(page, 'GET', '/org/tree')).body.departments
  const company = deps.find((d) => d.name === 'ООО «Пульс»' && !d.parentId)
  const office = deps.find((d) => d.name === 'Проектный офис' && d.parentId === company.id)

  await page.goto(`${BASE}/org`)
  await page.locator('[data-testid=org-unit]').waitFor()
  const tab = (view) => page.locator(`[data-testid=org-view-${view}]`)

  // Оргсхема: карточки подразделений; выбор с клавиатуры (Enter) и мышью
  await tab('chart').click()
  assert.equal(await tab('chart').getAttribute('aria-selected'), 'true')
  await page.locator('[data-testid=org-chart]').waitFor()
  await page.locator(`[data-unit-id="${company.id}"] .card-main`).focus()
  await page.keyboard.press('Enter')
  await page.locator('[data-testid=org-unit] h2', { hasText: 'ООО «Пульс»' }).waitFor()
  await page.locator(`[data-unit-id="${office.id}"] .card-main`).click()
  await page.locator('[data-testid=org-unit] h2', { hasText: 'Проектный офис' }).waitFor()
  assert.match(page.url(), new RegExp(`view=chart.*unit=${office.id}|unit=${office.id}.*view=chart`))
  assert.match(await page.locator(`[data-unit-id="${office.id}"]`).getAttribute('class'), /is-selected/)
  await shot(page, '08-org-chart')

  // Люди: кто кому подчиняется; редактор — под руководителем «Проектного офиса»
  await tab('people').click()
  await page.locator('[data-testid=org-people]').waitFor()
  await page.locator(`[data-person-id="${editor.id}"] .card-main`).click()
  const person = page.locator('[data-testid=org-person]')
  await person.locator('h2', { hasText: 'E2E editor' }).waitFor()
  await person.locator('text=по структуре').waitFor()
  assert.match(page.url(), new RegExp(`user=${editor.id}`))
  // Поиск в схеме: найденный без руководителя и подчинённых раскрывается в группе «Без руководителя»
  await page.fill('[data-testid=org-search]', 'E2E viewer')
  await page.locator('[data-testid=org-search-result]', { hasText: 'E2E viewer' }).waitFor()
  await page.keyboard.press('Enter')
  await person.locator('h2', { hasText: 'E2E viewer' }).waitFor()
  await page.locator(`[data-person-id="${viewer.id}"]`).waitFor()
  await shot(page, '08-org-people')

  // Дерево — вид версии 0.3
  await tab('tree').click()
  await page.locator('.node-name', { hasText: 'Проектный офис' }).click()
  await page.locator('[data-testid=org-unit] h2', { hasText: 'Проектный офис' }).waitFor()

  // Таблица: сортировка и выгрузка CSV (UTF-8 с BOM)
  await tab('table').click()
  const table = page.locator('[data-testid=org-table]')
  await table.locator('tbody tr', { hasText: 'Проектный офис' }).waitFor()
  await table.locator('th button', { hasText: 'Сотрудников' }).click()
  assert.equal(await table.locator('th', { hasText: 'Сотрудников' }).getAttribute('aria-sort'), 'descending')
  const [download] = await Promise.all([page.waitForEvent('download'), page.click('[data-testid=org-export-csv]')])
  assert.match(download.suggestedFilename(), /^puls-structure-\d{4}-\d{2}-\d{2}\.csv$/)
  const csv = readFileSync(await download.path(), 'utf-8')
  assert.equal(csv.charCodeAt(0), 0xfeff)
  assert.match(csv, /^\uFEFFПуть;Подразделение;Тип;Уровень;/)
  assert.match(csv, /ООО «Пульс» \/ Проектный офис;Проектный офис;Отдел;2;ООО «Пульс»;/)
  await shot(page, '08-org-table')

  // Вид запоминается: без ?view= открывается последний выбранный; ссылка ?view=&user= выделяет сотрудника
  await page.goto(`${BASE}/org`)
  await page.locator('[data-testid=org-table]').waitFor()
  assert.equal(await tab('table').getAttribute('aria-selected'), 'true')
  await page.goto(`${BASE}/org?view=people&user=${editor.id}`)
  await page.locator(`[data-person-id="${editor.id}"].is-selected`).waitFor()
  await person.locator('h2', { hasText: 'E2E editor' }).waitFor()
  assert.deepEqual(page.errors, [])

  // Телефон: схема прокручивается внутри рамки, страница вбок не уезжает
  const phone = await newPage({ width: 390, height: 844 })
  await login(phone)
  await phone.goto(`${BASE}/org?view=chart`)
  await phone.locator('[data-testid=org-chart]').waitFor()
  await phone.locator(`[data-unit-id="${company.id}"]`).waitFor()
  const widths = await phone.evaluate(() => ({ page: document.documentElement.scrollWidth, main: document.querySelector('.app-main').scrollWidth }))
  assert.ok(widths.page <= 390 && widths.main <= 390, JSON.stringify(widths))
  await shot(phone, '08-org-chart-phone')
  assert.deepEqual(phone.errors, [])
})

test('доступ подразделению получает вся его ветка', async () => {
  const created = await api(page, 'POST', '/projects', { title: 'E2E Доступ отдела' })
  await page.goto(`${BASE}/admin/access`)
  await page.click('[data-testid=access-departments]')
  await page.locator('.option', { hasText: 'ООО «Пульс»' }).locator('input').check()
  await page.locator('.option', { hasText: 'E2E Доступ отдела' }).locator('input').check()
  await page.selectOption('.controls select >> nth=0', 'edit')
  await page.click('button:has-text("Выдать доступ (1 × 1)")')
  await page.click('button:has-text("Правила")')
  await page.locator('[data-testid=dep-rule]', { hasText: 'E2E Доступ отдела' }).waitFor()
  await shot(page, '08-access-departments')

  // Сотрудник вложенного «Проектного офиса» видит проект и может его править
  const editor = await newPage()
  await login(editor, 'e2e.editor')
  const projects = (await api(editor, 'GET', '/projects')).body.map((p) => p.title)
  assert.ok(projects.includes('E2E Доступ отдела'))
  const plan = await api(editor, 'GET', `/plans/${created.body.plans[0].id}`)
  assert.equal(plan.body.plan.access, 'edit')
})
