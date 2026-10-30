// 0.4: расширенные фильтры плана (сроки, «мои задачи», сохранённые фильтры) и статистика проекта с главной
import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { api, BASE, closeBrowser, login, newPage, shot, SHOTS } from '../helpers.mjs'

/** День относительно сегодняшнего (по местному времени, как «сегодня» на плане). */
function day(offset) {
  const d = new Date()
  d.setDate(d.getDate() + offset)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const ru = (iso) => iso.split('-').reverse().join('.')
const task = (key, name, start, end, position, extra = {}) => ({ op: 'item.create', item: { key, type: 'task', name, start, end, position, ...extra } })

let page
let projectId
let planId

before(async () => {
  page = await newPage({ width: 1440, height: 900 })
  await login(page)
  const me = (await api(page, 'GET', '/auth/me')).body.user
  const created = await api(page, 'POST', '/projects', { title: 'E2E Фильтры' })
  projectId = created.body.id
  planId = created.body.plans[0].id
  await api(page, 'PATCH', `/plans/${planId}`, { settings: { range: { start: day(-40), end: day(80) } } })
  const res = await api(page, 'POST', `/plans/${planId}/ops`, {
    ops: [
      { op: 'assignee.create', assignee: { key: 'a-me', name: me.name, userId: me.id, color: '#2563eb', position: 1 } },
      { op: 'assignee.create', assignee: { key: 'a-other', name: 'Сидоров Сидор', color: '#e5484d', position: 2 } },
      task('t-overdue', 'Просроченная задача', day(-20), day(-8), 1, { status: 'inwork', progress: 30, assigneeKey: 'a-me' }),
      task('t-week', 'Задача этой недели', day(0), day(0), 2, { assigneeKey: 'a-other' }),
      task('t-future', 'Будущая задача', day(40), day(45), 3, { assigneeKey: 'a-other' }),
      task('t-shifted', 'Сдвинутая задача', day(20), day(25), 4, { offset: 2 }),
      { op: 'item.create', item: { key: 'm-1', type: 'milestone', name: 'Веха проекта', date: day(30), position: 5 } },
    ],
  })
  assert.equal(res.status, 200, JSON.stringify(res.body))
  await api(page, 'POST', `/plans/${planId}/items/t-future/comments`, { text: 'Уточнить сроки' })
})
after(closeBrowser)

const rows = () => page.locator('.grid-row .name').allTextContents()
const chips = async () => (await page.locator('.filter-bar .chip').allTextContents()).map((t) => t.trim())

async function openFilters() {
  if (!(await page.locator('.menu .filters').count())) await page.locator('.toolbar button', { hasText: 'Фильтры' }).click()
  await page.locator('.menu .filters').waitFor()
}
async function closeFilters() {
  await page.keyboard.press('Escape')
  await page.locator('.menu .filters').waitFor({ state: 'detached' })
}

test('фильтры плана: сроки, свой период, «Мои задачи», числа у пунктов', async () => {
  await page.goto(`${BASE}/projects/${projectId}`)
  await page.locator('.grid-row .name', { hasText: 'Будущая задача' }).waitFor()
  await openFilters()
  await page.click('[data-testid=filter-group-dates]')
  assert.match(await page.textContent('[data-testid=filter-dates-overdue]'), /Просрочены\s*1/)
  assert.match(await page.textContent('[data-testid=filter-dates-shifted]'), /Сроки сдвинуты\s*1/)
  await page.click('[data-testid=filter-dates-overdue]')
  // Веха ни к чему не привязана — она контекст и остаётся на виду
  assert.deepEqual(await rows(), ['Просроченная задача', 'Веха проекта'])
  assert.deepEqual(await chips(), ['Просрочены'])
  // Внутри группы — «или»
  await page.click('[data-testid=filter-dates-week]')
  assert.deepEqual(await rows(), ['Просроченная задача', 'Задача этой недели', 'Веха проекта'])
  await page.click('[data-testid=filter-dates-overdue]')
  await page.click('[data-testid=filter-dates-week]')
  // Свой период: ввод даты сам отмечает пункт
  await page.fill('[data-testid=filter-period-from]', day(39))
  await page.fill('[data-testid=filter-period-to]', day(41))
  assert.equal(await page.locator('[data-testid=filter-dates-period] input').isChecked(), true)
  assert.deepEqual(await rows(), ['Будущая задача', 'Веха проекта'])
  assert.deepEqual(await chips(), [`Свой период: ${ru(day(39))} — ${ru(day(41))}`])
  await shot(page, '10-filters-period')
  // Между группами — «и»: свой период и «Мои задачи» вместе — ничего (кроме вехи-контекста)
  assert.match(await page.textContent('.filter-mine'), /Только мои задачи\s*1/)
  await page.click('[data-testid=filter-mine]')
  assert.deepEqual(await rows(), ['Веха проекта'])
  await page.click('[data-testid=filter-dates-period]')
  assert.deepEqual(await rows(), ['Просроченная задача', 'Веха проекта'])
  assert.deepEqual(await chips(), ['Мои задачи'])
  // Тип и прочее: «С комментариями», «Только вехи»
  await page.click('[data-testid=filter-mine]')
  await page.click('[data-testid=filter-group-traits]')
  await page.click('[data-testid=filter-traits-comments]')
  assert.deepEqual(await rows(), ['Будущая задача', 'Веха проекта'])
  await page.click('[data-testid=filter-traits-comments]')
  await page.click('[data-testid=filter-traits-milestones]')
  assert.deepEqual(await rows(), ['Веха проекта'])
  await closeFilters()
  await page.locator('.filter-bar button', { hasText: 'Сбросить' }).click()
  assert.equal(await page.locator('.filter-bar').count(), 0)
  assert.equal((await rows()).length, 5)
  assert.deepEqual(page.errors, [])
})

test('сохранённый фильтр: сохранить, применить после перезагрузки, удалить', async () => {
  await openFilters()
  await page.click('[data-testid=filter-mine]')
  await page.click('[data-testid=filter-group-dates]')
  await page.click('[data-testid=filter-dates-overdue]')
  await page.click('[data-testid=filters-save]')
  await page.fill('[data-testid=saved-filter-name]', 'Мои просроченные')
  await page.click('[data-testid=saved-filter-save]')
  await page.locator('[data-testid=saved-filter]', { hasText: 'Мои просроченные' }).waitFor()
  assert.match(await page.textContent('[data-testid=saved-filter]'), /Мои задачи, Просрочены/)
  await shot(page, '10-filters-saved')
  // Отметки вида не запоминаются, сохранённый фильтр — да (у сотрудника и плана)
  await page.reload()
  await page.locator('.grid-row .name', { hasText: 'Будущая задача' }).waitFor()
  assert.equal(await page.locator('.filter-bar').count(), 0)
  await openFilters()
  await page.click('[data-testid=filter-group-saved]')
  await page.locator('[data-testid=saved-filter]', { hasText: 'Мои просроченные' }).click()
  assert.deepEqual(await chips(), ['Мои задачи', 'Просрочены'])
  assert.deepEqual(await rows(), ['Просроченная задача', 'Веха проекта'])
  assert.equal(await page.locator('.saved-row.is-current').count(), 1)
  await page.click('[data-testid=saved-filter-delete]')
  await page.locator('[data-testid=saved-filter]').waitFor({ state: 'detached' })
  await closeFilters()
})

test('экспорт CSV — с действующими фильтрами', async () => {
  // Отметки «Мои задачи» и «Просрочены» остались от сохранённого фильтра
  assert.deepEqual(await chips(), ['Мои задачи', 'Просрочены'])
  await page.click('button:has-text("Экспорт")')
  const [download] = await Promise.all([page.waitForEvent('download'), page.click('.menu-item:has-text("Таблица для Excel (CSV)")')])
  const path = `${SHOTS}/filtered.csv`
  await download.saveAs(path)
  const csv = readFileSync(path, 'utf8')
  assert.match(csv, /Просроченная задача/)
  assert.doesNotMatch(csv, /Будущая задача|Задача этой недели/)
  await page.locator('.filter-bar button', { hasText: 'Сбросить' }).click()
})

test('телефон: окно фильтров во всю ширину, группы — вкладками', async () => {
  const phone = await newPage({ width: 390, height: 844 })
  await login(phone)
  await phone.goto(`${BASE}/projects/${projectId}/${planId}`)
  await phone.locator('.grid-row .name', { hasText: 'Будущая задача' }).waitFor()
  await phone.locator('.mobile-search button', { hasText: 'Фильтры' }).click()
  await phone.locator('.menu .filters').waitFor()
  await phone.click('[data-testid=filter-group-dates]')
  await phone.click('[data-testid=filter-dates-overdue]')
  const box = await phone.locator('.menu').boundingBox()
  assert.ok(box.width >= 370 && box.x >= 0 && box.x + box.width <= 390, `окно ${box.x}+${box.width}`)
  assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true)
  await shot(phone, '10-filters-phone')
  assert.deepEqual(phone.errors, [])
})

