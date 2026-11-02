// 0.7.0 (ADR-0012): несколько предшественников и связи вех — второй предшественник в карточке и две стрелки, перенос
// предшественника сдвигает последующую задачу по наибольшему ограничению, связь в веху, связь перетаскиванием от конца
// полосы; крайний срок на диаграмме (зелёный, при срыве — красный) и фильтр, «Начало не ранее», столбец
// «Предшественники» в CSV
import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { api, BASE, closeBrowser, login, newPage, SHOTS, shot, waitSaved } from '../helpers.mjs'

const task = (key, name, start, end, position) => ({ op: 'item.create', item: { key, type: 'task', name, start, end, position } })

let page
let projectId
let planId

before(async () => {
  page = await newPage({ width: 1600, height: 950 })
  await login(page)
  const created = await api(page, 'POST', '/projects', { title: 'E2E Связи и сроки' })
  projectId = created.body.id
  planId = created.body.plans[0].id
  await api(page, 'PATCH', `/plans/${planId}`, { settings: { range: { start: '2026-05-01', end: '2026-07-31' }, scale: 'day', zoom: 1 } })
  const res = await api(page, 'POST', `/plans/${planId}/ops`, { ops: [
    task('a', 'Анализ', '2026-05-04', '2026-05-08', 1024),
    task('b', 'Дизайн', '2026-05-04', '2026-05-12', 2048),
    task('c', 'Разработка', '2026-05-04', '2026-05-07', 3072),
    { op: 'item.create', item: { key: 'm', type: 'milestone', name: 'Сдача', date: '2026-06-30', position: 4096 } },
  ] })
  assert.equal(res.status, 200, JSON.stringify(res.body))
  await page.goto(`${BASE}/projects/${projectId}`)
  await page.waitForSelector('.gantt .grid-row')
  await page.click('button:has-text("Дни")')
})
after(closeBrowser)

async function plan() {
  return (await api(page, 'GET', `/plans/${planId}`)).body
}
async function row(key) {
  return (await plan()).items.find((i) => i.key === key)
}
async function openCard(name) {
  await page.locator('.grid-row', { hasText: name }).locator('.open-btn').click()
  await page.waitForFunction((n) => document.querySelector('[data-testid=panel-name]')?.value === n, name)
}
/** Дата в карточке: ввод и change, как при выборе в календаре. */
async function setDate(testid, value) {
  const input = page.locator(`.panel [data-testid=${testid}]`)
  await input.fill(value)
  await input.dispatchEvent('change')
  await waitSaved(page)
}

test('второй предшественник в карточке: две стрелки, задача — после позднего из предшественников', async () => {
  await openCard('Разработка')
  // Листание списка стрелками связей не создаёт — только «Добавить»
  await page.locator('.panel [data-testid=link-add]').focus()
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('ArrowDown')
  await page.waitForTimeout(300)
  assert.equal((await plan()).links.length, 0)
  assert.equal(await page.locator('.panel [data-testid=link-pred]').count(), 0)
  await page.locator('.panel [data-testid=link-add]').selectOption('a')
  await page.click('.panel [data-testid=link-add-submit]')
  await waitSaved(page)
  await page.locator('.panel [data-testid=link-add]').selectOption('b')
  await page.click('.panel [data-testid=link-add-submit]')
  await waitSaved(page)
  const doc = await plan()
  assert.deepEqual(doc.links.map((l) => [l.from, l.to, l.type]), [['a', 'c', 'FS'], ['b', 'c', 'FS']])
  const c = doc.items.find((i) => i.key === 'c')
  assert.deepEqual([c.start, c.end], ['2026-05-13', '2026-05-16'])
  assert.equal(await page.locator('.panel [data-testid=link-pred]').count(), 2)
  assert.equal(await page.locator('.arrows path[data-link]').count(), 2)
  // Связи выбранной строки выделены
  assert.equal(await page.locator('.arrows path.active').count(), 2)
  await shot(page, '14-two-predecessors')
})

test('перенос предшественника сдвигает последующую по наибольшему ограничению; лаг в рабочих днях', async () => {
  await openCard('Анализ')
  await setDate('panel-end', '2026-05-20')
  let c = await row('c')
  assert.deepEqual([c.start, c.end], ['2026-05-21', '2026-05-24'])
  await setDate('panel-end', '2026-05-06')
  c = await row('c')
  assert.deepEqual([c.start, c.end], ['2026-05-13', '2026-05-16'], 'теперь начало задаёт «Дизайн»')
  // Лаг второго предшественника — 4 дня: «Дизайн» заканчивается во вторник 12.05 → начало в воскресенье 17.05;
  // в рабочих днях — через выходные, во вторник 19.05
  await openCard('Разработка')
  const second = page.locator('.panel [data-testid=link-pred]').nth(1)
  await second.locator('[data-testid=link-lag]').fill('4')
  await second.locator('[data-testid=link-lag]').dispatchEvent('change')
  await waitSaved(page)
  c = await row('c')
  assert.deepEqual([c.start, c.end], ['2026-05-17', '2026-05-20'])
  await second.locator('[data-testid=link-lag-kind]', { hasText: 'кал.' }).click()
  await waitSaved(page)
  c = await row('c')
  assert.deepEqual([c.start, c.end], ['2026-05-19', '2026-05-22'])
  const link = (await plan()).links.find((l) => l.from === 'b')
  assert.deepEqual([link.lag, link.lagKind], [4, 'working'])
  await page.locator('.panel [data-testid=item-links]').scrollIntoViewIfNeeded()
  await shot(page, '14-links-card')
  // Отмена — назад к календарным дням, задача — на прежние даты
  await page.keyboard.press('Escape')
  await page.keyboard.press('Control+z')
  await waitSaved(page)
  assert.equal((await plan()).links.find((l) => l.from === 'b').lagKind, 'calendar')
  assert.equal((await row('c')).start, '2026-05-17')
})

