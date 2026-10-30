/**
 * Сценарии тестов 1.0.1 (tests/gantt-filters.test.mjs, tests/gantt-links.test.mjs), перенесённые
 * на доменные модули 0.2: поведение фильтров, связей и дерева сохранено.
 */
import { describe, expect, it } from 'vitest'
import { buildIndex, flattenTree } from '../tree'
import { visibilityPredicate } from '../filters'
import { computeDependencyShifts } from '../dependencies'

let pos = 0
const task = (key, start, end, extra = {}) => ({
  key, type: 'task', name: key, start, end, endMode: 'date', duration: 1, durationType: 'calendar', progress: 0,
  offset: 0, parentKey: null, dependencyKey: null, dependencyType: 'FS', dependencyLag: 0, status: 'new',
  assigneeKey: null, category: null, position: ++pos, ...extra,
})

/* G1 (группа)
   ├─ T1 (new, X, a1)  ├─ T2 (done, X, a2)  └─ P (inwork, Y)
                                               ├─ C (blocked, X, a1)
                                               └─ M1 (веха)
   S1 (done) */
const base = () => {
  pos = 0
  return [
    { key: 'G1', type: 'group', name: 'G', position: ++pos, parentKey: null },
    task('T1', '2026-05-01', '2026-05-05', { parentKey: 'G1', category: 'X', assigneeKey: 'a1' }),
    task('T2', '2026-05-02', '2026-05-06', { parentKey: 'G1', category: 'X', status: 'done', assigneeKey: 'a2' }),
    task('P', '2026-05-03', '2026-05-20', { parentKey: 'G1', category: 'Y', status: 'inwork' }),
    task('C', '2026-05-04', '2026-05-08', { parentKey: 'P', category: 'X', status: 'blocked', assigneeKey: 'a1' }),
    { key: 'M1', type: 'milestone', name: 'M', date: '2026-05-05', parentKey: 'P', position: ++pos },
    task('S1', '2026-05-01', '2026-05-02', { status: 'done' }),
  ]
}

function visible(items, filters = {}, collapsed = new Set()) {
  const index = buildIndex(items)
  const predicate = visibilityPredicate(index, {
    search: filters.search || '',
    categories: new Set(filters.categories || []),
    statuses: new Set(filters.statuses || []),
    assignees: new Set(filters.assignees || []),
  })
  return flattenTree(index, { collapsed, expandAll: Boolean(predicate), isVisible: predicate }).map((r) => r.item.key).join(',')
}

describe('фильтры (сценарии 1.0.1)', () => {
  it.each([
    [{}, 'G1,T1,T2,P,C,M1,S1'],
    [{ statuses: ['blocked'] }, 'G1,P,C,M1'],
    [{ statuses: ['new', 'done'] }, 'G1,T1,T2,S1'],
    [{ assignees: ['a1'] }, 'G1,T1,P,C,M1'],
    [{ assignees: ['a1', 'a2'] }, 'G1,T1,T2,P,C,M1'],
    [{ assignees: ['__none__'] }, 'G1,P,M1,S1'],
    [{ statuses: ['blocked'], assignees: ['a1'] }, 'G1,P,C,M1'],
    [{ statuses: ['done'], assignees: ['a1'] }, ''],
    [{ categories: ['X'], statuses: ['inwork'], assignees: ['__none__'] }, ''],
    [{ categories: ['X'], statuses: ['blocked'], assignees: ['__none__'] }, ''],
    [{ categories: ['X'] }, 'G1,T1,T2,P,C,M1'],
    [{ categories: ['__none__'] }, 'S1'],
  ])('%j → %s', (filters, expected) => {
    expect(visible(base(), filters)).toBe(expected)
  })

  it('родитель другой категории: ребёнок виден вместе с предками', () => {
    pos = 0
    const items = [
      { key: 'G1', type: 'group', name: 'G', position: ++pos },
      task('P', '2026-05-01', '2026-05-20', { parentKey: 'G1', category: 'Y', status: 'done' }),
      task('C', '2026-05-02', '2026-05-06', { parentKey: 'P', category: 'X', status: 'blocked' }),
    ]
    expect(visible(items, { statuses: ['blocked'] })).toBe('G1,P,C')
  })

  it('веха показывается один раз', () => {
    const keys = visible(base(), { statuses: ['blocked'] }).split(',')
    expect(keys.filter((k) => k === 'M1')).toHaveLength(1)
  })

  it('свёрнутая группа раскрывается при активном фильтре', () => {
    expect(visible(base(), { statuses: ['blocked'] }, new Set(['G1']))).toBe('G1,P,C,M1')
    expect(visible(base(), {}, new Set(['G1']))).toBe('G1,S1')
  })

  it('поиск показывает предков совпадений', () => {
    expect(visible(base(), { search: 'c' })).toBe('G1,P,C')
  })
})

