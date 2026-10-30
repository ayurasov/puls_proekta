import { after, test } from 'node:test'
import assert from 'node:assert/strict'
import { BASE, closeBrowser, login, newPage, PASSWORD, shot } from '../helpers.mjs'

after(closeBrowser)

test('вход: неверный пароль — понятная ошибка, верный — главная с проектами 1.0.1', async () => {
  const page = await newPage()
  await page.goto(BASE + '/')
  await page.waitForURL(/\/login/)
  await page.fill('[data-testid=login]', 'admin')
  await page.fill('[data-testid=password]', 'wrong-password')
  await page.click('[data-testid=login-submit]')
  await page.waitForSelector('[data-testid=login-error]')
  assert.match(await page.textContent('[data-testid=login-error]'), /Неверный логин или пароль/)
  await page.fill('[data-testid=password]', PASSWORD)
  await page.click('[data-testid=login-submit]')
  await page.waitForSelector('.project-card')
  assert.equal(await page.locator('.project-card').count(), 12)
  assert.ok(await page.locator('.direction-header h2', { hasText: 'Проектный офис' }).count())
  await shot(page, '01-dashboard')
  assert.deepEqual(page.errors, [])
})

test('ссылки 1.0.x вида /#/project/N открывают проект', async () => {
  const page = await newPage()
  await login(page)
  await page.goto(BASE + '/#/project/1')
  await page.waitForURL(/\/projects\/1/)
  await page.waitForSelector('.gantt .grid-row')
  assert.ok(await page.locator('.plan-tab').count() >= 2, 'у проекта 1 две вкладки-версии')
})

test('ссылка 1.0.x без входа: после входа открывается проект, а не главная', async () => {
  const page = await newPage()
  await page.goto(BASE + '/#/project/1')
  await page.waitForURL(/\/login/)
  assert.match(decodeURIComponent(new URL(page.url()).search), /next=\/projects\/1/)
  await page.fill('[data-testid=login]', 'admin')
  await page.fill('[data-testid=password]', PASSWORD)
  await page.click('[data-testid=login-submit]')
  await page.waitForURL(/\/projects\/1/)
  await page.waitForSelector('.gantt .grid-row')
})

test('создание проекта открывает его пустой план', async () => {
  const page = await newPage()
  await login(page)
  await page.click('[data-testid=new-project]')
  await page.fill('[data-testid=project-title]', 'E2E Новый проект')
  await page.click('[data-testid=project-save]')
  await page.waitForURL(/\/projects\/\d+/)
  await page.waitForSelector('[data-testid=quick-add]')
  assert.match(await page.textContent('.grid-empty'), /План пуст/)
})
