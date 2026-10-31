import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { availableGuides, canReadGuide, GUIDES, guideDownloads, loadGuide, resolveGuideImage } from '../guides'
import { HELP_TOPICS, helpTopic } from '../helpTopics'

const SRC = fileURLToPath(new URL('../..', import.meta.url))

function vueFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return entry.name === '__tests__' ? [] : vueFiles(path)
    return entry.name.endsWith('.vue') ? [path] : []
  })
}

/**
 * Темы всех <HelpLink> в интерфейсе: topic="…" и варианты :topic="условие ? 'тема' : 'тема'"
 * (строки после «?» и «:»; строки в сравнениях условия — не темы).
 */
function helpLinkTopics() {
  const used = []
  for (const file of vueFiles(SRC)) {
    const source = readFileSync(file, 'utf8')
    for (const [, tag] of source.matchAll(/<HelpLink\b([^>]*)>/g)) {
      const fixed = tag.match(/(?:^|\s)topic="([^"]+)"/)
      const bound = tag.match(/:topic="([^"]+)"/)
      if (fixed) used.push({ file, topic: fixed[1] })
      if (bound) for (const [, topic] of bound[1].matchAll(/(?:^|[?:])\s*'([^']+)'/g)) used.push({ file, topic })
      if (!fixed && !bound) used.push({ file, topic: '(нет topic)' })
    }
    // Заголовок страницы интеграции передаёт тему справки своему HelpLink (help="…")
    for (const [, tag] of source.matchAll(/<IntegrationHeader\b([^>]*)>/g)) {
      const help = tag.match(/(?:^|\s)help="([^"]+)"/)
      if (help) used.push({ file, topic: help[1] })
    }
  }
  return used
}

describe('руководства в приложении', () => {
  it('renders both guides with every screenshot resolved to a build asset', async () => {
    for (const kind of Object.keys(GUIDES)) {
      const guide = await loadGuide(kind)
      expect(guide.meta.title).toContain(GUIDES[kind].title.toLowerCase())
      expect(guide.headings.length).toBeGreaterThan(20)
      const sources = [...guide.html.matchAll(/<img src="([^"]+)"/g)].map((match) => match[1])
      expect(sources.length).toBeGreaterThan(5)
      expect(sources.filter((src) => src.startsWith('images/'))).toEqual([])
      expect(guide.html).toMatch(/<h2 id="[^"]+">/)
      expect(await loadGuide(kind)).toBe(guide)
    }
  })

  it('resolves only screenshots that exist in images/user and images/admin', () => {
    expect(resolveGuideImage('images/user/01-login.png')).toBeTruthy()
    expect(resolveGuideImage('./images/admin/01-users.png')).toBeTruthy()
    expect(resolveGuideImage('images/user/nope.png')).toBeNull()
    expect(resolveGuideImage('images/user/../../../package.json')).toBeNull()
    expect(resolveGuideImage('https://example.ru/a.png')).toBeNull()
  })

  it('names downloads in Latin with the app version', () => {
    const { version } = JSON.parse(readFileSync(new URL('../../../package.json', import.meta.url), 'utf8'))
    expect(guideDownloads('user')).toMatchObject({
      pdfName: `puls-${version}-rukovodstvo-polzovatelya.pdf`, docxName: `puls-${version}-rukovodstvo-polzovatelya.docx`,
    })
    expect(guideDownloads('admin')).toMatchObject({
      pdfName: `puls-${version}-rukovodstvo-administratora.pdf`, docxName: `puls-${version}-rukovodstvo-administratora.docx`,
    })
    for (const kind of Object.keys(GUIDES)) {
      const files = guideDownloads(kind)
      expect(files.pdfUrl).toMatch(/\.pdf($|\?)/)
      expect(files.docxUrl).toMatch(/\.docx($|\?)/)
    }
  })

  it('shows the admin guide to administrators only', () => {
    expect(canReadGuide('user', { isAdmin: false })).toBe(true)
    expect(canReadGuide('admin', { isAdmin: false })).toBe(false)
    expect(canReadGuide('admin', { isAdmin: true })).toBe(true)
    expect(canReadGuide('nope', { isAdmin: true })).toBe(false)
    expect(availableGuides({ isAdmin: false }).map((guide) => guide.key)).toEqual(['user'])
    expect(availableGuides({ isAdmin: true }).map((guide) => guide.key)).toEqual(['user', 'admin'])
  })
})

describe('контекстная справка (HelpLink)', () => {
  it('every help topic points to an existing section with the same title', async () => {
    const broken = []
    for (const [key, topic] of Object.entries(HELP_TOPICS)) {
      const { headings } = await loadGuide(topic.guide)
      const heading = headings.find((item) => item.id === topic.id)
      if (!heading) broken.push(`${key}: нет раздела #${topic.id} в «${GUIDES[topic.guide].title}»`)
      else if (heading.text !== topic.title) broken.push(`${key}: раздел #${topic.id} называется «${heading.text}», а не «${topic.title}»`)
    }
    expect(broken).toEqual([])
  })

  it('every HelpLink in the interface uses a known topic, and every topic is used', () => {
    const used = helpLinkTopics()
    expect(used.length).toBeGreaterThanOrEqual(10)
    expect(used.filter(({ topic }) => !helpTopic(topic)).map(({ file, topic }) => `${file.slice(SRC.length)}: ${topic}`)).toEqual([])
    const usedKeys = new Set(used.map(({ topic }) => topic))
    expect(Object.keys(HELP_TOPICS).filter((key) => !usedKeys.has(key))).toEqual([])
    expect(helpTopic('toString')).toBeNull()
  })
})
