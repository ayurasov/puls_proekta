import { describe, expect, it } from 'vitest'
import { formatDate, formatRelative, parseDay, pluralize } from '../format'

describe('format', () => {
  it('parses calendar days without timezone shift', () => {
    const d = parseDay('2026-01-01')
    expect([d.getFullYear(), d.getMonth(), d.getDate()]).toEqual([2026, 0, 1])
    expect(formatDate('2026-03-09')).toBe('09.03.2026')
  })
  it('pluralizes russian nouns', () => {
    expect([1, 2, 5, 11, 21, 22, 25].map((n) => pluralize(n, 'задача', 'задачи', 'задач')))
      .toEqual(['задача', 'задачи', 'задач', 'задач', 'задача', 'задачи', 'задач'])
  })
  it('formats relative time', () => {
    const now = new Date('2026-10-30T12:00:00')
    expect(formatRelative(new Date('2026-10-30T11:59:40'), now)).toBe('только что')
    expect(formatRelative(new Date('2026-10-30T11:50:00'), now)).toBe('10 мин назад')
    expect(formatRelative(new Date('2026-10-29T09:05:00'), now)).toMatch(/^вчера в 09:05/)
  })
})
