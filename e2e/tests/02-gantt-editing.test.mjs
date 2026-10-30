import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import { api, BASE, closeBrowser, login, newPage, shot, waitSaved } from '../helpers.mjs'

let page
let projectId
let planId

before(async () => {
  page = await newPage({ width: 1600, height: 950 })
  await login(page)
  const created = await api(page, 'POST', '/projects', { title: 'E2E Редактирование' })
  projectId = created.body.id
  planId = created.body.plans[0].id
  await api(page, 'PATCH', `/plans/${planId}`, { settings: { range: { start: '2026-05-01', end: '2026-08-31' }, scale: 'day', zoom: 1 } })
  await page.goto(`${BASE}/projects/${projectId}`)
  await page.waitForSelector('[data-testid=quick-add]')
})
after(closeBrowser)

async function planItems() {
  const res = await api(page, 'GET', `/plans/${planId}`)
  return res.body.items
}
async function addTask(name) {
  await page.fill('[data-testid=quick-add]', name)
  await page.press('[data-testid=quick-add]', 'Enter')
  await page.locator('.grid-row .name', { hasText: name }).waitFor()
}

test('быстрое добавление, переименование F2, сохранение на сервере', async () => {
  await addTask('Анализ требований')
  await addTask('Разработка')
  await waitSaved(page)
  await page.locator('.grid-row .name', { hasText: 'Разработка' }).click()
  await page.keyboard.press('F2')
  await page.fill('.rename-input', 'Разработка модуля')
  await page.keyboard.press('Enter')
  await waitSaved(page)
  const names = (await planItems()).map((i) => i.name)
  assert.deepEqual(names, ['Анализ требований', 'Разработка модуля'])
})

test('перетаскивание полосы сдвигает сроки, Ctrl+Z возвращает', async () => {
  const before = (await planItems()).find((i) => i.name === 'Анализ требований')
  await page.click('button:has-text("Дни")')
  const row = page.locator('.grid-row', { hasText: 'Анализ требований' })
  const key = await row.getAttribute('data-row')
  const bar = page.locator(`g.task[data-key="${key}"] .bar`)
  await bar.scrollIntoViewIfNeeded()
  const box = await bar.boundingBox()
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 + 26 * 3, box.y + box.height / 2, { steps: 10 })
  await page.mouse.up()
  await waitSaved(page)
  const moved = (await planItems()).find((i) => i.key === key)
  const shift = (new Date(moved.start) - new Date(before.start)) / 86400000
  assert.equal(shift, 3, `сдвиг на 3 дня, получено ${moved.start}`)
  assert.equal((new Date(moved.end) - new Date(before.end)) / 86400000, 3)
  await page.keyboard.press('Escape')
  await page.keyboard.press('Control+z')
  await waitSaved(page)
  const restored = (await planItems()).find((i) => i.key === key)
  assert.equal(restored.start, before.start)
})

test('связь «предшественник» сдвигает последующую задачу', async () => {
  const items = await planItems()
  const a = items.find((i) => i.name === 'Анализ требований')
  const b = items.find((i) => i.name === 'Разработка модуля')
  await page.locator('.grid-row', { hasText: 'Разработка модуля' }).locator('.open-btn').click()
  await page.waitForSelector('.panel')
  await page.locator('.panel label', { hasText: 'Предшественник' }).locator('select').selectOption(a.key)
  await waitSaved(page)
  const shifted = (await planItems()).find((i) => i.key === b.key)
  const expected = new Date(new Date(a.end).getTime() + 86400000).toISOString().slice(0, 10)
  assert.equal(shifted.start, expected)
  await shot(page, '02-dependency')
})

test('статус «Завершена» через контекстное меню спрашивает дату завершения', async () => {
  await page.keyboard.press('Escape')
  const row = page.locator('.grid-row', { hasText: 'Анализ требований' })
  await row.click({ button: 'right' })
  await page.locator('.menu-item', { hasText: 'Завершена' }).click()
  await page.waitForSelector('.modal')
  assert.match(await page.textContent('.modal'), /Когда задача фактически завершена/)
  await page.click('.modal button:has-text("В плановый срок")')
  await page.click('.modal .btn-primary:has-text("Завершить")')
  await waitSaved(page)
  const task = (await planItems()).find((i) => i.name === 'Анализ требований')
  assert.equal(task.status, 'done')
  assert.equal(task.completedDate, task.end)
  assert.equal(task.offset, 0)
})

test('фильтры: несколько значений подряд, панель в одну строку на 1366 px', async () => {
  await page.setViewportSize({ width: 1366, height: 900 })
  const toolbarHeight = () => page.locator('.toolbar').evaluate((el) => el.getBoundingClientRect().height)
  assert.ok(await toolbarHeight() < 60, 'панель инструментов умещается в одну строку')
  await page.click('button:has-text("Фильтры")')
  for (const status of ['Новая', 'Завершена']) await page.locator('.filter-option', { hasText: status }).locator('input').click()
  // Строка фильтров меняет раскладку — меню не должно закрываться после первой отметки
  assert.equal(await page.locator('.filters').count(), 1)
  const chips = (await page.locator('.filter-bar .chip').allTextContents()).map((t) => t.trim())
  assert.deepEqual(chips, ['Новая', 'Завершена'])
  await page.keyboard.press('Escape')
  await page.locator('.filter-bar button', { hasText: 'Сбросить' }).click()
  assert.equal(await page.locator('.filter-bar').count(), 0)
  await page.setViewportSize({ width: 1600, height: 950 })
})

test('группа: создание, перенос задачи в группу через карточку, удаление с выбором', async () => {
  await page.click('.split-caret')
  await page.locator('.menu-item', { hasText: 'Группа' }).click()
  await page.waitForSelector('.panel')
  await page.fill('[data-testid=panel-name]', 'Этап 1')
  await page.keyboard.press('Enter')
  await waitSaved(page)
  const group = (await planItems()).find((i) => i.type === 'group')
  assert.equal(group.name, 'Этап 1')
  await page.locator('.grid-row', { hasText: 'Анализ требований' }).locator('.open-btn').click()
  await page.locator('.panel label', { hasText: 'Входит в' }).locator('select').selectOption(group.key)
  await waitSaved(page)
  assert.equal((await planItems()).find((i) => i.name === 'Анализ требований').parentKey, group.key)
  await page.keyboard.press('Escape')
  await page.locator('.grid-row', { hasText: 'Этап 1' }).click()
  await page.keyboard.press('Delete')
  await page.waitForSelector('.modal')
  await page.click('.modal button:has-text("Оставить вложенные")')
  await waitSaved(page)
  const after = await planItems()
  assert.ok(!after.some((i) => i.type === 'group'))
  assert.equal(after.find((i) => i.name === 'Анализ требований').parentKey, null)
  assert.deepEqual(page.errors, [])
})
