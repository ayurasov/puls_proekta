import { after, test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { BASE, closeBrowser, login, newPage, openProject, shot } from '../helpers.mjs'

/**
 * Руководства внутри приложения: пункт «Руководство» в боковом меню, «О программе», оглавление,
 * поиск, ссылки на разделы, скачивание PDF и DOCX, руководство администратора только
 * администратору, контекстная справка «?». Страница руководства открывается с той же CSP,
 * что отдаёт nginx в Docker, — нарушения политики считаются ошибками.
 */
after(closeBrowser)

const CSP = /add_header Content-Security-Policy "([^"]+)"/.exec(readFileSync(new URL('../../nginx.conf', import.meta.url), 'utf8'))[1]

async function withProductionCsp(page) {
  await page.route('**/*', async (route) => {
    if (route.request().resourceType() !== 'document') return route.continue()
    const response = await route.fetch()
    await route.fulfill({ response, headers: { ...response.headers(), 'content-security-policy': CSP } })
  })
  await page.addInitScript(() => {
    window.cspViolations = []
    document.addEventListener('securitypolicyviolation', (event) => window.cspViolations.push(`${event.violatedDirective} ${event.blockedURI}`))
  })
}

const hashOf = (page) => decodeURIComponent(new URL(page.url()).hash)

async function waitGuide(page) {
  await page.waitForSelector('[data-testid=guide-content] h2')
}

/** Заголовок раздела у верхнего края экрана (после перехода по оглавлению или ссылке). */
async function waitHeadingAtTop(page, id) {
  await page.waitForFunction((target) => {
    const heading = document.querySelector(`[data-testid=guide-content] [id="${target}"]`)
    if (!heading) return false
    const { top } = heading.getBoundingClientRect()
    return top >= -2 && top < window.innerHeight / 3
  }, id, { timeout: 10000 })
}

async function openHelp(page, selector) {
  const [popup] = await Promise.all([page.context().waitForEvent('page'), page.click(selector)])
  popup.errors = []
  popup.on('pageerror', (e) => popup.errors.push(e.message))
  await popup.waitForLoadState()
  await waitGuide(popup)
  return popup
}

test('руководство из бокового меню: оглавление, поиск, переход к разделу, скачивание PDF и DOCX', async () => {
  const page = await newPage()
  await withProductionCsp(page)
  await login(page, 'e2e.editor')
  await page.click('[data-testid=guide-link]')
  await page.waitForURL(/\/guide$/)
  await waitGuide(page)
  assert.equal(await page.textContent('h1.page-title'), 'Руководство пользователя')
  assert.equal(await page.locator('[data-testid=guide-switch-admin]').count(), 0, 'руководство администратора скрыто')

  // Все снимки экрана — файлы сборки
  const images = await page.locator('[data-testid=guide-content] figure img').evaluateAll((list) => list.map((img) => img.getAttribute('src')))
  assert.ok(images.length >= 20, `рисунков: ${images.length}`)
  assert.deepEqual(images.filter((src) => !/^(\/assets\/|data:image\/)/.test(src)), [])

  // Поиск — по тексту разделов, не только по заголовкам
  await page.fill('[data-testid=guide-search]', 'автосдвиг')
  const found = await page.locator('.guide-toc .toc-text').allTextContents()
  assert.ok(found.includes('Связи и автосдвиг'), found.join(', '))
  assert.ok(found.includes('Настройки графика'), 'найдено в тексте раздела')
  assert.ok(!found.includes('Вход в систему'))
  await shot(page, '06-guide-search')
  await page.fill('[data-testid=guide-search]', 'несуществующееслово')
  await page.waitForSelector('.guide-toc-caption:has-text("Ничего не найдено")')
  await page.fill('[data-testid=guide-search]', '')

  // Переход по оглавлению: адрес с #разделом, заголовок наверху, пункт подсвечен
  await page.click('.guide-toc a:text-is("Статистика")')
  await page.waitForFunction(() => decodeURIComponent(location.hash) === '#статистика')
  await waitHeadingAtTop(page, 'статистика')
  assert.equal(await page.getAttribute('.guide-toc li.active a', 'href'), '#статистика')
  // Снимок раздела загрузился (ленивая загрузка по мере прокрутки)
  await page.waitForFunction(() => {
    const img = document.querySelector('[id="статистика"] ~ figure img')
    return img && img.complete && img.naturalWidth > 0
  })
  await shot(page, '06-guide-section')

  // Увеличение снимка экрана
  await page.click('[id="статистика"] ~ figure img')
  await page.waitForSelector('.guide-zoom img')
  await page.keyboard.press('Escape')
  await page.waitForSelector('.guide-zoom', { state: 'detached' })

  // PDF и DOCX к установленной версии, имена латиницей
  const [pdf] = await Promise.all([page.waitForEvent('download'), page.click('[data-testid=guide-download-pdf]')])
  assert.match(pdf.suggestedFilename(), /^puls-\d+\.\d+\.\d+-rukovodstvo-polzovatelya\.pdf$/)
  assert.equal(readFileSync(await pdf.path()).subarray(0, 5).toString(), '%PDF-')
  const [docx] = await Promise.all([page.waitForEvent('download'), page.click('[data-testid=guide-download-docx]')])
  assert.match(docx.suggestedFilename(), /^puls-\d+\.\d+\.\d+-rukovodstvo-polzovatelya\.docx$/)
  assert.equal(await page.getAttribute('[data-testid=guide-open-pdf]', 'target'), '_blank')
  const opened = await page.request.get(BASE + await page.getAttribute('[data-testid=guide-open-pdf]', 'href'))
  assert.equal(opened.status(), 200)

  assert.deepEqual(await page.evaluate(() => window.cspViolations), [])
  assert.deepEqual(page.errors, [])
})

