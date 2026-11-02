// 0.6.1: карточка проекта с несколькими планами (пустая первая вкладка не прячет сводку), статистика с переключением
// между планами, вид прогресса на полосах (заливка как в 1.0 — по умолчанию, полоска, светлый остаток, с процентом)
import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import { api, BASE, closeBrowser, login, newPage, shot } from '../helpers.mjs'

function day(offset) {
  const d = new Date()
  d.setDate(d.getDate() + offset)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const task = (key, name, start, end, position, extra = {}) => ({ op: 'item.create', item: { key, type: 'task', name, start, end, position, ...extra } })

let page
let projectId
let firstPlanId
let stage2
let stage3

before(async () => {
  page = await newPage({ width: 1440, height: 950 })
  await login(page)
  const created = await api(page, 'POST', '/projects', { title: 'E2E Подпроекты' })
  projectId = created.body.id
  firstPlanId = created.body.plans[0].id // первая вкладка остаётся пустой
  stage2 = (await api(page, 'POST', `/projects/${projectId}/plans`, { title: 'Этап 2' })).body.id
  stage3 = (await api(page, 'POST', `/projects/${projectId}/plans`, { title: 'Этап 3' })).body.id
  for (const [planId, ops] of [
    [stage2, [
      task('a', 'Сборка стенда', day(-5), day(10), 1, { status: 'inwork', progress: 60, color: 'blue' }),
      task('b', 'Обследование', day(-20), day(-10), 2, { status: 'done', progress: 100, completedDate: day(-10) }),
      { op: 'item.create', item: { key: 'm', type: 'milestone', name: 'Приёмка этапа 2', date: day(20), position: 3 } },
    ]],
    [stage3, [task('c', 'Интеграция с ERP', day(0), day(15), 1, { status: 'blocked', progress: 20 })]],
  ]) {
    await api(page, 'PATCH', `/plans/${planId}`, { settings: { range: { start: day(-30), end: day(40) } } })
    const res = await api(page, 'POST', `/plans/${planId}/ops`, { ops })
    assert.equal(res.status, 200, JSON.stringify(res.body))
  }
})
after(async () => {
  await api(page, 'DELETE', `/projects/${projectId}`)
  await closeBrowser()
})

test('карточка: каждый план своей строкой, пустая первая вкладка не прячет сводку', async () => {
  await page.goto(BASE + '/')
  const card = page.locator(`[data-testid=project-${projectId}]`)
  await card.waitFor()
  const rows = card.locator(`[data-testid=card-plans-${projectId}] .plan-row`)
  assert.equal(await rows.count(), 3)
  assert.deepEqual((await rows.locator('.plan-name').allTextContents()).map((t) => t.trim()), ['E2E Подпроекты', 'Этап 2', 'Этап 3'])
  assert.match(await rows.nth(0).textContent(), /задач пока нет/)
  assert.equal(await rows.nth(0).locator('.plan-name').getAttribute('href'), `/projects/${projectId}/${firstPlanId}`)
  assert.match(await rows.nth(1).textContent(), /2 задачи/)
  assert.match(await rows.nth(1).locator('.progress-value').textContent(), /80%/)
  assert.match(await rows.nth(2).textContent(), /1 заблок\./)
  // Ближайшая веха — с названием плана, в котором она стоит
  assert.match(await card.locator('.milestone').textContent(), /Приёмка этапа 2 · Этап 2/)
  await shot(page, '13-card-plans')
  // Название плана в строке открывает этот план
  await rows.nth(2).locator('.plan-name').click()
  await page.waitForURL(new RegExp(`/projects/${projectId}/${stage3}$`))
  await page.locator('.grid-row .name', { hasText: 'Интеграция с ERP' }).waitFor()
})

test('статистика с карточки: по умолчанию — первый план с задачами, переключается между планами', async () => {
  await page.goto(BASE + '/')
  const card = page.locator(`[data-testid=project-${projectId}]`)
  await card.locator(`[data-testid=project-stats-${projectId}]`).click()
  const modal = page.locator('.modal')
  const tabs = modal.locator('[data-testid=stats-plan-switch] [role=tab]')
  await tabs.first().waitFor()
  assert.equal(await tabs.count(), 3)
  assert.equal((await modal.locator('[role=tab][aria-selected=true] .seg-label').textContent()).trim(), 'Этап 2')
  await modal.locator('.stat', { hasText: 'Задач' }).first().locator('strong', { hasText: '2' }).waitFor()
  await tabs.filter({ hasText: 'Этап 3' }).click()
  await modal.locator('.stat', { hasText: 'Задач' }).first().locator('strong', { hasText: '1' }).waitFor()
  await shot(page, '13-stats-switch')
  // «Открыть проект» ведёт в выбранный план
  await modal.locator('[data-testid=stats-open-project]').click()
  await page.waitForURL(new RegExp(`/projects/${projectId}/${stage3}`))
  // Кнопка статистики в строке плана открывает статистику этого плана
  await page.goto(BASE + '/')
  await card.locator('.plan-row', { hasText: 'Этап 3' }).locator('.plan-stats-btn').click()
  await page.locator('.modal [role=tab][aria-selected=true]', { hasText: 'Этап 3' }).waitFor()
  await page.keyboard.press('Escape')
  assert.deepEqual(page.errors, [])
})

test('вид прогресса на полосах: заливка по умолчанию, полоска, светлый остаток, с процентом, без прогресса', async () => {
  await page.goto(`${BASE}/projects/${projectId}/${stage2}`)
  const bar = page.locator('.task[data-key=a]')
  await bar.waitFor()
  // По умолчанию — заливка во всю высоту полосы, как в 1.0
  const fill = bar.locator('.progress-done.is-shade')
  await fill.waitFor()
  const barBox = await bar.locator('.bar').boundingBox()
  const fillBox = await fill.boundingBox()
  assert.ok(Math.abs(fillBox.height - barBox.height) < 1, 'заливка во всю высоту')
  assert.ok(Math.abs(fillBox.width - barBox.width * 0.6) < 2, 'ширина — 60 % полосы')

  await page.click('button[title="Настройки графика"]')
  const picker = page.locator('[data-testid=progress-style]')
  await picker.waitFor()
  assert.equal(await picker.locator('[aria-checked=true]').getAttribute('data-value'), 'fill')
  await picker.scrollIntoViewIfNeeded()
  await shot(page, '13-progress-settings')

  await picker.locator('[data-value=strip]').click()
  await bar.locator('rect.progress').waitFor()
  assert.equal(await bar.locator('.progress-done').count(), 0)

  await picker.locator('[data-value=tint]').click()
  await bar.locator('.progress-done.is-tint').waitFor()
  assert.notEqual(await bar.locator('.bar').getAttribute('fill'), '#3b82f6') // остаток — светлый тон

  await picker.locator('[data-value=percent]').click()
  await page.locator('.task[data-key=a] .bar-text', { hasText: /^60% · / }).waitFor()

  await picker.locator('[data-value=none]').click()
  await page.waitForFunction(() => !document.querySelector('.task[data-key=a] .progress-done, .task[data-key=a] rect.progress'))

  await picker.locator('[data-value=fill]').click()
  await bar.locator('.progress-done.is-shade').waitFor()
  await page.keyboard.press('Escape')
  // Настройка сохранена в плане и видна всем
  const plan = await api(page, 'GET', `/plans/${stage2}`)
  assert.equal(plan.body.plan.settings.display.progressStyle, 'fill')
  assert.equal(plan.body.plan.settings.display.showProgress, true)
  assert.deepEqual(page.errors, [])
})
