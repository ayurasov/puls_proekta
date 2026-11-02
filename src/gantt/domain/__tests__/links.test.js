/**
 * 0.7.0: несколько предшественников, связи вех, лаг в рабочих днях, «Начало не ранее», крайний срок (ADR-0012).
 * Поведение одной связи закреплено сценариями 1.0.1 (legacyParity.test.js) и не меняется.
 */
import { describe, expect, it } from 'vitest'
import { computeDependencyShifts, dependencyConstraints, linkProblem, shiftWorkingDays } from '../dependencies'
import { createCalendar } from '../calendar'
import { deadlineMissed } from '../tree'
import { filterCounts, itemPassesFilters } from '../filters'
import { createChartContext, dependencyPaths, rowModel } from '../../chartModel'
import { buildIndex } from '../tree'
import { predecessorsText } from '../../export'

const task = (key, start, end, extra = {}) => ({ key, type: 'task', name: key, start, end, status: 'new', offset: 0, ...extra })
const milestone = (key, date, extra = {}) => ({ key, type: 'milestone', name: key, date, ...extra })
let n = 0
const link = (from, to, type = 'FS', lag = 0, lagKind = 'calendar') => ({ key: `l${++n}`, from, to, type, lag, lagKind })
const shift = (items, links, opts = {}) => computeDependencyShifts(items, { links, ...opts })
const span = (s) => s && (s.date || `${s.start}..${s.end}`)

describe('несколько предшественников', () => {
  it('FS и SS: начало — наибольшее из ограничений, длительность сохраняется', () => {
    const items = [task('A', '2026-05-01', '2026-05-10'), task('B', '2026-05-01', '2026-05-03'), task('C', '2026-06-01', '2026-06-05')]
    // FS от A → с 11.05; SS от B с лагом 15 → с 16.05
    expect(span(shift(items, [link('A', 'C'), link('B', 'C', 'SS', 15)]).get('C'))).toBe('2026-05-16..2026-05-20')
    // Без второй связи — по первой
    expect(span(shift(items, [link('A', 'C')]).get('C'))).toBe('2026-05-11..2026-05-15')
  })

  it('FF и FS вместе: задача начинается так рано, как позволяют все связи', () => {
    const items = [task('A', '2026-05-01', '2026-05-10'), task('B', '2026-05-12', '2026-05-20'), task('C', '2026-06-01', '2026-06-05')]
    // FS от A → начало не раньше 11.05; FF от B → окончание не раньше 20.05: 5 дней — с 16.05 по 20.05
    expect(span(shift(items, [link('A', 'C'), link('B', 'C', 'FF')]).get('C'))).toBe('2026-05-16..2026-05-20')
    // Окончание по FF раньше, чем даёт FS: решает FS
    expect(span(shift(items, [link('A', 'C'), link('B', 'C', 'FF', -15)]).get('C'))).toBe('2026-05-11..2026-05-15')
  })

  it('два FF: окончание — наибольшее', () => {
    const items = [task('A', '2026-05-01', '2026-05-10'), task('B', '2026-05-01', '2026-05-14'), task('C', '2026-06-01', '2026-06-03')]
    expect(span(shift(items, [link('A', 'C', 'FF'), link('B', 'C', 'FF')]).get('C'))).toBe('2026-05-12..2026-05-14')
  })

  it('цепочка с ветвлением считается за один вызов, в любом порядке строк', () => {
    const items = [task('D', '2026-01-01', '2026-01-01'), task('C', '2026-01-01', '2026-01-02'), task('B', '2026-01-01', '2026-01-01'),
      task('A', '2026-05-01', '2026-05-10')]
    const shifts = shift(items, [link('A', 'B'), link('B', 'C'), link('A', 'C', 'FS', 3), link('C', 'D')])
    expect(span(shifts.get('B'))).toBe('2026-05-11..2026-05-11')
    expect(span(shifts.get('C'))).toBe('2026-05-14..2026-05-15')
    expect(span(shifts.get('D'))).toBe('2026-05-16..2026-05-16')
  })
})

