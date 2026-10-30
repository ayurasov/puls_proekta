// Исправления 0.2.1 по итогам проверки дизайна: клавиатура, отмена удаления, склонения, телефон
import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import { api, BASE, closeBrowser, login, newPage, shot, waitSaved } from '../helpers.mjs'

let page
let projectId
let planId

before(async () => {
  page = await newPage({ width: 1366, height: 768 })
  await login(page)
  const created = await api(page, 'POST', '/projects', { title: 'E2E Проверка 0.2.1' })
  projectId = created.body.id
  planId = created.body.plans[0].id
  await page.goto(`${BASE}/projects/${projectId}`)
  await page.waitForSelector('[data-testid=quick-add]')
  await page.fill('[data-testid=quick-add]', 'Первая задача')
  await page.press('[data-testid=quick-add]', 'Enter')
  await waitSaved(page)
})
after(closeBrowser)

test('склонения: «1 строка», «2 строки»', async () => {
  await page.locator('.col-name', { hasText: '1 строка' }).waitFor()
  await page.fill('[data-testid=quick-add]', 'Вторая задача')
  await page.press('[data-testid=quick-add]', 'Enter')
  await page.locator('.col-name', { hasText: '2 строки' }).waitFor()
  await waitSaved(page)
})

test('Enter на кнопке нажимает кнопку, а не открывает карточку выбранной строки', async () => {
  await page.locator('.grid-row', { hasText: 'Первая задача' }).click()
  await page.locator('.toolbar button', { hasText: 'Фильтры' }).focus()
  await page.keyboard.press('Enter')
  await page.locator('.menu .filters').waitFor()
  assert.equal(await page.locator('.panel').count(), 0, 'карточка не открылась')
  // Стрелки в меню ходят по пунктам и не переключают строку диаграммы
  await page.keyboard.press('Escape')
  await page.locator('.grid-row', { hasText: 'Первая задача' }).click({ button: 'right' })
  await page.locator('.menu .menu-item').first().waitFor()
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('ArrowDown')
  assert.equal(await page.locator('.grid-row.selected .name').textContent(), 'Первая задача')
  await page.keyboard.press('Escape')
  // В свободной области Enter по-прежнему открывает карточку
  await page.locator('.grid-row', { hasText: 'Первая задача' }).click()
  await page.keyboard.press('Enter')
  await page.locator('.panel').waitFor()
  // Delete на кнопке карточки не удаляет строку
  await page.locator('.panel .segmented button', { hasText: 'В работе' }).focus()
  await page.keyboard.press('Delete')
  await page.waitForTimeout(300)
  assert.equal(await page.locator('.grid-row', { hasText: 'Первая задача' }).count(), 1)
  // Esc в поле названия возвращает прежнее название
  await page.fill('[data-testid=panel-name]', 'Случайно стёрто')
  await page.press('[data-testid=panel-name]', 'Escape')
  assert.equal(await page.inputValue('[data-testid=panel-name]'), 'Первая задача')
  await page.keyboard.press('Escape')
})

test('подсказка по клавишам — кнопкой «?» и клавишей «?»', async () => {
  await page.click('[data-testid=shortcuts]')
  await page.locator('.menu .shortcuts', { hasText: 'Ctrl+D' }).waitFor()
  await page.keyboard.press('Escape')
  await page.evaluate(() => document.activeElement?.blur())
  await page.keyboard.press('?')
  await page.locator('.menu .shortcuts').waitFor()
  await page.keyboard.press('Escape')
})

test('опасное подтверждение: фокус на «Отмене»', async () => {
  await page.locator('.grid-row', { hasText: 'Вторая задача' }).click()
  await page.click('[data-testid=plan-add]')
  await page.click('.menu-item:has-text("Копия текущего плана")')
  await page.locator('.plan-tab', { hasText: '(копия)' }).waitFor()
  await page.locator('.plan-tab', { hasText: '(копия)' }).click({ button: 'right' })
  await page.click('[data-testid=plan-delete]')
  await page.locator('.modal .btn-danger').waitFor()
  assert.equal(await page.evaluate(() => document.activeElement?.textContent?.trim()), 'Отмена')
  await page.keyboard.press('Escape')
})