test('главная: статистика проекта без входа в проект и переход к задаче', async () => {
  await page.goto(BASE + '/')
  const card = page.locator(`[data-testid=project-${projectId}]`)
  await card.waitFor()
  await card.locator(`[data-testid=project-stats-${projectId}]`).click()
  const modal = page.locator('.modal')
  await modal.locator('h2', { hasText: 'Статистика: E2E Фильтры' }).waitFor()
  await modal.locator('.stat', { hasText: 'Просрочено сейчас' }).locator('strong', { hasText: '1' }).waitFor()
  assert.match(await modal.locator('.stat', { hasText: 'Задач' }).first().textContent(), /4/)
  assert.equal(new URL(page.url()).pathname, '/')
  await shot(page, '10-dashboard-stats')
  // Строка из «Просрочено на сегодня» открывает план с карточкой задачи
  await modal.locator('.link-row', { hasText: 'Просроченная задача' }).first().click()
  await page.waitForURL(new RegExp(`/projects/${projectId}/${planId}`))
  await page.locator('.panel').waitFor()
  assert.equal(await page.inputValue('[data-testid=panel-name]'), 'Просроченная задача')
  // Параметр убирается из адреса, чтобы не открывать карточку снова
  await page.waitForURL((url) => !url.search.includes('item='))
  // «Статистика» в меню «…» и «Открыть проект»
  await page.goto(BASE + '/')
  await card.locator('.card-menu').click()
  await page.click('.menu-item:has-text("Статистика")')
  await page.locator('[data-testid=stats-open-project]').click()
  await page.waitForURL(new RegExp(`/projects/${projectId}/${planId}$`))
  await page.locator('.grid-row .name', { hasText: 'Будущая задача' }).waitFor()
  assert.deepEqual(page.errors, [])
})