describe('лаг в рабочих днях', () => {
  // 04.11.2026 — праздник (День народного единства), 07–08.11 — выходные
  const calendar = createCalendar({ holidays: ['2026-11-04'], useRuCalendar: true })

  it('через выходные и праздники', () => {
    expect(shiftWorkingDays(calendar, '2026-11-06', 1)).toBe('2026-11-09')
    expect(shiftWorkingDays(calendar, '2026-11-03', 1)).toBe('2026-11-05')
    expect(shiftWorkingDays(calendar, '2026-11-09', -2)).toBe('2026-11-05')
    const items = [task('A', '2026-11-02', '2026-11-06'), task('B', '2026-12-01', '2026-12-02'), task('C', '2026-11-02', '2026-11-03')]
    // FS без лага в рабочих днях — следующий рабочий день; +2 — третий рабочий после окончания
    expect(span(shift(items, [link('A', 'B', 'FS', 0, 'working')], { calendar }).get('B'))).toBe('2026-11-09..2026-11-10')
    expect(span(shift(items, [link('A', 'B', 'FS', 2, 'working')], { calendar }).get('B'))).toBe('2026-11-11..2026-11-12')
    // Тот же лаг в календарных днях — через выходные
    expect(span(shift(items, [link('A', 'B', 'FS', 2)], { calendar }).get('B'))).toBe('2026-11-09..2026-11-10')
    // SS +2 рабочих от понедельника 02.11: 03.11, (04.11 праздник), 05.11
    expect(span(shift(items, [link('C', 'B', 'SS', 2, 'working')], { calendar }).get('B'))).toBe('2026-11-05..2026-11-06')
  })
})

describe('вехи в связях', () => {
  const items = [task('A', '2026-05-01', '2026-05-10'), milestone('M', '2026-07-01')]

  it('веха-последующая встаёт на дату предшественника', () => {
    expect(shift(items, [link('A', 'M')]).get('M')).toEqual({ date: '2026-05-10' })
    expect(shift(items, [link('A', 'M', 'FS', 2)]).get('M')).toEqual({ date: '2026-05-12' })
    expect(shift(items, [link('A', 'M', 'SS', 1)]).get('M')).toEqual({ date: '2026-05-02' })
    expect(shift(items, [link('A', 'M', 'FF')]).get('M')).toEqual({ date: '2026-05-10' })
    expect(shift(items, [link('A', 'M', 'SF')]).get('M')).toEqual({ date: '2026-05-01' })
    // Сдвиг предшественника учитывается
    expect(shift([{ ...items[0], offset: 3 }, items[1]], [link('A', 'M')]).get('M')).toEqual({ date: '2026-05-13' })
  })

  it('веха в цепочке: задача после вехи', () => {
    const chain = [...items, task('B', '2026-01-01', '2026-01-02')]
    const shifts = shift(chain, [link('A', 'M'), link('M', 'B')])
    expect(shifts.get('M')).toEqual({ date: '2026-05-10' })
    expect(span(shifts.get('B'))).toBe('2026-05-11..2026-05-12')
  })
})