test('удаление плана из меню вкладки — «Отменить» возвращает его', async () => {
  await page.locator('.plan-tab', { hasText: '(копия)' }).click({ button: 'right' })
  await page.click('[data-testid=plan-delete]')
  await page.click('.modal .btn-danger:has-text("В корзину")')
  const toast = page.locator('.toast', { hasText: 'в корзине' })
  await toast.waitFor()
  await page.waitForFunction(() => document.querySelectorAll('.plan-tab').length === 1)
  await shot(page, '06-plan-deleted-toast')
  await toast.locator('button', { hasText: 'Отменить' }).click()
  await page.locator('.plan-tab', { hasText: '(копия)' }).waitFor()
  const project = await api(page, 'GET', `/projects/${projectId}`)
  assert.equal(project.body.plans.filter((p) => !p.isArchived).length, 2)
})

test('карточка проекта считает только основной план', async () => {
  await page.goto(BASE + '/')
  const card = page.locator(`[data-testid=project-${projectId}]`)
  await card.waitFor()
  // Две задачи основного плана, копия с теми же задачами не удваивает
  assert.match(await card.locator('.card-stats').textContent(), /2 задачи/)
  assert.match(await card.locator('.card-footer').textContent(), /Изменён/)
})

test('поиск без результатов — «Ничего не найдено»', async () => {
  await page.fill('input[type=search]', 'такого проекта нет')
  await page.locator('[data-testid=projects-not-found]').waitFor()
  assert.match(await page.textContent('.page-subtitle'), /Найдено 0 из/)
  await page.fill('input[type=search]', '')
})

test('удаление проекта — «Отменить» восстанавливает его', async () => {
  const card = page.locator(`[data-testid=project-${projectId}]`)
  await card.locator('.card-menu').click()
  await page.click('.menu-item:has-text("Удалить")')
  await page.click('.modal .btn-danger:has-text("В корзину")')
  const toast = page.locator('.toast', { hasText: 'в корзине' })
  await toast.waitFor()
  await card.waitFor({ state: 'detached' })
  await toast.locator('button', { hasText: 'Отменить' }).click()
  await card.waitFor()
  const res = await api(page, 'GET', `/projects/${projectId}`)
  assert.equal(res.status, 200)
})

test('роль в таблице сотрудников — с отменой', async () => {
  const created = await api(page, 'POST', '/users', { name: 'Ролев Роман', login: 'rolev.rr', role: 'viewer', authSource: 'local', password: 'e2e-password-1' })
  assert.equal(created.status, 201)
  await page.goto(BASE + '/admin/users')
  const row = page.locator('tbody tr', { hasText: 'Ролев Роман' })
  await row.locator('select').selectOption('editor')
  const toast = page.locator('.toast', { hasText: 'Ролев Роман' })
  await toast.waitFor()
  await toast.locator('button', { hasText: 'Отменить' }).click()
  await page.waitForFunction(async () => {
    const res = await fetch('/api/users')
    return (await res.json()).find((u) => u.login === 'rolev.rr')?.role === 'viewer'
  })
})

test('реестр: подписи вместо ключей хранения', async () => {
  await page.goto(BASE + '/admin/audit')
  await page.locator('tbody tr').first().waitFor()
  const text = await page.locator('tbody').textContent()
  assert.ok(!/project #|plan #|user #|direction #/.test(text), 'объекты — названиями')
})

test('телефон: меню — выдвижная панель, страница без горизонтальной прокрутки', async () => {
  const phone = await newPage({ width: 390, height: 844 })
  await login(phone)
  await phone.waitForSelector('.project-card')
  assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true)
  assert.equal(await phone.locator('.sidebar').evaluate((el) => el.getBoundingClientRect().right <= 0), true, 'меню спрятано')
  await phone.click('[data-testid=mobile-nav]')
  await phone.locator('.mobile-nav-backdrop').waitFor()
  assert.equal(await phone.locator('.app-main').evaluate((el) => el.inert), true)
  await shot(phone, '06-phone-drawer')
  await phone.click('.mobile-nav-backdrop', { position: { x: 370, y: 400 } })
  await phone.locator('.mobile-nav-backdrop').waitFor({ state: 'detached' })
  // Карточка строки на телефоне — на весь экран
  await phone.goto(`${BASE}/projects/${projectId}/${planId}`)
  await phone.locator('.grid-row', { hasText: 'Первая задача' }).click()
  await phone.keyboard.press('Enter')
  await phone.locator('.panel').waitFor()
  await phone.waitForTimeout(400) // анимация появления карточки
  const box = await phone.locator('.panel').boundingBox()
  assert.ok(box.width >= 389 && box.x <= 1, `карточка ${box.width}px`)
  assert.deepEqual(phone.errors, [])
})
