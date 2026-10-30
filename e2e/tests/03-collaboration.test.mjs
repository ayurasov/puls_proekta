import { after, test } from 'node:test'
import assert from 'node:assert/strict'
import { api, closeBrowser, login, newPage, openProject, shot, waitSaved } from '../helpers.mjs'

after(closeBrowser)

test('правки коллеги появляются без перезагрузки, присутствие видно', async () => {
  const admin = await newPage()
  const editor = await newPage()
  await login(admin)
  await login(editor, 'e2e.editor')
  await openProject(admin, 'ИСУП МО - реализация договоров')
  await openProject(editor, 'ИСУП МО - реализация договоров')
  await editor.fill('[data-testid=quick-add]', 'Задача от коллеги')
  await editor.press('[data-testid=quick-add]', 'Enter')
  await waitSaved(editor)
  await admin.locator('.grid-row .name', { hasText: 'Задача от коллеги' }).waitFor({ timeout: 15000 })
  await admin.waitForSelector('.presence .presence-avatar', { timeout: 15000 })
  await shot(admin, '03-presence')
})

test('одновременная правка разных полей одной строки — сохраняются обе', async () => {
  const a = await newPage()
  const b = await newPage()
  await login(a)
  await login(b, 'e2e.editor')
  await openProject(a, 'ИСУП МО - реализация договоров')
  await openProject(b, 'ИСУП МО - реализация договоров')
  const plans = await api(a, 'GET', '/projects')
  const project = plans.body.find((p) => p.title === 'ИСУП МО - реализация договоров')
  const planId = project.plans[0].id
  const doc = await api(a, 'GET', `/plans/${planId}`)
  const task = doc.body.items.find((i) => i.name === 'Задача от коллеги')
  const base = doc.body.plan.revision
  const r1 = await api(a, 'POST', `/plans/${planId}/ops`, { baseRevision: base, ops: [{ op: 'item.update', key: task.key, fields: { progress: 60 } }] })
  const r2 = await api(b, 'POST', `/plans/${planId}/ops`, { baseRevision: base, ops: [{ op: 'item.update', key: task.key, fields: { category: 'тестирование' } }] })
  assert.equal(r1.status, 200)
  assert.equal(r2.status, 200)
  assert.equal(r2.body.conflicts.length, 1)
  const final = (await api(a, 'GET', `/plans/${planId}`)).body.items.find((i) => i.key === task.key)
  assert.equal(final.progress, 60)
  assert.equal(final.category, 'тестирование')
})

test('наблюдатель видит план только для чтения', async () => {
  const viewer = await newPage()
  await login(viewer, 'e2e.viewer')
  await openProject(viewer, 'ИСУП МО - реализация договоров')
  assert.match(await viewer.textContent('.banner-muted'), /только для просмотра/)
  assert.equal(await viewer.locator('[data-testid=add-task]').count(), 0)
  assert.equal(await viewer.locator('[data-testid=quick-add]').count(), 0)
  const res = await api(viewer, 'POST', '/projects', { title: 'нельзя' })
  assert.equal(res.status, 403)
})
