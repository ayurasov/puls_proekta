import { describe, expect, it } from 'vitest'
import { computeDependencyShifts, dependentKeys } from '../dependencies'
import { inverseOf } from '../ops'

const task = (key, start, end, extra = {}) => ({ key, type: 'task', start, end, ...extra })

describe('автосдвиг по связям: ревизия 0.2.0', () => {
  it('цикл связей не уводит даты на месяцы вперёд — задачи цикла не сдвигаются', () => {
    const items = [
      task('a', '2026-01-01', '2026-01-05', { dependencyKey: 'b' }),
      task('b', '2026-01-06', '2026-01-10', { dependencyKey: 'a' }),
    ]
    expect(computeDependencyShifts(items).size).toBe(0)
  })

  it('FF-подзадача, упёршаяся в окончание родителя, не заканчивается раньше начала', () => {
    const items = [
      task('p', '2026-01-01', '2026-01-10'),
      task('pre', '2026-01-11', '2026-01-20'),
      task('c', '2026-01-02', '2026-01-04', { parentKey: 'p', dependencyKey: 'pre', dependencyType: 'FF' }),
    ]
    const shift = computeDependencyShifts(items).get('c')
    expect(shift.start <= shift.end).toBe(true)
    expect(shift.end).toBe('2026-01-10')
    expect(shift.start).toBe('2026-01-08')
  })

  it('only: сдвигаются только перечисленные задачи, остальные несогласованные не трогаются', () => {
    const items = [
      task('a', '2026-01-01', '2026-01-05'),
      task('b', '2026-02-01', '2026-02-03', { dependencyKey: 'a' }), // давно не согласована
      task('c', '2026-01-01', '2026-01-02'),
      task('d', '2026-01-10', '2026-01-11', { dependencyKey: 'c' }),
    ]
    const shifts = computeDependencyShifts(items, { only: new Set(['d']) })
    expect([...shifts.keys()]).toEqual(['d'])
  })

  it('зависящие задачи не предлагаются в предшественники', () => {
    const items = [task('a', '2026-01-01', '2026-01-02'), task('b', '2026-01-03', '2026-01-04', { dependencyKey: 'a' }),
      task('c', '2026-01-05', '2026-01-06', { dependencyKey: 'b' })]
    expect([...dependentKeys(items, 'a')].sort()).toEqual(['b', 'c'])
  })
})

describe('отмена изменения настроек', () => {
  it('возвращает только изменённую настройку', () => {
    const state = { plan: { settings: { scale: 'week', range: { start: '2026-01-01', end: '2026-12-31' }, display: { showNotes: true, legendEnabled: false } } } }
    const [inverse] = inverseOf(state, { op: 'plan.update', fields: { settings: { display: { legendEnabled: true } } } })
    expect(inverse.fields.settings).toEqual({ display: { legendEnabled: false } })
  })
})
