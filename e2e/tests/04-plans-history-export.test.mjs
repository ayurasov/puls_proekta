import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { api, BASE, closeBrowser, login, newPage, shot, SHOTS, waitSaved } from '../helpers.mjs'

let page
let projectId

before(async () => {
  page = await newPage()
  await login(page)
  const created = await api(page, 'POST', '/projects', { title: 'E2E Планы' })
  projectId = created.body.id
  await page.goto(`${BASE}/projects/${projectId}`)
  await page.waitForSelector('[data-testid=quick-add]')
  await page.fill('[data-testid=quick-add]', 'Задача плана')
  await page.press('[data-testid=quick-add]', 'Enter')
  await waitSaved(page)
})
after(closeBrowser)

test('новый план и копия — вкладки; архив вместо потери вкладки', async () => {
  await page.click('[data-testid=plan-add]')
  await page.click('.menu-item:has-text("Копия текущего плана")')
  await page.locator('.plan-tab', { hasText: '(копия)' }).waitFor()
  assert.equal(await page.locator('.plan-tab').count(), 2)
  await page.locator('.plan-tab', { hasText: '(копия)' }).click({ button: 'right' })
  await page.click('.menu-item:has-text("Убрать в архив")')
  await page.click('.modal .btn-primary:has-text("В архив")')
  await page.locator('button:has-text("Архив · 1")').waitFor()
  assert.equal(await page.locator('.plan-tab').count(), 1)
  const project = await api(page, 'GET', `/projects/${projectId}`)
  assert.equal(project.body.plans.filter((p) => p.isArchived).length, 1)
})

test('сохранённая версия и восстановление', async () => {
  await page.click('[data-testid=history]')
  await page.click('.modal button:has-text("Версии")')
  await page.click('.modal button:has-text("Сохранить текущую версию")')
  const dialog = page.locator('.modal-overlay').last()
  await dialog.locator('input').fill('До удаления')
  await dialog.locator('button.btn-primary', { hasText: /^\s*Сохранить\s*$/ }).click()
  await page.locator('.snapshot', { hasText: 'До удаления' }).waitFor()
  await page.keyboard.press('Escape')
  await page.locator('.grid-row', { hasText: 'Задача плана' }).click()
  await page.keyboard.press('Delete')
  await page.click('.modal .btn-danger')
  await waitSaved(page)
  assert.equal(await page.locator('.grid-row', { hasText: 'Задача плана' }).count(), 0)
  await page.click('[data-testid=history]')
  await page.click('.modal button:has-text("Версии")')
  await page.locator('.snapshot', { hasText: 'До удаления' }).locator('button:has-text("Восстановить")').click()
  await page.locator('.modal-overlay').last().locator('button.btn-primary', { hasText: 'Восстановить' }).click()
  await page.locator('.grid-row', { hasText: 'Задача плана' }).waitFor({ timeout: 10000 })
  await shot(page, '04-restored')
})

test('экспорт PNG, PDF, CSV, JSON', async () => {
  const signatures = { png: [0x89, 0x50, 0x4e, 0x47], pdf: [0x25, 0x50, 0x44, 0x46] }
  for (const [label, kind] of [['Изображение PNG', 'png'], ['Документ PDF', 'pdf'], ['Таблица для Excel (CSV)', 'csv'], ['JSON (открывается и в 1.0)', 'json']]) {
    await page.click('button:has-text("Экспорт")')
    const [download] = await Promise.all([page.waitForEvent('download', { timeout: 30000 }), page.click(`.menu-item:has-text("${label}")`)])
    const path = `${SHOTS}/export.${kind}`
    await download.saveAs(path)
    const data = readFileSync(path)
    if (signatures[kind]) assert.deepEqual([...data.subarray(0, 4)], signatures[kind], kind)
    if (kind === 'csv') assert.match(data.toString('utf8'), /Название;Статус/)
    if (kind === 'json') assert.equal(JSON.parse(data.toString('utf8')).rows.length, 1)
  }
})
