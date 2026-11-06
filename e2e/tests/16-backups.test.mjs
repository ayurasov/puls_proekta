import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import { api, BASE, closeBrowser, login, newPage, shot } from '../helpers.mjs'

/**
 * 0.7.3: «Резервные копии» (ADR-0015) — администратор задаёт расписание и хранение, запрашивает копию; служба копий
 * (контейнер backup) на e2e-стенде не запущена — раздел говорит, как её запустить. Сотрудник с «Смотрит состояние
 * системы» видит раздел без права менять.
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

test('администратор задаёт расписание и хранение и запрашивает копию', async () => {
  await admin.goto(`${BASE}/admin/backups`)
  await admin.locator('[data-testid=backup-service]', { hasText: 'ещё не подключалась' }).waitFor()
  await admin.locator('[data-testid=backup-env-note]').waitFor()
  // По будням в 02:30, хранить 14 дней
  await admin.click('[data-day="6"]')
  await admin.click('[data-day="7"]')
  await admin.fill('[data-testid=backup-time]', '02:30')
  await admin.fill('[data-testid=backup-retention]', '14')
  await admin.click('[data-testid=backup-save]')
  await admin.locator('.toast', { hasText: 'Расписание сохранено' }).waitFor()
  const saved = (await api(admin, 'GET', '/system/backups')).body
  assert.deepEqual(saved.settings, { enabled: true, time: '02:30', days: [1, 2, 3, 4, 5], retentionDays: 14, keepManualDays: 90 })
  assert.equal(await admin.locator('[data-testid=backup-env-note]').count(), 0)
  await admin.click('[data-testid=backup-run]')
  await admin.locator('[data-testid=backup-requested]').waitFor()
  assert.equal(await admin.locator('[data-testid=backup-run]').isDisabled(), true)
  await shot(admin, '16-backups')
  const again = await api(admin, 'POST', '/system/backups/run')
  assert.equal(again.status, 409)
  // В «Состоянии системы» — карточка со ссылкой на раздел
  await admin.goto(`${BASE}/admin/health`)
  await admin.locator('[data-testid=health-backups] a[href="/admin/backups"]').waitFor()
  assert.deepEqual(admin.errors.filter((e) => !/status of 40[13]|409/.test(e)), [])
})

test('«Смотрит состояние системы» видит раздел, но не меняет расписание', async () => {
  await api(admin, 'PATCH', `/users/${viewerId}`, { capabilities: ['system.health'] })
  const viewer = await newPage({ width: 1440, height: 950 })
  await login(viewer, 'e2e.viewer')
  await viewer.locator('[data-testid=nav-admin-backups]').click()
  await viewer.locator('[data-testid=backup-service]').waitFor()
  assert.equal(await viewer.locator('[data-testid=backup-run]').count(), 0)
  assert.equal(await viewer.locator('[data-testid=backup-save]').count(), 0)
  assert.equal(await viewer.locator('[data-testid=backup-time]').isDisabled(), true)
  assert.equal((await api(viewer, 'PUT', '/system/backups/settings', { enabled: false, days: [] })).status, 403)
})
