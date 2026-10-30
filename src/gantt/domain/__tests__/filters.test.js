/**
 * Фильтры 0.4: сроки, Jira, тип и прочее, «мои задачи», числа у пунктов, сохранённые фильтры.
 * «Сегодня» — пятница 30.10.2026: неделя 26.10–01.11, следующая 02.11–08.11, месяц — октябрь.
 */
import { describe, expect, it } from 'vitest'
import { buildIndex, flattenTree } from '../tree'
import {
  applyFilters, clearFilters, emptyFilters, filterChips, filterCounts, hasActiveFilters, isOverdue, itemPassesFilters,
  myAssigneeKeys, parseSavedFilters, periodLabel, periodRange, removeSavedFilter, sameFilters, serializeFilters,
  toggleFilter, upsertSavedFilter, visibilityPredicate, SAVED_FILTERS_LIMIT,
} from '../filters'

const TODAY = '2026-10-30'

let pos = 0
const task = (key, start, end, extra = {}) => ({
  key, type: 'task', name: key, start, end, status: 'new', progress: 0, offset: 0, parentKey: null,
  dependencyKey: null, assigneeKey: null, category: null, commentCount: 0, position: ++pos, ...extra,
})

/* G (группа)
   ├─ A  20.10–27.10, в работе, a1, X, Jira (в работе)          — просрочена, эта неделя, месяц
   ├─ B  03.11–05.11, новая, a2, 2 комментария, после A, Jira (к выполнению, расходится) — следующая неделя
   └─ M  веха 04.11
   C  01.09–10.09, завершена со сдвигом +3                       — сроки сдвинуты
   D  01.10–20.10, новая, сдвиг +15 (до 04.11)                   — просрочена, сдвинута, неделя, месяц
   E  без дат
   M2 веха 01.12, ни к чему не привязана */
const plan = () => {
  pos = 0
  return [
    { key: 'G', type: 'group', name: 'G', position: ++pos, parentKey: null },
    task('A', '2026-10-20', '2026-10-27', { parentKey: 'G', status: 'inwork', assigneeKey: 'a1', category: 'X', jiraUrl: 'https://jira/browse/P-1' }),
    task('B', '2026-11-03', '2026-11-05', { parentKey: 'G', assigneeKey: 'a2', commentCount: 2, dependencyKey: 'A', jiraUrl: 'https://jira/browse/P-2' }),
    { key: 'M', type: 'milestone', name: 'M', date: '2026-11-04', parentKey: 'G', position: ++pos },
    task('C', '2026-09-01', '2026-09-10', { status: 'done', offset: 3, completedDate: '2026-09-13' }),
    task('D', '2026-10-01', '2026-10-20', { offset: 15 }),
    task('E', null, null),
    { key: 'M2', type: 'milestone', name: 'M2', date: '2026-12-01', parentKey: null, position: ++pos },
  ]
}

const ASSIGNEES = [
  { key: 'a1', name: 'Петров Пётр', userId: 7 },
  { key: 'a2', name: 'Иванов Иван', userId: null },
  { key: 'a3', name: 'Иванов Иван', userId: 8 },
]
const ME = { id: 7, name: 'Иванов  Иван' }

const jira = {
  linked: (item) => Boolean(item.jiraUrl),
  differs: (item) => item.key === 'B',
  category: (item) => ({ A: 'indeterminate', B: 'new' })[item.key] || null,
}
const ctx = (extra = {}) => ({ today: TODAY, jira, mine: myAssigneeKeys(ASSIGNEES, ME), ...extra })

function filters(data = {}) {
  const f = applyFilters(emptyFilters(), data)
  f.search = data.search || ''
  return f
}

function visible(data, context = ctx(), items = plan()) {
  const index = buildIndex(items)
  const predicate = visibilityPredicate(index, filters(data), context)
  return flattenTree(index, { expandAll: Boolean(predicate), isVisible: predicate }).map((r) => r.item.key).join(',')
}

