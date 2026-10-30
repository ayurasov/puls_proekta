import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { parseReleaseNotes } from '../releaseNotes'
import packageJson from '../../../package.json'

const sample = `# Версии

Вступление документа.

## Версия 0.2.1 (Октябрь 2026 г.)

Исправления по итогам проверки.
Вторая строка абзаца.

**Войдите заново.**

### Телефон

- Меню открывается кнопкой,
  закрывается фоном.
- \`Esc\` закрывает [окно](x.md).

## История до 0.2

Текст истории.

### Версия 0.1.1 (1.0.1)

- Старый пункт.
`

describe('«Что нового» из docs/versions.md', () => {
  it('выпуски, вступление, разделы и многострочные пункты', () => {
    const [latest, old] = parseReleaseNotes(sample)
    expect(latest.version).toBe('0.2.1 (Октябрь 2026 г.)')
    expect(latest.lead.map((p) => p.map((x) => x.text).join(''))).toEqual(['Исправления по итогам проверки. Вторая строка абзаца.', 'Войдите заново.'])
    expect(latest.lead[1][0].bold).toBe(true)
    expect(latest.sections[0].title).toBe('Телефон')
    expect(latest.sections[0].items.map((i) => i.map((x) => x.text).join(''))).toEqual(['Меню открывается кнопкой, закрывается фоном.', 'Esc закрывает окно.'])
    expect(old.version).toBe('0.1.1 (1.0.1)')
    expect(old.lead).toEqual([])
  })

  it('первая запись настоящего docs/versions.md — текущая версия приложения', () => {
    const markdown = readFileSync(new URL('../../../docs/versions.md', import.meta.url), 'utf8')
    const releases = parseReleaseNotes(markdown)
    expect(releases[0].version.startsWith(packageJson.version + ' ')).toBe(true)
    expect(releases[0].sections.length).toBeGreaterThan(0)
  })
})
