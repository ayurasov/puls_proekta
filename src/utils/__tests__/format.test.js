import { describe, expect, it } from 'vitest'
import { countLabel, formatBytes, formatDate, formatNumber, formatRelative, parseDay, pluralize } from '../format'

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

describe('formatNumber', () => {
  it('по-русски: десятичная запятая и пробел между разрядами', () => {
    expect(formatNumber(27.4)).toBe('27,4')
    expect(formatNumber(1250)).toBe('1 250')
    expect(formatNumber(3.25)).toBe('3,3')
    expect(formatNumber(null)).toBe('0')
  })

  it('число со словом в нужной форме', () => {
    expect([0, 1, 2, 5, 11, 21, 104].map((n) => countLabel(n, 'строка', 'строки', 'строк')))
      .toEqual(['0 строк', '1 строка', '2 строки', '5 строк', '11 строк', '21 строка', '104 строки'])
    expect(countLabel(3, 'заблокирована', 'заблокированы', 'заблокировано')).toBe('3 заблокированы')
  })
})

describe('formatBytes (0.7.3, размеры резервных копий)', () => {
  it('по-русски, с десятичной запятой', () => {
    expect(formatBytes(0)).toBe('0 Б')
    expect(formatBytes(840 * 1024)).toBe('840 КБ')
    expect(formatBytes(5_452_595)).toBe('5,2 МБ')
    expect(formatBytes(157_286_400)).toBe('150 МБ')
    expect(formatBytes(3 * 1024 ** 4)).toBe('3 ТБ')
  })
})