describe('периоды', () => {
  it('неделя с понедельника, следующая неделя, месяц', () => {
    expect(periodRange('week', TODAY)).toEqual({ from: '2026-10-26', to: '2026-11-01' })
    expect(periodRange('nextWeek', TODAY)).toEqual({ from: '2026-11-02', to: '2026-11-08' })
    expect(periodRange('month', TODAY)).toEqual({ from: '2026-10-01', to: '2026-10-31' })
    expect(periodRange('week', '2026-11-01')).toEqual({ from: '2026-10-26', to: '2026-11-01' })
  })

  it('свой период: перепутанные границы, открытый с одной стороны, без дат', () => {
    expect(periodRange('period', TODAY, { from: '2026-11-15', to: '2026-11-01' })).toEqual({ from: '2026-11-01', to: '2026-11-15' })
    expect(periodRange('period', TODAY, { from: '2026-11-01', to: '' })).toEqual({ from: '2026-11-01', to: null })
    expect(periodRange('period', TODAY, { from: '', to: '' })).toBeNull()
    expect(periodRange('period', TODAY, { from: '2026-02-30', to: 'abc' })).toBeNull()
    expect(periodRange('overdue', TODAY)).toBeNull()
  })

  it('подписи своего периода', () => {
    expect(periodLabel({ from: '2026-11-01', to: '2026-11-15' })).toBe('01.11.2026 — 15.11.2026')
    expect(periodLabel({ from: '2026-11-01', to: '' })).toBe('с 01.11.2026')
    expect(periodLabel({ from: '', to: '2026-11-15' })).toBe('по 15.11.2026')
    expect(periodLabel({ from: '2026-11-01', to: '2026-11-01' })).toBe('01.11.2026')
    expect(periodLabel({})).toBe('Свой период')
  })

  it('просрочка: не завершена и плановое окончание прошло; сегодняшний срок — ещё не просрочка', () => {
    expect(isOverdue(task('x', '2026-10-01', '2026-10-29'), TODAY)).toBe(true)
    expect(isOverdue(task('x', '2026-10-01', TODAY), TODAY)).toBe(false)
    expect(isOverdue(task('x', '2026-10-01', '2026-10-29', { status: 'done' }), TODAY)).toBe(false)
    expect(isOverdue(task('x', '2026-10-01', '2026-10-29', { status: 'blocked', offset: 10 }), TODAY)).toBe(true)
    expect(isOverdue({ key: 'm', type: 'milestone', date: '2026-10-01' }, TODAY)).toBe(false)
  })
})

describe('группа «Сроки»', () => {
  it.each([
    [{ dates: ['week'] }, 'G,A,M,D,M2'],
    [{ dates: ['nextWeek'] }, 'G,B,M,D,M2'],
    [{ dates: ['month'] }, 'G,A,M,D,M2'],
    [{ dates: ['overdue'] }, 'G,A,M,D,M2'],
    [{ dates: ['shifted'] }, 'C,D,M2'],
    [{ dates: ['noDates'] }, 'E,M2'],
    [{ dates: ['period'], period: { from: '2026-09-05', to: '2026-09-06' } }, 'C,M2'],
    [{ dates: ['period'], period: { from: '2026-11-05', to: '' } }, 'G,B,M,M2'],
    // внутри группы — «или», между группами — «и»
    [{ dates: ['noDates', 'shifted'] }, 'C,D,E,M2'],
    [{ dates: ['overdue'], statuses: ['inwork'] }, 'G,A,M,M2'],
    [{ dates: ['overdue'], statuses: ['new'] }, 'D,M2'],
  ])('%j → %s', (data, expected) => {
    expect(visible(data)).toBe(expected)
  })

  it('сдвиг-просрочка продлевает сроки задачи для периода', () => {
    // D по плану до 20.10, со сдвигом +15 — до 04.11: попадает в следующую неделю
    expect(itemPassesFilters(plan().find((i) => i.key === 'D'), filters({ dates: ['nextWeek'] }), ctx())).toBe(true)
  })

  it('«Свой период» без дат не фильтрует', () => {
    const f = filters({ dates: ['period'] })
    expect(hasActiveFilters(f, ctx())).toBe(false)
    expect(visibilityPredicate(buildIndex(plan()), f, ctx())).toBeNull()
  })
})

