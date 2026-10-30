import { after, test } from 'node:test'
import assert from 'node:assert/strict'
import { api, BASE, closeBrowser, login, newPage, shot } from '../helpers.mjs'

after(closeBrowser)

test('администратор: новый сотрудник, выдача доступа, отключение с подтверждением', async () => {
  const page = await newPage()
  await login(page)
  await page.goto(BASE + '/admin/users')
  await page.click('button:has-text("Сотрудник")')
  const modal = page.locator('.modal')
  await modal.locator('label', { hasText: 'ФИО' }).locator('input').fill('Тестов Тест Тестович')
  await modal.locator('label', { hasText: 'Логин' }).locator('input').fill('testov.tt')
  await modal.locator('label', { hasText: 'Пароль' }).locator('input').fill('password-12345')
  await modal.locator('.btn-primary').click()
  await page.locator('td', { hasText: 'Тестов Тест Тестович' }).waitFor()

  await page.goto(BASE + '/admin/access')
  await page.locator('.option', { hasText: 'Тестов Тест Тестович' }).locator('input').check()
  await page.locator('.option', { hasText: 'АльтерОС' }).first().locator('input').check()
  await shot(page, '05-access-before-grant')
  await page.locator('button.btn-primary', { hasText: 'Выдать доступ' }).click()
  await page.locator('.toast', { hasText: 'Доступы выданы' }).waitFor()
  const rules = await api(page, 'GET', '/access')
  assert.ok(rules.body.some((r) => r.userLogin === 'testov.tt' && r.targetName === 'АльтерОС'))

  await page.goto(BASE + '/admin/users')
  const row = page.locator('tr', { hasText: 'Тестов Тест Тестович' })
  await row.locator('.switch').click()
  await page.waitForSelector('.modal')
  assert.match(await page.textContent('.modal'), /сразу выйдет из системы/)
  await page.click('.modal .btn-danger')
  await page.waitForTimeout(500)
  const users = await api(page, 'GET', '/users')
  assert.equal(users.body.find((u) => u.login === 'testov.tt').isActive, false)
  await shot(page, '05-users')
})

test('реестр изменений перенесён и фильтруется', async () => {
  const page = await newPage()
  await login(page)
  await page.goto(BASE + '/admin/audit')
  await page.waitForFunction(() => /^[1-9]/.test(document.querySelector('.page-subtitle')?.textContent || ''))
  const subtitle = await page.textContent('.page-subtitle')
  const total = Number(/(\d+)/.exec(subtitle)[1])
  assert.ok(total >= 4098, `в реестре ${total} записей`)
  await page.selectOption('.filters select >> nth=0', 'karatyshhev.av')
  await page.waitForTimeout(800)
  // Фильтр — по логину, в таблице — ФИО автора; разделы и объекты 1.0.x — понятными словами
  const cells = await page.locator('tbody tr td:nth-child(2)').allTextContents()
  assert.ok(cells.length && cells.every((c) => c.trim() === 'Каратыщев Андрей Викторович'), cells.slice(0, 3).join(', '))
  const sections = await page.locator('tbody tr td:nth-child(4)').allTextContents()
  assert.ok(!sections.some((c) => c.trim() === 'Гант'), 'раздел «Гант» показан как «Планы»')
  const entities = await page.locator('tbody tr td:nth-child(6)').allTextContents()
  assert.ok(!entities.some((c) => /project #|plan #|user #/.test(c)), entities.slice(0, 3).join(', '))
})