describe('«Начало не ранее»', () => {
  const snet = (date) => ({ constraintType: 'snet', constraintDate: date })

  it('задача без связей не уходит раньше даты ограничения; длительность сохраняется', () => {
    expect(span(shift([task('A', '2026-05-01', '2026-05-03', snet('2026-05-10'))], []).get('A'))).toBe('2026-05-10..2026-05-12')
    expect(shift([task('A', '2026-05-12', '2026-05-13', snet('2026-05-10'))], []).size).toBe(0)
    // Другие ограничения MS Project на расчёт не влияют
    expect(shift([task('A', '2026-05-01', '2026-05-03', { constraintType: 'fnlt', constraintDate: '2026-05-10' })], []).size).toBe(0)
  })

  it('связь тянет начало раньше ограничения — побеждает ограничение', () => {
    const items = [task('A', '2026-05-01', '2026-05-05'), task('B', '2026-06-01', '2026-06-02', snet('2026-05-20'))]
    expect(span(shift(items, [link('A', 'B')]).get('B'))).toBe('2026-05-20..2026-05-21')
    // Связь даёт позже ограничения — по связи
    const late = [task('A', '2026-05-01', '2026-05-25'), items[1]]
    expect(span(shift(late, [link('A', 'B')]).get('B'))).toBe('2026-05-26..2026-05-27')
    // FF-связь: окончание по связи, но начало не раньше ограничения
    expect(span(shift(items, [link('A', 'B', 'FF')]).get('B'))).toBe('2026-05-20..2026-05-21')
  })

  it('веха — не раньше даты ограничения', () => {
    const items = [task('A', '2026-05-01', '2026-05-05'), milestone('M', '2026-04-01', snet('2026-05-15'))]
    expect(shift(items, [link('A', 'M')]).get('M')).toEqual({ date: '2026-05-15' })
    expect(shift([items[1]], []).get('M')).toEqual({ date: '2026-05-15' })
  })

  it('подсказки карточки: каждая связь и ограничение', () => {
    const items = [task('A', '2026-05-01', '2026-05-10'), task('B', '2026-05-01', '2026-05-03'), task('C', '2026-06-01', '2026-06-05', snet('2026-05-20'))]
    const byKey = new Map(items.map((i) => [i.key, i]))
    const c = dependencyConstraints(items[2], byKey, [link('A', 'C'), link('B', 'C', 'FF', 1)])
    expect(c.minStart).toBe('2026-05-20')
    expect(c.minEnd).toBe('2026-05-04')
    expect(c.hints).toEqual([
      { kind: 'start', value: '2026-05-11', name: 'A' }, { kind: 'end', value: '2026-05-04', name: 'B' }, { kind: 'snet', value: '2026-05-20' },
    ])
  })
})

describe('циклы и закреплённые даты', () => {
  it('строки цикла не сдвигаются, расчёт не зацикливается', () => {
    const items = [task('a', '2026-01-01', '2026-01-05'), task('b', '2026-01-06', '2026-01-10'), task('c', '2026-03-01', '2026-03-02'),
      task('d', '2026-01-01', '2026-01-01')]
    const shifts = shift(items, [link('a', 'b'), link('b', 'c'), link('c', 'a'), link('c', 'd')])
    expect([...shifts.keys()]).toEqual(['d'])
    expect(span(shifts.get('d'))).toBe('2026-03-03..2026-03-03')
  })

  it('перенос из Jira: свободная дата закреплена и при нескольких предшественниках', () => {
    const items = [task('A', '2026-05-01', '2026-05-10'), task('B', '2026-05-01', '2026-05-12'), task('C', '2026-05-02', '2026-05-30')]
    // Обе связи задают начало — окончание из Jira (30.05) остаётся
    const fixed = new Map([['C', { start: '2026-05-02', end: '2026-05-30' }]])
    expect(span(shift(items, [link('A', 'C'), link('B', 'C')], { fixed }).get('C'))).toBe('2026-05-13..2026-05-30')
    // Обе FF — закреплено начало
    const ff = new Map([['C', { start: '2026-05-02', end: '2026-05-05' }]])
    expect(span(shift(items, [link('A', 'C', 'FF'), link('B', 'C', 'FF')], { fixed: ff }).get('C'))).toBe('2026-05-02..2026-05-12')
    // FS и FF — свободной даты нет: задача встаёт по связям целиком
    expect(span(shift(items, [link('A', 'C'), link('B', 'C', 'FF', 30)], { fixed }).get('C'))).toBe('2026-05-14..2026-06-11')
  })
})

describe('проверка новой связи', () => {
  const items = [task('a', '2026-01-01', '2026-01-02'), task('b', '2026-01-03', '2026-01-04'), { key: 'g', type: 'group', name: 'Группа' }]
  const byKey = new Map(items.map((i) => [i.key, i]))
  const links = [{ key: 'l1', from: 'a', to: 'b', type: 'FS' }]

  it('повтор, цикл, сама на себя, группа', () => {
    expect(linkProblem(links, byKey, 'a', 'b')).toBe('Связь «a» → «b» уже есть')
    expect(linkProblem(links, byKey, 'b', 'a')).toMatch(/цикл/)
    expect(linkProblem(links, byKey, 'a', 'a')).toMatch(/самой себя/)
    expect(linkProblem(links, byKey, 'a', 'g')).toMatch(/задачами и вехами/)
    // Правка той же связи — не повтор
    expect(linkProblem(links, byKey, 'a', 'b', 'l1')).toBe('')
  })
})

