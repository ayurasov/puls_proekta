import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { api, BASE, closeBrowser, login, newPage, shot } from '../helpers.mjs'

/**
 * MS Project (ADR-0012): проект из файла MS Project XML как мастер-копия, номера строк из файла, обновление плана
 * из изменённого файла с сохранением комментариев «Пульса» и выгрузка обратно в MS Project XML.
 */
const FILE = fileURLToPath(new URL('../../backend/tests/fixtures/msproject/vnedrenie_crm.xml', import.meta.url))
const xml = readFileSync(FILE, 'utf-8')
let page
let projectId
let planId

before(async () => {
  page = await newPage({ width: 1500, height: 950 })
  await login(page)
})
after(closeBrowser)

const asFile = (text, name = 'Внедрение CRM.xml') => ({ name, mimeType: 'application/xml', buffer: Buffer.from(text, 'utf-8') })

test('проект из MS Project — мастер-копия: план загрузки, создание, значок и номера строк из файла', async () => {
  await page.goto(`${BASE}/`)
  await page.click('[data-testid=new-project-msp]')
  await page.setInputFiles('[data-testid=msp-file]', asFile(xml))
  const preview = page.locator('[data-testid=msp-preview]')
  await preview.waitFor()
  assert.match(await preview.textContent(), /Задач \d+/)
  assert.ok(await page.locator('[data-testid=msp-warnings] li').count() > 0)
  await page.locator('[data-testid=msp-mode] button', { hasText: 'Мастер-копия' }).click()
  await page.waitForFunction(() => !document.querySelector('[data-testid=msp-apply]')?.disabled)
  await shot(page, '14-msproject-preview')
  await page.click('[data-testid=msp-apply]')
  await page.waitForURL(/\/projects\/\d+/)
  projectId = Number(/projects\/(\d+)/.exec(page.url())[1])
  const project = (await api(page, 'GET', `/projects/${projectId}`)).body
  planId = project.plans[0].id
  const plan = (await api(page, 'GET', `/plans/${planId}`)).body
  assert.equal(plan.plan.settings.source.master, true)
  assert.ok(plan.items.length > 20 && plan.links.length > 5)
  await page.locator('[data-testid=msp-badge]').waitFor()

  // Номера строк — коды WBS из файла
  await page.locator('.toolbar button[title="Вид"], .toolbar button:has-text("Вид")').first().click()
  await page.locator('.menu-item', { hasText: 'Номер (1.2.3)' }).click()
  await page.keyboard.press('Escape')
  const first = page.locator('[data-testid=row-number]').first()
  await first.waitFor()
  assert.match(await first.textContent(), /^\d+(\.\d+)*$/)
  assert.deepEqual(page.errors, [])
})

test('обновление из MS Project: переименование и срок из файла, комментарий «Пульса» сохраняется', async () => {
  const plan = (await api(page, 'GET', `/plans/${planId}`)).body
  const task = plan.items.find((i) => i.name === 'Разработка устава проекта')
  await api(page, 'POST', `/plans/${planId}/items/${task.key}/comments`, { text: 'Согласовано с заказчиком' })
  const changed = xml.replace('<Name>Разработка устава проекта</Name>', '<Name>Устав проекта</Name>')

  await page.locator('.plan-tab.is-active .tab-menu').click()
  await page.click('[data-testid=plan-msp-update]')
  await page.setInputFiles('[data-testid=msp-file]', asFile(changed))
  const changes = page.locator('[data-testid=msp-changes]')
  await changes.waitFor()
  assert.match(await changes.textContent(), /Изменено 1/)
  assert.match(await changes.textContent(), /Устав проекта/)
  await page.waitForFunction(() => !document.querySelector('[data-testid=msp-apply]')?.disabled)
  await page.click('[data-testid=msp-apply]')
  await page.locator('.toast', { hasText: 'План обновлён из MS Project' }).waitFor()
  await page.locator('.grid-row .name', { hasText: 'Устав проекта' }).waitFor()
  const after = (await api(page, 'GET', `/plans/${planId}`)).body
  const renamed = after.items.find((i) => i.key === task.key)
  assert.equal(renamed.name, 'Устав проекта')
  assert.equal(renamed.commentCount, task.commentCount + 1)
  assert.deepEqual(page.errors, [])
})

test('выгрузка в MS Project XML', async () => {
  await page.click('button[title="Экспорт"]')
  const [download] = await Promise.all([page.waitForEvent('download'), page.click('[data-testid=export-msproject]')])
  // Имя файла — название проекта и плана (кириллица); безголовый Chromium песочницы заменяет такие имена на «download»
  const content = readFileSync(await download.path(), 'utf-8')
  assert.match(content, /<Project xmlns="http:\/\/schemas\.microsoft\.com\/project">/)
  assert.match(content, /<Name>Устав проекта<\/Name>/)
  assert.match(content, /<PredecessorLink>/)
  assert.deepEqual(page.errors, [])
})
