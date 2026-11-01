import { describe, expect, it } from 'vitest'
import { appLabel, normalizeScopes, scopeLabel, sortKeys, toggleScope } from '../integrationKeys'

/** 0.6.0: ключи интеграций — права как на сервере. */
describe('права ключа', () => {
  it('показатели без чтения проектов не бывает, порядок — как в каталоге', () => {
    expect(normalizeScopes(['projects.stats'])).toEqual(['projects.read', 'projects.stats'])
    expect(normalizeScopes(['projects.stats', 'projects.read', 'unknown'])).toEqual(['projects.read', 'projects.stats'])
    expect(normalizeScopes([])).toEqual([])
  })

  it('снятие чтения снимает показатели, включение показателей включает чтение', () => {
    expect(toggleScope(['projects.read', 'projects.stats'], 'projects.read')).toEqual([])
    expect(toggleScope(['projects.read', 'projects.stats'], 'projects.stats')).toEqual(['projects.read'])
    expect(toggleScope([], 'projects.stats')).toEqual(['projects.read', 'projects.stats'])
  })

  it('подписи и порядок ключей', () => {
    expect(scopeLabel('projects.stats')).toBe('Показатели проектов')
    expect(appLabel('dashboards')).toBe('«Дашборды компании»')
    expect(appLabel('x')).toBe('x')
    const sorted = sortKeys([
      { id: 1, active: false, issuedAt: '2026-11-01T10:00:00Z' },
      { id: 2, active: true, issuedAt: '2026-10-01T10:00:00Z' },
      { id: 3, active: true, issuedAt: '2026-10-31T10:00:00Z' },
    ])
    expect(sorted.map((k) => k.id)).toEqual([3, 2, 1])
  })
})
