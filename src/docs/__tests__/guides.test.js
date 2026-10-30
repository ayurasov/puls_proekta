import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { availableGuides, canReadGuide, GUIDES, guideDownloads, loadGuide, resolveGuideImage } from '../guides'

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