test('главная: фильтры по состоянию и направлению, сортировка, запоминание выбора', async () => {
  await page.goto(BASE + '/')
  const card = page.locator(`[data-testid=project-${projectId}]`)
  await card.waitFor()
  const all = await page.locator('.project-card').count()
  assert.equal(await card.getAttribute('draggable'), 'true')
  await page.selectOption('[data-testid=filter-state]', 'overdue')
  await card.waitFor()
  const overdue = await page.locator('.project-card').count()
  assert.ok(overdue < all, `просроченных ${overdue} из ${all}`)
  for (const stats of await page.locator('.project-card .card-stats').allTextContents()) assert.match(stats, /просроч/)
  // С фильтром карточки не перетаскиваются
  assert.equal(await card.getAttribute('draggable'), 'false')
  assert.match(await page.textContent('.page-subtitle'), new RegExp(`Найдено ${overdue} из`))
  await page.selectOption('[data-testid=filter-state]', 'done')
  await card.waitFor({ state: 'detached' })
  await page.selectOption('[data-testid=filter-state]', '')
  // Направление «Без направления»: проекты E2E созданы без направления
  await page.selectOption('[data-testid=filter-direction]', 'none')
  await card.waitFor()
  assert.deepEqual(await page.locator('.direction-header h2').allTextContents(), ['Без направления'])
  // Сортировка по названию внутри направления
  await page.selectOption('[data-testid=projects-sort]', 'title')
  const titles = await page.locator('.project-card .card-title').allTextContents()
  assert.deepEqual(titles, [...titles].sort((a, b) => a.localeCompare(b, 'ru', { sensitivity: 'base', numeric: true })))
  assert.equal(await card.getAttribute('draggable'), 'false')
  await page.click('[data-testid=filter-mine-projects]')
  assert.equal(await page.getAttribute('[data-testid=filter-mine-projects]', 'aria-pressed'), 'true')
  await shot(page, '10-dashboard-filters')
  // Выбор запоминается
  await page.reload()
  await card.waitFor()
  assert.equal(await page.inputValue('[data-testid=filter-direction]'), 'none')
  assert.equal(await page.inputValue('[data-testid=projects-sort]'), 'title')
  assert.equal(await page.getAttribute('[data-testid=filter-mine-projects]', 'aria-pressed'), 'true')
  // Ничего не найдено — сброс фильтров
  await page.selectOption('[data-testid=filter-state]', 'done')
  await page.fill('input[type=search]', 'E2E Фильтры')
  await page.locator('[data-testid=projects-not-found]').waitFor()
  await page.click('[data-testid=projects-not-found] button:has-text("Сбросить фильтры")')
  await card.waitFor()
  await page.fill('input[type=search]', '')
  await page.selectOption('[data-testid=projects-sort]', 'order')
  assert.equal(await page.locator('.project-card').count(), all)
  assert.equal(await card.getAttribute('draggable'), 'true')
  assert.deepEqual(page.errors, [])
})

test('главная на телефоне: фильтры в несколько строк, без горизонтальной прокрутки', async () => {
  const phone = await newPage({ width: 390, height: 844 })
  await login(phone)
  await phone.locator('[data-testid=dashboard-filters]').waitFor()
  assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true)
  await phone.locator(`[data-testid=project-stats-${projectId}]`).click()
  await phone.locator('.modal .stat', { hasText: 'Просрочено сейчас' }).waitFor()
  await phone.waitForTimeout(300) // анимация появления окна
  await shot(phone, '10-dashboard-phone-stats')
  assert.deepEqual(phone.errors, [])
})