describe('связи и лаг (сценарии 1.0.1)', () => {
  it.each([
    ['FS', 0, '2026-05-11..2026-05-15'],
    ['FS', 3, '2026-05-14..2026-05-18'],
    ['FS', -2, '2026-05-09..2026-05-13'],
    ['SS', 0, '2026-05-01..2026-05-05'],
    ['SS', 3, '2026-05-04..2026-05-08'],
    ['SS', -2, '2026-04-29..2026-05-03'],
    ['FF', 0, '2026-05-06..2026-05-10'],
    ['FF', 3, '2026-05-09..2026-05-13'],
    ['SF', 0, '2026-04-27..2026-05-01'],
    ['SF', 3, '2026-04-30..2026-05-04'],
  ])('%s lag=%i', (type, lag, expected) => {
    const items = [
      task('A', '2026-05-01', '2026-05-10'),
      task('B', '2026-06-01', '2026-06-05', { dependencyKey: 'A', dependencyType: type, dependencyLag: lag }),
    ]
    const shift = computeDependencyShifts(items).get('B')
    expect(`${shift.start}..${shift.end}`).toBe(expected)
  })

  it('подзадача со связью не выходит за сроки родителя', () => {
    const items = [
      task('A', '2026-04-01', '2026-04-10'),
      task('P', '2026-05-01', '2026-05-31'),
      task('B', '2026-05-10', '2026-05-14', { parentKey: 'P', dependencyKey: 'A', dependencyType: 'SS', dependencyLag: 5 }),
    ]
    const shift = computeDependencyShifts(items).get('B')
    expect(`${shift.start}..${shift.end}`).toBe('2026-05-01..2026-05-05')
  })

  it('учитывает сдвиг и фактическое завершение предшественника, цепочки', () => {
    const items = [
      task('A', '2026-05-01', '2026-05-10', { offset: 2 }),
      task('B', '2026-05-11', '2026-05-12', { dependencyKey: 'A' }),
      task('C', '2026-05-13', '2026-05-13', { dependencyKey: 'B' }),
    ]
    const shifts = computeDependencyShifts(items)
    expect(shifts.get('B')).toEqual({ start: '2026-05-13', end: '2026-05-14' })
    expect(shifts.get('C')).toEqual({ start: '2026-05-15', end: '2026-05-15' })
    const done = [
      task('A', '2026-05-01', '2026-05-10', { status: 'done', completedDate: '2026-05-06', offset: -4 }),
      task('B', '2026-05-11', '2026-05-12', { dependencyKey: 'A' }),
    ]
    expect(computeDependencyShifts(done).get('B')).toEqual({ start: '2026-05-07', end: '2026-05-08' })
  })

  it('зависимость от вехи', () => {
    const items = [
      { key: 'M', type: 'milestone', name: 'M', date: '2026-07-01', position: 1 },
      task('B', '2026-05-11', '2026-05-12', { dependencyKey: 'M' }),
    ]
    expect(computeDependencyShifts(items).get('B')).toEqual({ start: '2026-07-02', end: '2026-07-03' })
  })
})