describe('группа Jira', () => {
  it.each([
    [{ jira: ['linked'] }, 'G,A,B,M,M2'],
    [{ jira: ['unlinked'] }, 'C,D,E,M2'],
    [{ jira: ['differs'] }, 'G,B,M,M2'],
    [{ jira: ['category:indeterminate'] }, 'G,A,M,M2'],
    [{ jira: ['category:new', 'category:done'] }, 'G,B,M,M2'],
    [{ jira: ['linked'], dates: ['overdue'] }, 'G,A,M,M2'],
  ])('%j → %s', (data, expected) => {
    expect(visible(data)).toBe(expected)
  })

  it('без подключённой Jira отметки группы не действуют', () => {
    const f = filters({ jira: ['linked'] })
    expect(hasActiveFilters(f, ctx({ jira: null }))).toBe(false)
    expect(visible({ jira: ['linked'] }, ctx({ jira: null }))).toBe('G,A,B,M,C,D,E,M2')
  })
})

describe('группа «Тип и прочее» и «мои задачи»', () => {
  it.each([
    [{ traits: ['comments'] }, 'G,B,M,M2'],
    [{ traits: ['dependencies'] }, 'G,A,B,M,M2'],
    // «Только вехи»: вехи — не контекст, а то, что отбирается; задачи скрыты
    [{ traits: ['milestones'] }, 'G,M,M2'],
    [{ traits: ['milestones'], dates: ['nextWeek'] }, 'G,M'],
    [{ traits: ['milestones'], statuses: ['new'] }, ''],
    [{ mine: true }, 'G,A,B,M,M2'],
    [{ mine: true, statuses: ['new'] }, 'G,B,M,M2'],
  ])('%j → %s', (data, expected) => {
    expect(visible(data)).toBe(expected)
  })

  it('мои исполнители: связанные со мной и несвязанные тёзки, но не тёзка, связанный с другим', () => {
    expect([...myAssigneeKeys(ASSIGNEES, ME)].sort()).toEqual(['a1', 'a2'])
    expect([...myAssigneeKeys(ASSIGNEES, { id: 9, name: 'иванов иван' })]).toEqual(['a2'])
    expect([...myAssigneeKeys([{ key: 'z', name: 'Семёнов Олег' }], { id: 1, name: 'Семенов Олег' })]).toEqual(['z'])
    expect(myAssigneeKeys(ASSIGNEES, null).size).toBe(0)
  })

  it('«мои задачи», если меня нет среди исполнителей, — ничего', () => {
    expect(visible({ mine: true }, ctx({ mine: new Set() }))).toBe('M2')
  })

  it('поиск и фильтр вместе', () => {
    // веха M видна по фильтру как контекст, но в поиск не попала
    expect(visible({ search: 'b', dates: ['nextWeek'] })).toBe('G,B')
  })
})

describe('числа у пунктов', () => {
  it('по всему плану, у «Только вехи» — число вех', () => {
    const counts = filterCounts(plan(), filters({ period: { from: '2026-09-01', to: '2026-09-30' } }), ctx())
    expect(counts.statuses).toEqual({ inwork: 1, new: 3, done: 1 })
    expect(counts.assignees).toEqual({ a1: 1, a2: 1, __none__: 3 })
    expect(counts.dates).toEqual({ week: 2, nextWeek: 2, month: 2, period: 1, overdue: 2, shifted: 2, noDates: 1 })
    expect(counts.jira).toEqual({ linked: 2, unlinked: 3, differs: 1, 'category:new': 1, 'category:indeterminate': 1 })
    expect(counts.traits).toEqual({ milestones: 2, comments: 1, dependencies: 2 })
    expect(counts.mine).toBe(2)
  })

  it('без Jira числа группы Jira не считаются', () => {
    expect(filterCounts(plan(), filters(), ctx({ jira: null })).jira).toEqual({})
  })
})