test('связь в веху: веха встаёт на окончание предшественника, цикл не предлагается', async () => {
  await openCard('Сдача')
  await page.locator('.panel [data-testid=link-add]').selectOption('c')
  await page.click('.panel [data-testid=link-add-submit]')
  await waitSaved(page)
  const doc = await plan()
  const c = doc.items.find((i) => i.key === 'c')
  assert.equal(doc.items.find((i) => i.key === 'm').date, c.end)
  assert.ok(doc.links.some((l) => l.from === 'c' && l.to === 'm'))
  assert.equal(await page.locator('.arrows path[data-link]').count(), 3)
  // В карточке «Анализа» строки, которые от него зависят, недоступны: связь замкнула бы цикл
  await openCard('Анализ')
  assert.equal(await page.locator('.panel [data-testid=link-add] option[value=c]').isDisabled(), true)
  assert.equal(await page.locator('.panel [data-testid=link-add] option[value=m]').isDisabled(), true)
  assert.equal(await page.locator('.panel [data-testid=link-add] option[value=b]').isDisabled(), false)
  assert.equal(await page.locator('.panel [data-testid=link-succ]').count(), 1)
  await shot(page, '14-milestone-link')
})

test('связь перетаскиванием от конца полосы к другой строке', async () => {
  await page.keyboard.press('Escape')
  const bar = page.locator('g.task[data-key="a"] .bar')
  await bar.scrollIntoViewIfNeeded()
  await bar.hover()
  const handle = await page.locator('g.task[data-key="a"] [data-testid=link-handle]').boundingBox()
  const target = await page.locator('g.task[data-key="b"] .bar').boundingBox()
  await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2)
  await page.mouse.down()
  await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 8 })
  await page.mouse.up()
  await waitSaved(page)
  const doc = await plan()
  assert.ok(doc.links.some((l) => l.from === 'a' && l.to === 'b' && l.type === 'FS'))
  // «Дизайн» — после «Анализа» (окончание 06.05), дальше по цепочке — «Разработка» и веха
  const b = doc.items.find((i) => i.key === 'b')
  assert.equal(b.start, '2026-05-07')
  const c = doc.items.find((i) => i.key === 'c')
  assert.ok(c.start > b.end)
  assert.equal(doc.items.find((i) => i.key === 'm').date, c.end)
  assert.deepEqual(page.errors, [])
})

test('крайний срок: зелёная метка, при срыве — красная; фильтр «Срыв крайнего срока»', async () => {
  const c = await row('c')
  await openCard('Разработка')
  const later = new Date(new Date(c.end).getTime() + 5 * 86400000).toISOString().slice(0, 10)
  await setDate('panel-deadline', later)
  const marker = page.locator('[data-testid=deadline-marker][data-deadline=c]')
  assert.equal(await marker.getAttribute('fill'), '#16a34a')
  assert.equal(await page.locator('[data-testid=panel-deadline-late]').count(), 0)
  await setDate('panel-deadline', c.start)
  assert.equal(await marker.getAttribute('fill'), '#dc2626')
  await page.locator('[data-testid=panel-deadline-late]').waitFor()
  assert.equal((await row('c')).deadline, c.start)
  await shot(page, '14-deadline')
  await page.keyboard.press('Escape')
  await page.locator('.toolbar button', { hasText: 'Фильтры' }).click()
  await page.click('[data-testid=filter-group-dates]')
  assert.match(await page.textContent('[data-testid=filter-dates-deadline]'), /Срыв крайнего срока\s*1/)
  await page.click('[data-testid=filter-dates-deadline]')
  await page.keyboard.press('Escape')
  const names = await page.locator('.grid-row .name').allTextContents()
  assert.ok(names.includes('Разработка') && !names.includes('Анализ') && !names.includes('Дизайн'), names.join(', '))
  await page.locator('.filter-bar button', { hasText: 'Сбросить' }).click()
})

test('CSV: столбец «Предшественники» с номерами строк, как в MS Project', async () => {
  await page.click('button:has-text("Экспорт")')
  const [download] = await Promise.all([page.waitForEvent('download'), page.click('.menu-item:has-text("Таблица для Excel (CSV)")')])
  const path = `${SHOTS}/links.csv`
  await download.saveAs(path)
  const text = readFileSync(path, 'utf8')
  assert.equal(text.charCodeAt(0), 0xfeff)
  const lines = text.slice(1).split('\r\n')
  const header = lines[0].split(';')
  assert.equal(header[0], '№')
  const column = header.indexOf('Предшественники')
  assert.ok(column > 0)
  assert.ok(header.includes('Крайний срок'))
  // 1 Анализ, 2 Дизайн, 3 Разработка, 4 Сдача
  assert.match(lines[2], /^2;1;Задача;Дизайн;/)
  assert.match(lines[2], /;1FS;/)
  assert.match(lines[3], /;"1FS;2FS\+4д";/)
  assert.match(lines[4], /^4;1;Веха;Сдача;.*;3FS;/)
})

test('«Начало не ранее» не пускает задачу раньше даты', async () => {
  await openCard('Анализ')
  await setDate('panel-snet', '2026-05-11')
  const a = await row('a')
  assert.deepEqual([a.start, a.end, a.constraintType, a.constraintDate], ['2026-05-11', '2026-05-13', 'snet', '2026-05-11'])
  // Последующие сдвигаются вслед
  assert.equal((await row('b')).start, '2026-05-14')
  await page.locator('.panel .hints', { hasText: 'Начало не ранее 11.05.2026' }).waitFor()
  assert.deepEqual(page.errors, [])
})
