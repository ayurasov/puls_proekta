import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import { api, BASE, closeBrowser, login, newPage, shot } from '../helpers.mjs'

/**
 * 0.7.2: «Состояние системы» (ADR-0014) — администратору всегда; полномочия без роли администратора (ADR-0013) —
 * администратор выдаёт их сотруднику, тот видит в меню только свои разделы и не меняет учётные записи администраторов.
 */
let admin
let viewerId

before(async () => {
  admin = await newPage({ width: 1440, height: 950 })
  await login(admin)
  viewerId = (await api(admin, 'GET', '/users')).body.find((u) => u.login === 'e2e.viewer').id
})
after(async () => {
  await api(admin, 'PATCH', `/users/${viewerId}`, { capabilities: [] })
  await closeBrowser()
})

test('администратор видит состояние системы: сводка, карточки, проверка подключений', async () => {
  await admin.goto(`${BASE}/admin/health`)
  await admin.locator('[data-testid=health-summary]').waitFor()
  assert.equal(await admin.locator('.component').count(), 10)
  for (const key of ['app', 'backups', 'errors', 'jira', 'podelu', 'ldap', 'keys', 'calendar', 'users', 'data']) {
    await admin.locator(`[data-testid=health-${key}]`).waitFor()
  }
  assert.match(await admin.locator('[data-testid=health-app]').textContent(), /Схема базы: 0008_user_capabilities/)
  await shot(admin, '15-health')
  await admin.click('[data-testid=health-check]')
  await admin.locator('.toast').first().waitFor()
  assert.deepEqual(admin.errors.filter((e) => !/status of 40[13]|502/.test(e)), [])
})

test('администратор выдаёт полномочия: список по группам, отметка в таблице, реестр', async () => {
  await admin.goto(`${BASE}/admin/users`)
  const row = admin.locator('tr', { hasText: '@e2e.viewer' })
  await row.locator('button[title="Изменить"]').click()
  const list = admin.locator('[data-testid=user-capability-list]')
  await list.waitFor()
  assert.deepEqual(await list.locator('.caps-group-name').allTextContents(), ['Система', 'Люди и доступы', 'Проекты'])
  await admin.check('[data-cap="system.health"]')
  await admin.check('[data-cap="users.manage"]')
  await list.scrollIntoViewIfNeeded()
  await shot(admin, '15-user-capabilities')
  await admin.click('.modal-footer .btn-primary')
  await row.locator('[data-testid=user-capabilities]', { hasText: '2 полномочия' }).waitFor()
  const user = (await api(admin, 'GET', '/users')).body.find((u) => u.id === viewerId)
  assert.deepEqual(user.capabilities, ['system.health', 'users.manage'])
  const audit = (await api(admin, 'GET', `/audit?q=${encodeURIComponent('Смотрит состояние системы')}`)).body
  assert.ok(JSON.stringify(audit).includes('Смотрит состояние системы'))
})

test('сотрудник с полномочиями видит только свои разделы и не меняет администратора', async () => {
  const viewer = await newPage({ width: 1440, height: 950 })
  await login(viewer, 'e2e.viewer')
  const nav = viewer.locator('[data-testid=sidebar-admin] .nav-item')
  await nav.first().waitFor()
  assert.deepEqual((await nav.allTextContents()).map((t) => t.trim()), ['Состояние системы', 'Сотрудники', 'Импорт и экспорт'])
  // В импорте и экспорте — только сотрудники: структура и синхронизации требуют «Ведёт структуру компании»
  await viewer.goto(`${BASE}/admin/org-transfer`)
  await viewer.locator('[data-testid=transfer-tab-employees]').waitFor()
  assert.equal(await viewer.locator('[data-testid^=transfer-tab-]').count(), 1)
  assert.equal(await viewer.locator('[data-testid=sidebar-integrations]').count(), 0)
  // /admin — первый доступный раздел; чужой раздел — на главную
  await viewer.goto(`${BASE}/admin`)
  await viewer.waitForURL(/\/admin\/health$/)
  await viewer.locator('[data-testid=health-summary]').waitFor()
  await viewer.goto(`${BASE}/admin/access`)
  await viewer.waitForURL((url) => url.pathname === '/')
  // Учётная запись администратора защищена: роль и включение не меняются, в карточке — пояснение
  await viewer.goto(`${BASE}/admin/users`)
  const adminRow = viewer.locator('tr', { hasText: '@admin' })
  await adminRow.waitFor()
  assert.equal(await adminRow.locator('select').isDisabled(), true)
  await adminRow.locator('button[title="Изменить"]').click()
  await viewer.locator('[data-testid=user-locked]', { hasText: 'только администратор' }).waitFor()
  assert.equal(await viewer.locator('[data-testid=user-capability-list]').count(), 0)
  await viewer.keyboard.press('Escape')
  // Роль «Администратор» другим не назначается
  const other = viewer.locator('tr', { hasText: '@e2e.editor' })
  assert.equal(await other.locator('option[value=admin]').isDisabled(), true)
  // Профиль показывает свои полномочия
  await viewer.click('.user-card')
  await viewer.locator('[data-testid=profile-capabilities]', { hasText: 'Ведёт учётные записи' }).waitFor()
  await shot(viewer, '15-profile-capabilities')
  assert.deepEqual(viewer.errors.filter((e) => !/status of 40[13]/.test(e)), [])
})