test('ссылка на раздел открывает его сразу; руководство администратора — не для всех', async () => {
  const page = await newPage()
  await login(page, 'e2e.viewer')
  await page.goto(`${BASE}/guide#${encodeURIComponent('экспорт')}`)
  await waitGuide(page)
  await waitHeadingAtTop(page, 'экспорт')
  await page.goto(BASE + '/guide/admin')
  await page.waitForURL(/\/guide$/)
  await waitGuide(page)
  assert.equal(await page.textContent('h1.page-title'), 'Руководство пользователя')
  await page.goto(BASE + '/about')
  assert.equal(await page.locator('[data-testid=about-guide-admin]').count(), 0)
  const pdfName = await page.getAttribute('[data-testid=about-guide-user] a[download$=".pdf"]', 'download')
  assert.match(pdfName, /^puls-\d+\.\d+\.\d+-rukovodstvo-polzovatelya\.pdf$/)
  await page.click('[data-testid=about-guide-user] a:has-text("Читать в приложении")')
  await page.waitForURL(/\/guide$/)
  await waitGuide(page)
  assert.deepEqual(page.errors, [])
})

test('узкий экран: раздел выбирается из списка', async () => {
  const page = await newPage({ width: 820, height: 900 })
  await login(page, 'e2e.viewer')
  await page.goto(BASE + '/guide')
  await waitGuide(page)
  assert.equal(await page.isVisible('.guide-toc'), false)
  await page.selectOption('.guide-toc-mobile', { label: 'Экспорт' })
  await page.waitForFunction(() => decodeURIComponent(location.hash) === '#экспорт')
  await waitHeadingAtTop(page, 'экспорт')
  await shot(page, '06-guide-mobile')
})

test('контекстная справка «?» на панели плана открывает раздел в новой вкладке', async () => {
  const page = await newPage()
  await login(page, 'e2e.editor')
  await openProject(page, 'ИСУП МО - реализация договоров')
  const help = await openHelp(page, '.toolbar [data-testid=help-link]')
  assert.match(new URL(help.url()).pathname, /^\/guide$/)
  assert.equal(hashOf(help), '#экран-плана')
  await waitHeadingAtTop(help, 'экран-плана')
  assert.deepEqual(help.errors, [])
  // План остался открытым в своей вкладке
  assert.match(page.url(), /\/projects\/\d+/)
})

test('администратор: руководство администратора, переключение и справка в разделах администрирования', async () => {
  const page = await newPage()
  await withProductionCsp(page)
  await login(page)
  await page.goto(BASE + '/about')
  await page.click('[data-testid=about-guide-admin] a:has-text("Читать в приложении")')
  await page.waitForURL(/\/guide\/admin$/)
  await waitGuide(page)
  assert.equal(await page.textContent('h1.page-title'), 'Руководство администратора')
  const [pdf] = await Promise.all([page.waitForEvent('download'), page.click('[data-testid=guide-download-pdf]')])
  assert.match(pdf.suggestedFilename(), /^puls-\d+\.\d+\.\d+-rukovodstvo-administratora\.pdf$/)
  await page.click('.guide-toc a:text-is("Вход через домен")')
  await waitHeadingAtTop(page, 'вход-через-домен')
  await page.click('[data-testid=guide-switch-user]')
  await page.waitForURL(/\/guide$/)
  await waitGuide(page)
  assert.equal(await page.textContent('h1.page-title'), 'Руководство пользователя')
  assert.deepEqual(await page.evaluate(() => window.cspViolations), [])

  await page.goto(BASE + '/admin/users')
  const help = await openHelp(page, '.page-header [data-testid=help-link]')
  assert.match(new URL(help.url()).pathname, /^\/guide\/admin$/)
  assert.equal(hashOf(help), '#сотрудники')
  await waitHeadingAtTop(help, 'сотрудники')
  await shot(help, '06-guide-admin-help')

  // Справка следует за вкладкой раздела
  await page.goto(BASE + '/admin/settings')
  await page.click('.segmented button:has-text("Производственный календарь")')
  assert.equal(await page.getAttribute('.page-header [data-testid=help-link]', 'data-help-topic'), 'admin-calendar')
  assert.deepEqual(page.errors, [])
})
