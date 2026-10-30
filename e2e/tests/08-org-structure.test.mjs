import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
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
  await page.goto(`${BASE}/admin/structure`)
  await page.locator('text=Подразделения ещё не созданы').waitFor()
  await addUnit('ООО «Пульс»', 'Верхний уровень', 'Компания')
  await addUnit('Проектный офис', 'ООО «Пульс»', 'Отдел')
  assert.equal(await page.locator('[data-testid=dep-row]').count(), 2)

  const users = (await api(page, 'GET', '/users')).body
  const admin = users.find((u) => u.login === 'admin')
  const editor = users.find((u) => u.login === 'e2e.editor')
  const deps = (await api(page, 'GET', '/org/tree')).body.departments
  const office = deps.find((d) => d.name === 'Проектный офис')

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
  await viewer.locator('.node-name', { hasText: 'Проектный офис' }).click()
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
