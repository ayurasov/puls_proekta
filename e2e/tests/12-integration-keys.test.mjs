import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import { api, BASE, closeBrowser, login, newPage, shot } from '../helpers.mjs'

/**
 * 0.6.0: «Интеграции → Ключи доступа» — у каждой системы свой ключ и права (ADR-0011): ключ «По Делу» не
 * затрагивается, ключ «Дашбордов компании» получает показатели проектов, перевыпуск, права, отзыв, журнал.
 */
let page
let dashboardsKey

before(async () => {
  page = await newPage({ width: 1440, height: 950 })
  await login(page)
})
after(async () => {
  for (const key of (await api(page, 'GET', '/settings/integration-keys')).body.keys.filter((k) => k.active)) {
    await api(page, 'DELETE', `/settings/integration-keys/${key.id}`)
  }
  await closeBrowser()
})

function call(path, key) {
  return fetch(`${BASE}/api/integration/v1${path}`, { headers: { Authorization: `Bearer ${key}` } })
}
async function issuedKey() {
  const code = page.locator('[data-testid=key-issued] code')
  await code.waitFor()
  const key = (await code.textContent()).trim()
  await page.locator('.modal-footer .btn-primary', { hasText: 'Готово' }).click()
  return key
}

test('ключ «По Делу» из своей страницы виден среди ключей и продолжает работать', async () => {
  await page.goto(`${BASE}/admin/integrations/podelu`)
  await page.click('[data-testid=podelu-links-issue]')
  const podeluKey = (await page.locator('[data-testid=podelu-links-issued] code').textContent()).trim()
  await page.click('[data-testid=nav-integration-keys]')
  await page.locator('[data-testid=key-row]', { hasText: 'По Делу' }).locator('text=Проекты и планы').waitFor()
  const res = await call('/projects', podeluKey)
  assert.equal(res.status, 200)
  const project = (await res.json()).projects[0]
  assert.equal(project.inwork, undefined)  // «По Делу» получает прежние поля
  assert.equal(typeof project.direction === 'string' || project.direction === null, true)
})

test('ключ «Дашбордов» получает показатели проектов и направления', async () => {
  await page.click('[data-testid=key-issue-open]')
  // «Дашборды компании» предложены сами: название и права «Проекты и планы» + «Показатели проектов»
  assert.equal(await page.inputValue('[data-testid=key-name]'), 'Дашборды компании')
  assert.equal(await page.isChecked('[data-testid="key-scope-projects.stats"]'), true)
  await shot(page, '12-key-issue')
  await page.click('[data-testid=key-save]')
  dashboardsKey = await issuedKey()
  assert.match(dashboardsKey, /^puls_[\w-]{40,}$/)
  const projects = (await (await call('/projects', dashboardsKey)).json()).projects
  const isup = projects.find((p) => p.title === 'ИСУП МО - реализация договоров')
  for (const field of ['progress', 'overdue', 'blocked', 'inwork', 'nextMilestone', 'directionInfo', 'updatedAt', 'planWindow']) {
    assert.ok(field in isup, field)
  }
  assert.equal(typeof isup.inwork, 'number')
  const directions = (await (await call('/directions', dashboardsKey)).json()).directions
  assert.ok(directions.length > 0 && directions.every((d) => typeof d.projects === 'number'))
  await page.locator('[data-testid=key-row]', { hasText: 'Дашборды компании' }).locator('text=Показатели проектов').waitFor()
  await shot(page, '12-keys-list')
})

test('перевыпуск, права и отзыв — с журналом', async () => {
  const row = page.locator('[data-testid=key-row]', { hasText: 'Дашборды компании' })
  await row.locator('[data-testid=key-reissue]').click()
  // Перевыпуск отключает систему до ввода нового ключа — кнопка подтверждения «опасная», фокус на «Отмене»
  await page.locator('.modal .btn-danger', { hasText: 'Перевыпустить' }).click()
  const fresh = await issuedKey()
  assert.equal((await call('/ping', dashboardsKey)).status, 401)
  assert.equal((await call('/ping', fresh)).status, 200)

  await row.locator('[data-testid=key-edit]').click()
  await page.uncheck('[data-testid="key-scope-projects.stats"]')
  await page.click('[data-testid=key-save]')
  await page.locator('.toast', { hasText: 'Ключ изменён' }).waitFor()
  const project = (await (await call('/projects', fresh)).json()).projects[0]
  assert.equal(project.inwork, undefined)

  await row.locator('[data-testid=key-revoke]').click()
  await page.locator('.modal .btn-danger', { hasText: 'Отозвать' }).click()
  await page.locator('.toast', { hasText: 'Ключ отозван' }).waitFor()
  assert.equal((await call('/projects', fresh)).status, 401)
  await page.click('[data-testid=keys-revoked-toggle]')
  await page.locator('.is-revoked', { hasText: 'Дашборды компании' }).waitFor()
  const journal = page.locator('[data-testid=keys-journal]')
  for (const text of ['перевыпущен', 'права:', 'отозван']) await journal.locator(`text=${text}`).first().waitFor()
  assert.deepEqual(page.errors.filter((e) => !/status of 40[13]/.test(e)), [])
})
