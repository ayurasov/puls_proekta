/**
 * Небольшой безопасный разборщик Markdown для руководств внутри приложения — тех же
 * docs/manuals/*.md, из которых pandoc собирает DOCX и PDF.
 *
 * Поддерживается то, что нужно руководствам: титульный блок YAML, заголовки, абзацы, списки
 * (в том числе вложенные), таблицы с выравниванием, цитаты-врезки («Совет.», «Важно.»), рисунки,
 * блоки кода, жирный текст, курсив, код, ссылки, экранирование «\*» и жёсткий перенос строки.
 * Разметка pandoc не попадает в текст: атрибуты `{#id .class width=45%}` после заголовков,
 * рисунков, ссылок и фрагментов убираются (`{#id}` заголовка становится его якорем), строки
 * `:::`, `\newpage`, комментарии `<!-- -->` и вставки только для DOCX/PDF (`{=openxml}`) пропускаются.
 *
 * Безопасность: весь текст экранируется, HTML из исходника не вставляется никогда; ссылки — только
 * на якоря документа, http(s) и mailto; рисунки — через resolveImage или по относительному пути.
 */
const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
const UNESCAPES = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'" }

export function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (char) => ESCAPES[char])
}

/** Текст из HTML, собранного этим модулем (теги убираются, сущности — обратно в символы). */
export function htmlToText(html) {
  return String(html)
    .replace(/<(?:br|hr)\b[^>]*>|<\/(?:p|li|td|th|tr|h[1-6]|figcaption|blockquote|pre|div|ul|ol|table)>/g, ' ')
    .replace(/<[^>]*>/g, '')
    .replace(/&(?:amp|lt|gt|quot|#39);/g, (entity) => UNESCAPES[entity])
    .replace(/\s+/g, ' ').trim()
}

/** Якорь раздела: строчные буквы (кириллица сохраняется) и цифры через дефис. */
export function slugify(text) {
  return String(text)
    .toLowerCase()
    .replace(/[«»"'`*_“”„‘’]/g, '')
    .replace(/[^\p{L}\p{N}]+/gu, '-')
    .replace(/^-+|-+$/g, '') || 'section'
}

// Атрибуты pandoc: {#id .class key=value key="value" -} и вставка для одного формата {=openxml}
const ATTR_ITEM = String.raw`(?:[#.][^\s{}]+|[\w-]+=(?:"[^"]*"|[^\s{}"]*)|=[\w-]+|-)`
const ATTRS = String.raw`\{\s*${ATTR_ITEM}(?:\s+${ATTR_ITEM})*\s*\}`
const TRAILING_ATTRS = new RegExp(String.raw`\s*(${ATTRS})\s*$`)
const ATTR_PARTS = /([#.])([^\s{}]+)|([\w-]+)=(?:"([^"]*)"|([^\s{}"]*))|=([\w-]+)/g

export function parseAttributes(block) {
  const result = { id: '', classes: [], values: {}, format: '' }
  for (const [, sigil, name, key, quoted, plain, format] of String(block).matchAll(ATTR_PARTS)) {
    if (sigil === '#') result.id = name
    else if (sigil === '.') result.classes.push(name)
    else if (key) result.values[key] = quoted ?? plain ?? ''
    else if (format) result.format = format
  }
  return result
}

function safeHref(href) {
  if (/^#[^\s"'<>]*$/.test(href)) return href
  if (/^(https?:\/\/|mailto:)/i.test(href)) return href
  return null
}

function safeImageUrl(url) {
  if (!url) return null
  const value = String(url)
  if (/^data:image\/(?:png|jpe?g|gif|webp|svg\+xml)[;,]/i.test(value)) return value
  if (/^https?:\/\//i.test(value)) return value
  // Любая другая схема (javascript:, vbscript:, file:…) — не рисунок руководства
  if (/^[a-z][\w+.-]*:/i.test(value) || /^\/\//.test(value)) return null
  return value
}

function imageUrl(src, options) {
  return safeImageUrl(options.resolveImage ? options.resolveImage(src) : src)
}

// Адрес ссылки или рисунка: без пробелов, допускаются скобки одного уровня — wiki/Foo_(bar)
const URL_PART = String.raw`(?:[^()\s]|\([^()\s]*\))+`

const INLINE = new RegExp([
  String.raw`(?<br>(?: {2,}|\\)\n)`,
  String.raw`<!--[\s\S]*?-->`,
  String.raw`\\(?<esc>[\\\x60*_{}\[\]()#+\-.!|<>~"'^$])`,
  String.raw`\x60(?<code>[^\x60]+)\x60(?<codeAttrs>${ATTRS})?`,
  String.raw`!\[(?<alt>[^\]]*)\]\((?<src>${URL_PART})(?:\s+"(?<imgTitle>[^"]*)")?\)(?:${ATTRS})?`,
  String.raw`\[(?<label>(?:[^\[\]]|\[[^\]]*\])+)\]\((?<href>${URL_PART})(?:\s+"(?<linkTitle>[^"]*)")?\)(?:${ATTRS})?`,
  String.raw`\[(?<span>[^\[\]]+)\]${ATTRS}`,
  String.raw`<(?<auto>(?:https?:\/\/|mailto:)[^\s<>]+)>`,
  String.raw`\*\*(?<bold>[\s\S]+?)\*\*`,
  String.raw`\*(?<italic>[^*\s](?:[^*]*[^*\s])?)\*`,
].join('|'), 'g')

function text(value) {
  return escapeHtml(value.replace(/\n/g, ' '))
}

function externalLink(href, inner, title) {
  const titleAttr = title ? ` title="${escapeHtml(title)}"` : ''
  return `<a href="${escapeHtml(href)}"${titleAttr} target="_blank" rel="noopener noreferrer">${inner}</a>`
}

export function renderInline(source, options = {}) {
  const value = String(source)
  let html = ''
  let last = 0
  for (const match of value.matchAll(INLINE)) {
    html += text(value.slice(last, match.index))
    last = match.index + match[0].length
    const g = match.groups
    if (g.br !== undefined) html += '<br>'
    else if (g.esc !== undefined) html += escapeHtml(g.esc)
    else if (g.code !== undefined) {
      // `…`{=html} и подобные — вставка только для одного формата: в приложении не выводится
      if (!(g.codeAttrs && parseAttributes(g.codeAttrs).format)) html += `<code>${escapeHtml(g.code)}</code>`
    } else if (g.src !== undefined) {
      const url = imageUrl(g.src, options)
      const title = g.imgTitle ? ` title="${escapeHtml(g.imgTitle)}"` : ''
      html += url ? `<img src="${escapeHtml(url)}" alt="${escapeHtml(plainText(g.alt))}"${title} loading="lazy" decoding="async">` : text(plainText(g.alt))
    } else if (g.href !== undefined) {
      const safe = safeHref(g.href)
      const inner = renderInline(g.label, options)
      if (!safe) html += inner
      else if (safe.startsWith('#')) html += `<a href="${escapeHtml(safe)}">${inner}</a>`
      else html += externalLink(safe, inner, g.linkTitle)
    } else if (g.span !== undefined) html += renderInline(g.span, options)
    else if (g.auto !== undefined) html += externalLink(g.auto, escapeHtml(g.auto))
    else if (g.bold !== undefined) html += `<strong>${renderInline(g.bold, options)}</strong>`
    else if (g.italic !== undefined) html += `<em>${renderInline(g.italic, options)}</em>`
    // иначе — комментарий HTML: пропускается
  }
  return html + text(value.slice(last))
}

/** Строка без разметки — для оглавления, якорей и подписей рисунков. */
export function plainText(source) {
  return htmlToText(renderInline(source))
}

function frontMatter(lines) {
  const meta = {}
  if (lines[0]?.trim() !== '---') return { meta, start: 0 }
  const end = lines.findIndex((line, index) => index > 0 && /^(---|\.\.\.)$/.test(line.trim()))
  if (end === -1) return { meta, start: 0 }
  for (const line of lines.slice(1, end)) {
    const match = line.match(/^([\w-]+):\s*(.*?)\s*$/)
    if (!match) continue
    const [, key, raw] = match
    const quoted = raw.match(/^"(.*)"$/) || raw.match(/^'(.*)'$/)
    meta[key] = quoted ? quoted[1].replace(/\\"/g, '"') : raw
  }
  return { meta, start: end + 1 }
}

function splitRow(line) {
  return line.trim().replace(/^\|/, '').replace(/(?<!\\)\|$/, '').split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, '|'))
}

const TABLE_SEPARATOR = /^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/
const LIST_ITEM = /^( {0,3})([-*+]|\d{1,9}[.)])(?:(\s+)(.*))?$/
const HEADING = /^ {0,3}(#{1,6})(?:\s+(.*?))?\s*$/
const RULE = /^ {0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$/
const FENCE = /^( {0,3})(`{3,}|~{3,})\s*(.*)$/
const FIGURE = new RegExp(String.raw`^!\[([^\]]*)\]\((${URL_PART})(?:\s+"([^"]*)")?\)(?:${ATTRS})?$`)
const CALLOUTS = { 'Совет': 'tip', 'Подсказка': 'tip', 'Важно': 'important', 'Внимание': 'important', 'Осторожно': 'important', 'Примечание': 'note' }

function isBlockStart(line) {
  const trimmed = line.trim()
  return HEADING.test(line) || RULE.test(line) || FENCE.test(line) || trimmed.startsWith('>') || trimmed.startsWith('|')
    || trimmed.startsWith('<!--') || /^:{3,}/.test(trimmed)
}

function uniqueId(ctx, wanted) {
  let id = wanted
  for (let n = 2; ctx.usedIds.has(id); n++) id = `${wanted}-${n}`
  ctx.usedIds.add(id)
  return id
}

function renderHeading(level, source, ctx, depth) {
  let content = (source || '').replace(/\s+#+$/, '')
  let attrs = null
  const trailing = content.match(TRAILING_ATTRS)
  if (trailing) {
    attrs = parseAttributes(trailing[1])
    content = content.slice(0, trailing.index).replace(/\s+#+$/, '')
  }
  const title = plainText(content)
  const custom = attrs?.id && /^[\p{L}\p{N}_:.-]+$/u.test(attrs.id) ? attrs.id : ''
  const id = uniqueId(ctx, custom || slugify(title))
  if (depth === 0) ctx.headings.push({ level, text: title, id })
  const tag = `h${Math.min(6, level + (ctx.options.headingOffset || 0))}`
  const anchor = ctx.options.anchors ? `<a class="md-anchor" href="#${escapeHtml(id)}" aria-hidden="true" tabindex="-1">#</a>` : ''
  return `<${tag} id="${escapeHtml(id)}">${renderInline(content, ctx.options)}${anchor}</${tag}>`
}

function renderFigure([, alt, src, title], options) {
  const url = imageUrl(src, options)
  if (!url) return ''
  const caption = alt ? `<figcaption>${renderInline(alt, options)}</figcaption>` : ''
  const titleAttr = title ? ` title="${escapeHtml(title)}"` : ''
  return `<figure><img src="${escapeHtml(url)}" alt="${escapeHtml(plainText(alt))}"${titleAttr} loading="lazy" decoding="async">${caption}</figure>`
}

function renderTable(lines, i, options) {
  const header = splitRow(lines[i])
  const align = splitRow(lines[i + 1]).map((cell) => {
    if (cell.startsWith(':') && cell.endsWith(':')) return 'center'
    if (cell.endsWith(':')) return 'right'
    return cell.startsWith(':') ? 'left' : ''
  })
  const rows = []
  let next = i + 2
  while (next < lines.length && lines[next].trim() && lines[next].includes('|')) rows.push(splitRow(lines[next++]))
  const cls = (index) => (align[index] ? ` class="md-align-${align[index]}"` : '')
  const head = header.map((cell, index) => `<th${cls(index)}>${renderInline(cell, options)}</th>`).join('')
  const body = rows.map((row) => `<tr>${header.map((_, index) => `<td${cls(index)}>${renderInline(row[index] || '', options)}</td>`).join('')}</tr>`).join('')
  return { html: `<div class="md-table"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`, next }
}

function renderQuote(lines, i, ctx, depth) {
  const quoted = []
  let next = i
  while (next < lines.length) {
    const line = lines[next]
    if (line.trim().startsWith('>')) quoted.push(line.trim().replace(/^>\s?/, ''))
    // Ленивое продолжение абзаца цитаты без «>»
    else if (line.trim() && quoted.length && quoted[quoted.length - 1].trim() && !isBlockStart(line)) quoted.push(line.trim())
    else break
    next++
  }
  const inner = renderBlocks(quoted, ctx, depth + 1).join('\n')
  const label = inner.match(/^<p><strong>([^<]+?)\.?<\/strong>/)
  const kind = label && CALLOUTS[label[1].trim()]
  return { html: kind ? `<blockquote class="md-callout md-callout-${kind}">${inner}</blockquote>` : `<blockquote>${inner}</blockquote>`, next }
}

function renderList(lines, i, ctx, depth) {
  const first = lines[i].match(LIST_ITEM)
  const ordered = /\d/.test(first[2])
  const marker = ordered ? first[2].slice(-1) : first[2]
  const sameKind = (m) => (ordered ? /\d/.test(m[2]) && m[2].slice(-1) === marker : m[2] === marker)
  const items = []
  let current = null
  let loose = false
  let blank = false
  let next = i
  for (; next < lines.length; next++) {
    const line = lines[next]
    if (!line.trim()) { blank = true; current?.lines.push(''); continue }
    const indent = line.length - line.trimStart().length
    const item = line.match(LIST_ITEM)
    if (item && (!current || indent < current.contentIndent) && sameKind(item) && !RULE.test(line)) {
      if (blank && items.length) loose = true
      const gap = (item[3] || ' ').length
      current = { lines: [item[4] || ''], contentIndent: indent + item[2].length + (gap > 4 ? 1 : gap) }
      items.push(current)
      blank = false
      continue
    }
    if (current && indent >= current.contentIndent) {
      if (blank) loose = loose || current.lines.some((l) => l.trim())
      current.lines.push(line.slice(current.contentIndent))
    } else if (current && !blank && (indent > 0 || !(isBlockStart(line) || item))) {
      // Продолжение строки пункта с меньшим отступом или без него («ленивое»)
      current.lines.push(line.trim())
    } else break
    blank = false
  }
  // Пустые строки после списка ему не принадлежат
  while (next > i && !lines[next - 1].trim()) next--
  const tag = ordered ? 'ol' : 'ul'
  const start = ordered && Number(first[2].slice(0, -1)) !== 1 ? ` start="${Number(first[2].slice(0, -1))}"` : ''
  const body = items.map((entry) => {
    while (entry.lines.length && !entry.lines[entry.lines.length - 1].trim()) entry.lines.pop()
    return `<li>${renderBlocks(entry.lines, ctx, depth + 1, !loose).join('')}</li>`
  }).join('')
  return { html: `<${tag}${start}>${body}</${tag}>`, next }
}

/**
 * Блоки документа. depth > 0 — содержимое цитаты или пункта списка; tight — абзацы пункта
 * «плотного» списка выводятся без <p>.
 */
function renderBlocks(lines, ctx, depth = 0, tight = false) {
  const { options } = ctx
  const out = []
  let paragraph = []

  const flush = () => {
    if (!paragraph.length) return
    const source = paragraph.join('\n').trim()
    paragraph = []
    const figure = source.match(FIGURE)
    if (figure) {
      // Рисунок, которого нет в сборке, не выводится вовсе (тест руководств следит, чтобы все были)
      const html = renderFigure(figure, options)
      if (html) out.push(html)
      return
    }
    const html = renderInline(source, options)
    if (html.trim()) out.push(tight ? html : `<p>${html}</p>`)
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const trimmed = line.trim()
    if (!trimmed) { flush(); continue }

    if (trimmed.startsWith('<!--')) {
      flush()
      while (i < lines.length && !lines[i].includes('-->')) i++
      continue
    }
    // Разрывы страниц и блоки-обёртки pandoc нужны только DOCX и PDF
    if (/^\\(newpage|pagebreak|clearpage)\s*$/.test(trimmed) || /^:{3,}/.test(trimmed)) { flush(); continue }

    const fence = line.match(FENCE)
    if (fence) {
      flush()
      const [, indent, mark, info] = fence
      const closing = new RegExp(`^ {0,3}${mark[0]}{${mark.length},}\\s*$`)
      const dedent = new RegExp(`^ {0,${indent.length}}`)
      const code = []
      for (i++; i < lines.length && !closing.test(lines[i]); i++) code.push(lines[i].replace(dedent, ''))
      const attrs = info.trim().startsWith('{') ? parseAttributes(info) : { classes: info.trim() ? [info.trim().split(/\s+/)[0]] : [], format: '' }
      if (attrs.format) continue
      const language = attrs.classes[0] ? ` class="language-${escapeHtml(attrs.classes[0])}"` : ''
      out.push(`<pre><code${language}>${escapeHtml(code.join('\n'))}</code></pre>`)
      continue
    }

    const heading = line.match(HEADING)
    if (heading) {
      flush()
      if (depth === 0) ctx.marks.push(out.length)
      out.push(renderHeading(heading[1].length, heading[2], ctx, depth))
      continue
    }

    if (RULE.test(line)) { flush(); out.push('<hr>'); continue }

    if (line.includes('|') && lines[i + 1]?.includes('|') && TABLE_SEPARATOR.test(lines[i + 1].trim())) {
      flush()
      const table = renderTable(lines, i, options)
      out.push(table.html)
      i = table.next - 1
      continue
    }

    if (trimmed.startsWith('>')) {
      flush()
      const quote = renderQuote(lines, i, ctx, depth)
      out.push(quote.html)
      i = quote.next - 1
      continue
    }

    const item = line.match(LIST_ITEM)
    // Нумерованный список прерывает абзац, только если начинается с 1 — как в CommonMark
    if (item && (!paragraph.length || !/\d/.test(item[2]) || /^1\D/.test(item[2]))) {
      flush()
      const list = renderList(lines, i, ctx, depth)
      out.push(list.html)
      i = list.next - 1
      continue
    }

    paragraph.push(line.replace(/^\s+/, ''))
  }
  flush()
  return out
}

/**
 * Возвращает { html, headings, meta, sections }: headings — заголовки с уровнем и якорем для
 * оглавления, meta — поля титульного блока (title, subtitle, date), sections — текст каждого
 * раздела без разметки (до следующего заголовка) для поиска.
 *
 * Параметры: resolveImage(src) — адрес рисунка в сборке (null — рисунок не выводится),
 * headingOffset — сдвиг уровня тегов заголовков (1: «#» → <h2>), anchors — значок-ссылка «#»
 * у заголовков.
 */
export function renderMarkdown(source, options = {}) {
  const lines = String(source).replace(/\r\n?/g, '\n').replace(/\t/g, '    ').split('\n')
  const { meta, start } = frontMatter(lines)
  const ctx = { options, headings: [], usedIds: new Set(), marks: [] }
  const out = renderBlocks(lines.slice(start), ctx)
  const sections = {}
  ctx.headings.forEach((heading, index) => {
    const from = ctx.marks[index] + 1
    const to = ctx.marks[index + 1] ?? out.length
    sections[heading.id] = htmlToText(out.slice(from, to).join(' '))
  })
  return { html: out.join('\n'), headings: ctx.headings, meta, sections }
}