describe('отметки и сохранённые фильтры', () => {
  it('подписи отметок по порядку групп', () => {
    const f = filters({ mine: true, statuses: ['inwork'], assignees: ['a1', '__none__'], categories: ['X'], dates: ['period', 'overdue'],
      period: { from: '2026-11-01', to: '2026-11-15' }, jira: ['category:done'], traits: ['milestones'] })
    expect(filterChips(f, { assignees: ASSIGNEES }).map((c) => c.label)).toEqual([
      'Мои задачи', 'В работе', 'Петров Пётр', 'Без исполнителя', 'X', 'Свой период: 01.11.2026 — 15.11.2026', 'Просрочены',
      'Jira: Готово', 'Только вехи',
    ])
    expect(filterChips(filters({ dates: ['period'] }))[0].label).toBe('Свой период: даты не заданы')
  })

  it('переключение и сброс', () => {
    const f = filters({ statuses: ['new'] })
    toggleFilter(f, 'statuses', 'new')
    toggleFilter(f, 'dates', 'week')
    toggleFilter(f, 'mine')
    expect(serializeFilters(f)).toEqual({ dates: ['week'], mine: true })
    f.search = 'текст'
    clearFilters(f, { search: false })
    expect(serializeFilters(f)).toEqual({})
    expect(f.search).toBe('текст')
    clearFilters(f)
    expect(f.search).toBe('')
  })

  it('сохранение и применение: то же сочетание, поиск не трогается, мусор отбрасывается', () => {
    const source = filters({ statuses: ['inwork', 'new'], dates: ['period'], period: { from: '2026-11-01', to: '' }, mine: true })
    const data = JSON.parse(JSON.stringify(serializeFilters(source)))
    const target = filters({ categories: ['Y'], search: 'поиск' })
    const sets = SetsOf(target)
    applyFilters(target, data)
    expect(sameFilters(target, source)).toBe(true)
    expect(target.search).toBe('поиск')
    expect(SetsOf(target).every((set, i) => set === sets[i])).toBe(true) // те же объекты — вид остаётся реактивным
    applyFilters(target, { statuses: [1, 'done'], period: { from: 'вчера' }, mine: 'yes' })
    expect(serializeFilters(target)).toEqual({ statuses: ['done'] })
    expect(target.period).toEqual({ from: '', to: '' })
  })

  it('список сохранённых: замена по имени, новые — первыми, повреждённая запись — пустой список', () => {
    let list = upsertSavedFilter([], 'Мои на неделю', filters({ mine: true, dates: ['week'] }))
    list = upsertSavedFilter(list, 'Просрочка', filters({ dates: ['overdue'] }))
    list = upsertSavedFilter(list, '  мои на неделю ', filters({ mine: true }))
    expect(list.map((e) => e.name)).toEqual(['мои на неделю', 'Просрочка'])
    expect(list[0].filters).toEqual({ mine: true })
    expect(removeSavedFilter(list, 'Просрочка').map((e) => e.name)).toEqual(['мои на неделю'])
    expect(parseSavedFilters(JSON.stringify(list))).toEqual(list)
    expect(parseSavedFilters('{oops')).toEqual([])
    expect(parseSavedFilters('{"a":1}')).toEqual([])
    expect(parseSavedFilters('[{"name":""},{"name":"x","filters":{}},null]')).toEqual([{ name: 'x', filters: {} }])
    let many = []
    for (let i = 0; i < SAVED_FILTERS_LIMIT + 5; i += 1) many = upsertSavedFilter(many, `Ф${i}`, filters())
    expect(many).toHaveLength(SAVED_FILTERS_LIMIT)
    expect(many[0].name).toBe(`Ф${SAVED_FILTERS_LIMIT + 4}`)
  })
})

function SetsOf(f) {
  return ['statuses', 'assignees', 'categories', 'dates', 'jira', 'traits'].map((g) => f[g])
}
