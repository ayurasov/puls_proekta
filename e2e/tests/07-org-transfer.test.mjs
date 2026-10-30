import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { api, BASE, closeBrowser, login, newPage, shot } from '../helpers.mjs'

/**
 * Импорт и экспорт структуры (формат общий с «По Делу»): файл структуры выгружается и загружается
 * обратно, быстрый импорт CSV строит структуру, загрузка сотрудников из CSV разбирает коллизию.
 */
const dir = mkdtempSync(join(tmpdir(), 'puls-e2e-org-'))
let page

before(async () => {
  page = await newPage({ width: 1500, height: 950 })
  await login(page)
})
after(closeBrowser)

async function departments() {
  return (await api(page, 'GET', '/org/tree')).body.departments
}

test('файл структуры: выгрузка, загрузка того же файла без изменений, новое подразделение из файла', async () => {
  const company = await api(page, 'POST', '/departments', { name: 'АЛМИ Партнер', kind: 'company' })
  assert.equal(company.status, 201)
  const it = await api(page, 'POST', '/departments', { name: 'Разработка', parentId: company.body.id })
  const users = (await api(page, 'GET', '/users')).body
  const lead = users.find((u) => u.login === 'orlov.so')
  await api(page, 'PUT', `/departments/${it.body.id}/heads`, { userIds: [lead.id] })

  await page.goto(`${BASE}/admin/org-transfer`)
  await page.locator('.nav-item', { hasText: 'Импорт и экспорт' }).waitFor()
  const [download] = await Promise.all([page.waitForEvent('download'), page.click('[data-testid=structure-export]')])
  assert.match(download.suggestedFilename(), /^puls-structure-\d{4}-\d{2}-\d{2}\.json$/)
  const file = join(dir, download.suggestedFilename())
  await download.saveAs(file)
  const bundle = JSON.parse(readFileSync(file, 'utf-8'))
  assert.equal(bundle.format, 'podelu.org-structure')
  assert.deepEqual(bundle.departments.map((d) => d.name), ['АЛМИ Партнер', 'Разработка'])

  // Тот же файл — ничего нового
  await page.setInputFiles('[data-testid=structure-file]', file)
  const plan = page.locator('[data-testid=structure-plan]')
  await plan.waitFor()
  assert.match(await plan.textContent(), /Новых: 0/)
  assert.match(await plan.textContent(), /Ошибок: 0/)

  // В файле — новый отдел: план показывает его, «Применить» создаёт
  bundle.departments.push({ key: 'qa', name: 'Тестирование', parent: bundle.departments[1].key, kind: 'group', sortOrder: 0, heads: [] })
  const changed = join(dir, 'structure-changed.json')
  writeFileSync(changed, JSON.stringify(bundle))
  await page.setInputFiles('[data-testid=structure-file]', changed)
  await page.waitForFunction(() => /Новых: 1/.test(document.querySelector('[data-testid=structure-plan]')?.textContent || ''))
  assert.match(await page.textContent('[data-testid=structure-departments]'), /АЛМИ Партнер \/ Разработка \/ Тестирование/)
  await shot(page, '07-structure-plan')
  await page.click('[data-testid=structure-apply]')
  await page.locator('[data-testid=structure-applied]').waitFor()
  const qa = (await departments()).find((d) => d.name === 'Тестирование')
  assert.ok(qa && qa.kind === 'group' && qa.parentId === it.body.id)
  assert.deepEqual(page.errors, [])
})

test('быстрый импорт CSV: пример, проверка, применение', async () => {
  await page.goto(`${BASE}/admin/org-transfer?tab=csv`)
  await page.click('[data-testid=csv-sample]')
  await page.check('[data-testid=csv-create]')
  await page.click('[data-testid=import-preview]')
  const plan = page.locator('[data-testid=import-plan]')
  await plan.waitFor()
  const text = await plan.textContent()
  assert.match(text, /Строк: 3/)
  assert.match(text, /Новых подразделений: 2/)
  assert.match(text, /Новых сотрудников: 3/)
  assert.match(text, /ООО «Компания» \/ Продажи/)
  await shot(page, '07-csv-plan')
  await page.click('[data-testid=import-apply]')
  await page.locator('[data-testid=import-applied]').waitFor()
  const tree = (await api(page, 'GET', '/org/tree')).body
  const sales = tree.departments.find((d) => d.name === 'Продажи')
  assert.ok(sales && sales.headIds.length === 1)
  const people = (await api(page, 'GET', '/users')).body
  const petr = people.find((u) => u.email === 'manager@company.ru')
  assert.ok(petr && petr.departmentId === sales.id && petr.role === 'viewer' && petr.login === 'manager')
  // Повторная проверка того же текста — без изменений
  await page.click('[data-testid=import-preview]')
  await page.waitForFunction(() => /Без изменений: 3/.test(document.querySelector('[data-testid=import-plan]')?.textContent || ''))
})

test('сотрудники из CSV: коллизия по должности решается в пользу файла, новый сотрудник создаётся отключённым', async () => {
  const file = join(dir, 'employees.csv')
  writeFileSync(file, '\uFEFFemail;ФИО;Должность;Подразделение\r\n'
    + 'sergeev.av@almi-partner.ru;Сергеев Алексей Васильевич;Ведущий инженер-разработчик;АЛМИ Партнер / Разработка\r\n'
    + 'novikova.ea@almi-partner.ru;Новикова Елена Андреевна;Аналитик;АЛМИ Партнер / Разработка / Тестирование\r\n')
  await page.goto(`${BASE}/admin/org-transfer?tab=employees`)
  await page.check('[data-testid=new-users-inactive]')
  await page.setInputFiles('[data-testid=employees-file]', file)
  const plan = page.locator('[data-testid=employees-plan]')
  await plan.waitFor()
  await page.waitForFunction(() => /Коллизий: 1/.test(document.querySelector('[data-testid=employees-plan]')?.textContent || ''))
  assert.match(await plan.textContent(), /Новых: 1/)
  const record = page.locator('[data-record="sergeev.av@almi-partner.ru"]')
  const row = record.locator('tr[data-field=position]')
  assert.match(await row.textContent(), /Инженер-разработчик/)
  // Подразделение в системе пустое — заполняется без вопросов
  assert.match(await record.textContent(), /Подразделение:.*АЛМИ Партнер \/ Разработка.*в системе пусто/)
  await row.locator('[data-choice=incoming]').click()
  await page.waitForFunction(() => document.querySelector('[data-record="sergeev.av@almi-partner.ru"] tr[data-field=position] td.chosen:nth-child(3)'))
  await shot(page, '07-employees-conflict')
  await page.waitForFunction(() => !document.querySelector('[data-testid=employees-apply]')?.disabled)
  await page.click('[data-testid=employees-apply]')
  await page.locator('[data-testid=employees-applied]').waitFor()
  assert.match(await page.textContent('[data-testid=employees-applied]'), /новых 1, обновлено 1/)
  const people = (await api(page, 'GET', '/users')).body
  const sergeev = people.find((u) => u.login === 'sergeev.av')
  assert.equal(sergeev.position, 'Ведущий инженер-разработчик')
  const novikova = people.find((u) => u.email === 'novikova.ea@almi-partner.ru')
  assert.ok(novikova && novikova.isActive === false && novikova.login === 'novikova.ea')
  const tree = (await api(page, 'GET', '/org/tree')).body
  assert.equal(novikova.departmentId, tree.departments.find((d) => d.name === 'Тестирование').id)
  assert.deepEqual(page.errors, [])
})
