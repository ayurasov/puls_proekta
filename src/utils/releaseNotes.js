/**
 * Разбор docs/versions.md для окна «Что нового»: «## Версия X» (и «### Версия X» в истории) — выпуск,
 * абзацы до первого «###» — вступление, «### …» — разделы с абзацами и пунктами «- …» (продолжение
 * пункта — строки с отступом). **жирный** сохраняется, `код` и [ссылки](…) — только текстом.
 */
const RELEASE = /^#{2,3}\s+Версия\s+(.+)$/

export function inlineParts(text) {
  const plain = text.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/`([^`]+)`/g, '$1')
  return plain.split(/(\*\*[^*]+\*\*)/).filter(Boolean)
    .map((part) => (part.startsWith('**') && part.endsWith('**') ? { text: part.slice(2, -2), bold: true } : { text: part, bold: false }))
}

export function parseReleaseNotes(markdown) {
  const releases = []
  let release = null
  let section = null
  let list = null // текущий пункт или абзац, к которому дописываются строки-продолжения
  for (const raw of markdown.split(/\r?\n/)) {
    const line = raw.trim()
    const releaseMatch = raw.match(RELEASE)
    if (releaseMatch) {
      release = { version: releaseMatch[1].trim(), lead: [], sections: [] }
      releases.push(release)
      section = null
      list = null
      continue
    }
    if (/^#{1,2}\s/.test(raw)) { release = null; section = null; list = null; continue }
    if (!release) continue
    const sectionMatch = raw.match(/^###\s+(.+)$/)
    if (sectionMatch) {
      section = { title: sectionMatch[1].trim(), paragraphs: [], items: [] }
      release.sections.push(section)
      list = null
      continue
    }
    if (!line || line.startsWith('```') || line.startsWith('---')) { list = null; continue }
    if (line.startsWith('- ')) {
      if (!section) { section = { title: '', paragraphs: [], items: [] }; release.sections.push(section) }
      section.items.push(line.slice(2).trim())
      list = { target: section.items, index: section.items.length - 1 }
      continue
    }
    if (list && /^\s/.test(raw)) { list.target[list.index] += ` ${line}`; continue }
    const paragraphs = section ? section.paragraphs : release.lead
    if (list && list.target === paragraphs) { paragraphs[list.index] += ` ${line}`; continue }
    paragraphs.push(line)
    list = { target: paragraphs, index: paragraphs.length - 1 }
  }
  for (const r of releases) {
    r.lead = r.lead.map(inlineParts)
    for (const s of r.sections) {
      s.paragraphs = s.paragraphs.map(inlineParts)
      s.items = s.items.map(inlineParts)
    }
  }
  return releases
}