describe('крайний срок', () => {
  it('сорван, если окончание со сдвигом позже', () => {
    expect(deadlineMissed(task('a', '2026-05-01', '2026-05-10', { deadline: '2026-05-10' }))).toBe(false)
    expect(deadlineMissed(task('a', '2026-05-01', '2026-05-10', { deadline: '2026-05-11', offset: 2 }))).toBe(true)
    expect(deadlineMissed(milestone('m', '2026-05-12', { deadline: '2026-05-11' }))).toBe(true)
    expect(deadlineMissed(task('a', '2026-05-01', '2026-05-10'))).toBe(false)
  })

  it('фильтр «Срыв крайнего срока» и «Со связями» по связям плана', () => {
    const items = [task('a', '2026-05-01', '2026-05-10', { deadline: '2026-05-08' }), task('b', '2026-05-01', '2026-05-10', { deadline: '2026-05-20' }),
      task('c', '2026-05-01', '2026-05-02')]
    const ctx = { today: '2026-05-01', links: [{ key: 'l', from: 'b', to: 'c', type: 'FS' }] }
    expect(items.filter((i) => itemPassesFilters(i, { dates: new Set(['deadline']) }, ctx, items)).map((i) => i.key)).toEqual(['a'])
    expect(items.filter((i) => itemPassesFilters(i, { traits: new Set(['dependencies']) }, ctx, items)).map((i) => i.key)).toEqual(['b', 'c'])
    const counts = filterCounts(items, {}, ctx)
    expect(counts.dates.deadline).toBe(1)
    expect(counts.traits.dependencies).toBe(2)
  })
})

describe('рисунок и выгрузка', () => {
  const calendar = createCalendar({ useRuCalendar: false })
  const items = [task('A', '2026-05-01', '2026-05-03', { deadline: '2026-05-02' }), task('B', '2026-05-04', '2026-05-05', { deadline: '2026-05-10' }),
    milestone('M', '2026-05-06')]
  const index = buildIndex(items)
  const ctx = createChartContext({
    range: { start: '2026-05-01', end: '2026-05-31' }, scale: 'day', zoom: 1, rowHeight: 40, display: { showNotes: false },
    calendar, today: '2026-05-01', index, notes: [], assignees: [],
  })
  const models = items.map((item, i) => rowModel({ item }, i, ctx))

  it('стрелки — для всех связей, в том числе в веху; связь с невидимой строкой не рисуется', () => {
    const links = [{ key: 'l1', from: 'A', to: 'B', type: 'FS' }, { key: 'l2', from: 'B', to: 'M', type: 'FS' }, { key: 'l3', from: 'A', to: 'M', type: 'SS' },
      { key: 'l4', from: 'X', to: 'M', type: 'FS' }]
    const paths = dependencyPaths(models, ctx, links)
    expect(paths.map((p) => p.key)).toEqual(['l1', 'l2', 'l3'])
    const intoMilestone = paths.find((p) => p.key === 'l2')
    expect(intoMilestone.x2).toBe(models[2].milestone.x - 9)
    expect(intoMilestone.y2).toBe(models[2].milestone.y)
  })

  it('метка крайнего срока: красная, если срок сорван', () => {
    expect(models[0].deadline).toMatchObject({ missed: true, color: '#dc2626' })
    expect(models[1].deadline).toMatchObject({ missed: false, color: '#16a34a' })
    expect(models[2].deadline).toBeUndefined()
  })

  it('CSV: предшественники как в MS Project — номер строки, тип, лаг', () => {
    const numbers = new Map([['A', 3], ['B', 5]])
    const rows = { A: { name: 'A' }, B: { name: 'B' }, H: { name: 'Скрытая' } }
    const text = predecessorsText([
      { from: 'A', type: 'FS', lag: 2, lagKind: 'calendar' }, { from: 'B', type: 'SS', lag: 0 }, { from: 'A', type: 'FF', lag: -1, lagKind: 'working' },
      { from: 'H', type: 'FS', lag: 0 },
    ], numbers, rows)
    expect(text).toBe('3FS+2д;5SS;3FF-1рд;«Скрытая»FS')
  })
})
