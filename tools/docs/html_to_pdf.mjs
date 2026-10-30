// HTML → PDF через Chromium (Playwright): A4, колонтитул с номером страницы, закладки.
// playwright — из node_modules или глобальных модулей (NODE_PATH), как в e2e.
// Использование: node html_to_pdf.mjs <in.html> <out.pdf> "<колонтитул>" [<номера-страниц.json>]
// Второй проход получает номера страниц пунктов содержания (их вычисляет build_manuals.py
// по ссылкам первого прохода) и вписывает их в содержание.
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'

const { chromium } = createRequire(import.meta.url)('playwright')
const [input, output, footerText, numbersFile] = process.argv.slice(2)
// COVER_ONLY=1 — только обложка без колонтитула (подставляется в итоговый PDF вместо первой страницы)
const coverOnly = process.env.COVER_ONLY === '1'
const browser = await chromium.launch()
const page = await browser.newPage()
await page.goto(pathToFileURL(input).href, { waitUntil: 'load' })
await page.evaluate(() => document.fonts.ready)
if (numbersFile) {
  const numbers = JSON.parse(readFileSync(numbersFile, 'utf8'))
  await page.evaluate((nums) => {
    document.querySelectorAll('nav.toc a').forEach((a, i) => {
      if (nums[i] == null) return
      const span = document.createElement('span')
      span.className = 'toc-page'
      span.textContent = String(nums[i])
      a.appendChild(span)
    })
  }, numbers)
} else {
  // Резерв места под номер, чтобы второй проход не сдвинул вёрстку
  await page.evaluate(() => document.querySelectorAll('nav.toc a').forEach((a) => {
    const span = document.createElement('span'); span.className = 'toc-page'; span.textContent = '00'; a.appendChild(span)
  }))
}
await page.pdf({
  path: output, format: 'A4', printBackground: true, outline: true, tagged: true, preferCSSPageSize: true,
  displayHeaderFooter: !coverOnly,
  ...(coverOnly ? { pageRanges: '1' } : {}),
  headerTemplate: '<span></span>',
  footerTemplate: `<div style="width:100%;font-size:7.5px;font-family:Inter,Arial,sans-serif;color:#8a92a6;padding:0 17mm;display:flex;justify-content:space-between">
    <span>${footerText}</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>`,
})
await browser.close()
