import { existsSync, readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { escapeHtml, htmlToText, parseAttributes, plainText, renderInline, renderMarkdown, slugify } from '../markdown'

const manual = (name) => readFileSync(new URL(`../../../docs/manuals/${name}`, import.meta.url), 'utf8')

// Всё, что renderMarkdown может вывести: любой другой тег или атрибут — признак дыры в экранировании
const ALLOWED_TAGS = new Set(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'br', 'hr', 'strong', 'em', 'code', 'pre', 'a', 'img',
  'figure', 'figcaption', 'ul', 'ol', 'li', 'blockquote', 'div', 'table', 'thead', 'tbody', 'tr', 'th', 'td'])
const ALLOWED_ATTRS = new Set(['id', 'class', 'href', 'target', 'rel', 'src', 'alt', 'title', 'loading', 'decoding', 'start', 'aria-hidden', 'tabindex'])

function unsafeMarkup(html) {
  const problems = []
  for (const [, tag, attrs] of html.matchAll(/<\/?([a-zA-Z][\w-]*)([^>]*)>/g)) {
    if (!ALLOWED_TAGS.has(tag.toLowerCase())) problems.push(`<${tag}>`)
    for (const [, name] of attrs.matchAll(/\s([^\s=]+)(?:="[^"]*")?/g)) if (!ALLOWED_ATTRS.has(name)) problems.push(`${tag}[${name}]`)
  }
  for (const [, url] of html.matchAll(/(?:href|src)="([^"]*)"/g)) if (/^\s*(javascript|vbscript|data:text)/i.test(url)) problems.push(url)
  return problems
}

describe('markdown: строки', () => {
  it('escapes text and keeps only safe links', () => {
    expect(escapeHtml('<b>&"\'')).toBe('&lt;b&gt;&amp;&quot;&#39;')
    expect(renderInline('**Кнопка** и *курсив*, `<code>`')).toBe('<strong>Кнопка</strong> и <em>курсив</em>, <code>&lt;code&gt;</code>')
    expect(renderInline('[раздел](#вход-в-систему)')).toBe('<a href="#вход-в-систему">раздел</a>')
    expect(renderInline('[сайт](https://example.ru "Пример")')).toBe('<a href="https://example.ru" title="Пример" target="_blank" rel="noopener noreferrer">сайт</a>')
    expect(renderInline('<https://example.ru>')).toContain('href="https://example.ru"')
    expect(renderInline('[опасно](javascript:alert(1))')).toBe('опасно')
    expect(renderInline('[файл](user-guide.docx)')).toBe('файл')
    expect(renderInline('[статья](https://ru.wikipedia.org/wiki/Диаграмма_(Ганта))')).toBe('<a href="https://ru.wikipedia.org/wiki/Диаграмма_(Ганта)" target="_blank" rel="noopener noreferrer">статья</a>')
    expect(renderInline('Добавить на <дату>')).toBe('Добавить на &lt;дату&gt;')
  })

  it('handles escapes, hard breaks, comments and bold across wrapped lines', () => {
    expect(renderInline(String.raw`\*не курсив\* и \[не ссылка\]`)).toBe('*не курсив* и [не ссылка]')
    expect(renderInline('строка  \nновая\\\nещё')).toBe('строка<br>новая<br>ещё')
    expect(renderInline('**Руководство\nпользователя**')).toBe('<strong>Руководство пользователя</strong>')
    expect(renderInline('до <!-- заметка автора --> после')).toBe('до  после')
  })

  it('strips pandoc attributes after images, links, spans and code', () => {
    const resolveImage = (src) => `/assets/${src.split('/').pop()}`
    expect(renderInline('![Телефон](images/user/01.png){width=45%} текст', { resolveImage }))
      .toBe('<img src="/assets/01.png" alt="Телефон" loading="lazy" decoding="async"> текст')
    expect(renderInline('[сайт](https://example.ru){.external target="_blank"}')).not.toContain('{')
    expect(renderInline('[Отменить]{.kbd} и [текст]{#x .smallcaps}')).toBe('Отменить и текст')
    expect(renderInline('`<w:br/>`{=openxml}конец')).toBe('конец')
    expect(renderInline('`ls -la`{.bash}')).toBe('<code>ls -la</code>')
    // Фигурные скобки в обычном тексте и коде — не атрибуты
    expect(renderInline('`(uid={{username}})` и {не атрибут}')).toBe('<code>(uid={{username}})</code> и {не атрибут}')
    expect(parseAttributes('{#id .a .b width="45%" height=3cm -}')).toEqual({ id: 'id', classes: ['a', 'b'], values: { width: '45%', height: '3cm' }, format: '' })
  })

  it('slugifies keeping Cyrillic and gives plain text for headings', () => {
    expect(slugify('«Мои задачи»')).toBe('мои-задачи')
    expect(slugify('Сценарий А1. Новый сотрудник')).toBe('сценарий-а1-новый-сотрудник')
    expect(slugify('Связи и автосдвиг')).toBe('связи-и-автосдвиг')
    expect(slugify('***')).toBe('section')
    expect(plainText('Меню **«Вид»** и `код` [ссылка](#x)')).toBe('Меню «Вид» и код ссылка')
    expect(htmlToText('<p>a &amp; b</p><p>c</p>')).toBe('a & b c')
  })
})

describe('markdown: блоки', () => {
  it('builds headings with unique anchors, tables, lists, quotes and figures', () => {
    const source = [
      '---', 'title: "Пульс Проекта — руководство"', 'subtitle: "Версия 0.2.0"', '---', '',
      '# Вход в систему', 'Откройте **адрес** системы.', 'Вторая строка абзаца.', '',
      '## Вход в систему', '',
      '| Понятие | Что это |', '| --- | :---: |', '| Задача | Единица работы |', '',
      '1. Первый', '2. Второй', '', '- пункт', '- ещё', '',
      '> **Совет.** Полезно.', '',
      '![Экран входа](images/user/01-login.png)',
    ].join('\n')
    const { html, headings, meta, sections } = renderMarkdown(source, { resolveImage: (src) => `/assets/${src.split('/').pop()}` })
    expect(meta).toEqual({ title: 'Пульс Проекта — руководство', subtitle: 'Версия 0.2.0' })
    expect(headings).toEqual([
      { level: 1, text: 'Вход в систему', id: 'вход-в-систему' },
      { level: 2, text: 'Вход в систему', id: 'вход-в-систему-2' },
    ])
    expect(html).toContain('<h1 id="вход-в-систему">Вход в систему</h1>')
    expect(html).toContain('<p>Откройте <strong>адрес</strong> системы. Вторая строка абзаца.</p>')
    expect(html).toContain('<thead><tr><th>Понятие</th><th class="md-align-center">Что это</th></tr></thead><tbody><tr><td>Задача</td><td class="md-align-center">Единица работы</td></tr></tbody>')
    expect(html).toContain('<ol><li>Первый</li><li>Второй</li></ol>')
    expect(html).toContain('<ul><li>пункт</li><li>ещё</li></ul>')
    expect(html).toContain('<blockquote class="md-callout md-callout-tip"><p><strong>Совет.</strong> Полезно.</p></blockquote>')
    expect(html).toContain('<figure><img src="/assets/01-login.png" alt="Экран входа" loading="lazy" decoding="async"><figcaption>Экран входа</figcaption></figure>')
    expect(sections['вход-в-систему']).toBe('Откройте адрес системы. Вторая строка абзаца.')
    expect(sections['вход-в-систему-2']).toContain('Единица работы')
  })

  it('uses pandoc heading ids, shifts heading levels and adds anchors', () => {
    const { html, headings } = renderMarkdown('# Обзор {#overview .unnumbered}\n\n## Обзор {-}\n\n### Ещё ##', { headingOffset: 1, anchors: true })
    expect(headings.map((h) => h.id)).toEqual(['overview', 'обзор', 'ещё'])
    expect(headings.map((h) => h.text)).toEqual(['Обзор', 'Обзор', 'Ещё'])
    expect(html).toContain('<h2 id="overview">Обзор<a class="md-anchor" href="#overview" aria-hidden="true" tabindex="-1">#</a></h2>')
    expect(html).toContain('<h4 id="ещё">Ещё')
    expect(html).not.toContain('{')
  })

  it('strips figure attributes and drops unresolved figures', () => {
    const { html } = renderMarkdown('![Карточка](images/user/05.png){width=45%}\n\n![Нет](images/user/missing.png)', {
      resolveImage: (src) => (src.includes('missing') ? null : `/assets/${src.split('/').pop()}`),
    })
    expect(html).toBe('<figure><img src="/assets/05.png" alt="Карточка" loading="lazy" decoding="async"><figcaption>Карточка</figcaption></figure>')
  })

  it('renders nested, continued, loose and numbered lists', () => {
    const tight = renderMarkdown([
      '- **Боковое меню** слева: разделы', '  администрирования.', '  - вложенный', '  - ещё вложенный', '- второй',
      'ленивое продолжение', '', '3. третий', '4. четвёртый',
    ].join('\n')).html
    expect(tight).toBe('<ul><li><strong>Боковое меню</strong> слева: разделы администрирования.<ul><li>вложенный</li><li>ещё вложенный</li></ul></li>'
      + '<li>второй ленивое продолжение</li></ul>\n<ol start="3"><li>третий</li><li>четвёртый</li></ol>')
    const loose = renderMarkdown('1. Первый\n\n   Пояснение.\n\n2. Второй').html
    expect(loose).toBe('<ol><li><p>Первый</p><p>Пояснение.</p></li><li><p>Второй</p></li></ol>')
    // Нумерованный не с 1 не прерывает абзац: «в 2026\n2. …» — просто текст
    expect(renderMarkdown('Сдано в 2026\n2. квартале').html).toBe('<p>Сдано в 2026 2. квартале</p>')
  })

  it('renders code blocks and skips pandoc-only markup', () => {
    const { html } = renderMarkdown([
      'Текст', '', '```bash', 'echo "<hello>" && ls', '```', '',
      '```{=openxml}', '<w:p><w:r><w:br w:type="page"/></w:r></w:p>', '```', '',
      '\\newpage', '', '::: {custom-style="Note"}', 'Внутри блока', ':::', '',
      '<!-- черновик', 'не показывать -->', '', '***', 'Конец',
    ].join('\n'))
    expect(html).toBe('<p>Текст</p>\n<pre><code class="language-bash">echo &quot;&lt;hello&gt;&quot; &amp;&amp; ls</code></pre>\n<p>Внутри блока</p>\n<hr>\n<p>Конец</p>')
  })

  it('never emits markup from hostile input', () => {
    const hostile = [
      '<script>alert(1)</script>', '<img src=x onerror=alert(1)>', '[x](javascript:alert(1))', '[x](JaVaScRiPt:alert(1))',
      '![x](javascript:alert(1))', '![x"><svg onload=alert(1)>](images/user/a.png)', '[a](#" onclick="alert(1))',
      '<a href="javascript:alert(1)">a</a>', '# <iframe src=x>', '| <b>a</b> | b |', '| --- | --- |', '| c | <style>x</style> |',
      '> <object data=x>', '- <embed src=x>', '`</code><script>`', '<javascript:alert(1)>', '[x](data:text/html,<script>alert(1)</script>)',
    ].join('\n\n')
    const { html } = renderMarkdown(hostile, { resolveImage: (src) => (src.startsWith('images/') ? `/assets/${src}` : null) })
    expect(unsafeMarkup(html)).toEqual([])
    expect(html).not.toMatch(/<(?:script|iframe|svg|object|embed|style)\b|\son\w+="/i)
    expect(html).toContain('<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>')
    // Без resolveImage рисунок — только по относительному пути
    expect(renderInline('![x](javascript:alert(1)) ![y](//evil.example/a.png) ![z](images/a.png)'))
      .toBe('x y <img src="images/a.png" alt="z" loading="lazy" decoding="async">')
  })
})

describe('markdown: руководства из docs/manuals', () => {
  for (const [file, imageDir, title] of [
    ['user-guide.md', 'images/user/', 'руководство пользователя'],
    ['admin-guide.md', 'images/admin/', 'руководство администратора'],
  ]) {
    it(`renders ${file} with unique anchors and every screenshot resolved`, () => {
      const requested = []
      const { html, headings, meta, sections } = renderMarkdown(manual(file), {
        headingOffset: 1, anchors: true,
        resolveImage: (src) => { requested.push(src); return `/assets/${src.split('/').pop()}` },
      })
      expect(meta.title).toContain(title)
      expect(meta.subtitle).toMatch(/^Версия \d+\.\d+\.\d+$/)
      expect(headings.filter((h) => h.level === 1).length).toBeGreaterThanOrEqual(10)
      const ids = headings.map((h) => h.id)
      expect(new Set(ids).size).toBe(ids.length)
      expect(ids.every((id) => /^[\p{Ll}\p{N}-]+$/u.test(id))).toBe(true)
      expect(Object.keys(sections)).toEqual(ids)

      expect(requested.length).toBeGreaterThan(5)
      expect(requested.filter((src) => !src.startsWith(imageDir))).toEqual([])
      expect(requested.filter((src) => !existsSync(new URL(`../../../docs/manuals/${src}`, import.meta.url)))).toEqual([])
      expect(html.match(/<figure>/g)).toHaveLength(requested.length)

      expect(unsafeMarkup(html)).toEqual([])
      expect(html).not.toMatch(/<script|javascript:/i)
      const text = htmlToText(html)
      expect(text).not.toMatch(/\{(?:width|height)=|\{#|\{\.|\{=|^:::|\\newpage|!\[|\]\(/m)
      expect(text).not.toMatch(/\*\*/)
    })
  }
})
