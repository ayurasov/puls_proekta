import { addDays, endOfMonth, formatDay, isValidDay, startOfMonth, startOfWeek } from './dates'
import { STATUS_BY_KEY } from './colors'
import { ancestorKeys, parentOf, shiftedEnd } from './tree'

/**
 * Поиск и фильтры строк — логика 1.0.1 (ADR-0006), расширенная в 0.4:
 * * поиск по названию показывает совпавшие строки и их предков;
 * * фильтры отбирают задачи (внутри группы — «или», между группами — «и») и показывают их с цепочкой
 *   предков. Группы: статус, исполнитель, категория, сроки, Jira, тип и прочее, «мои задачи»;
 * * веха — контекст: видна, если видна строка, к которой она привязана, или если она ни к чему
 *   не привязана. С отметкой «Только вехи» вехи сами отбираются фильтрами (и уже не контекст);
 * * при активном поиске или фильтре свёрнутые группы раскрываются.
 * Значение '__none__' — «без категории» / «без исполнителя».
 *
 * Функции чистые: «сегодня», Jira и текущий сотрудник приходят в контексте ctx (filterContext в editor.js):
 * * ctx.today — 'YYYY-MM-DD', от него считаются «Эта неделя», «Просрочены» и т. п.;
 * * ctx.jira — { linked(item), differs(item), category(item) } или null, если Jira не подключена
 *   (тогда отметки группы Jira не действуют);
 * * ctx.mine — ключи исполнителей плана, которые относятся к текущему сотруднику (myAssigneeKeys).
 */
export const NONE = '__none__'

/** Группы-множества в виде (state.view) — в порядке показа; «мои задачи» — флаг view.mine. */
export const SET_GROUPS = ['statuses', 'assignees', 'categories', 'dates', 'jira', 'traits']

export const DATE_OPTIONS = [
  { value: 'week', label: 'Эта неделя', hint: 'Задачи, сроки которых захватывают текущую неделю (пн–вс)' },
  { value: 'nextWeek', label: 'Следующая неделя', hint: 'Задачи, сроки которых захватывают следующую неделю' },
  { value: 'month', label: 'Этот месяц', hint: 'Задачи, сроки которых захватывают текущий месяц' },
  { value: 'period', label: 'Свой период', hint: 'Задачи, сроки которых захватывают указанные даты' },
  { value: 'overdue', label: 'Просрочены', hint: 'Не завершены, а плановое окончание уже прошло — в том числе со сдвигом срока' },
  { value: 'shifted', label: 'Сроки сдвинуты', hint: 'Задачи со сдвигом: просрочка или ранняя сдача' },
  { value: 'noDates', label: 'Без дат', hint: 'Задачи без начала или окончания' },
]
export const JIRA_OPTIONS = [
  { value: 'linked', label: 'Связаны с Jira', hint: 'Строки со ссылкой на задачу подключённой Jira' },
  { value: 'unlinked', label: 'Без задачи Jira', hint: 'Задачи без ссылки на задачу Jira' },
  { value: 'differs', label: 'Расходятся с Jira', hint: 'В Jira другой статус, сроки или прогресс (название не считается)' },
]
/** Категории статусов Jira — пока данные задач Jira загружены. */
export const JIRA_CATEGORY_OPTIONS = [
  { value: 'category:new', label: 'К выполнению' },
  { value: 'category:indeterminate', label: 'В работе' },
  { value: 'category:done', label: 'Готово' },
]
export const TRAIT_OPTIONS = [
  { value: 'milestones', label: 'Только вехи', hint: 'Показать вехи (с их группами) вместо задач' },
  { value: 'comments', label: 'С комментариями', hint: 'Строки, у которых есть комментарии' },
  { value: 'dependencies', label: 'С зависимостями', hint: 'Задачи с предшественником или сами чьи-то предшественники' },
]

const OPTION_LABELS = {
  dates: Object.fromEntries(DATE_OPTIONS.map((o) => [o.value, o.label])),
  jira: Object.fromEntries([...JIRA_OPTIONS, ...JIRA_CATEGORY_OPTIONS.map((o) => ({ ...o, label: `Jira: ${o.label}` }))].map((o) => [o.value, o.label])),
  traits: Object.fromEntries(TRAIT_OPTIONS.map((o) => [o.value, o.label])),
}

export function emptyFilters() {
  return {
    search: '', statuses: new Set(), assignees: new Set(), categories: new Set(), dates: new Set(),
    period: { from: '', to: '' }, jira: new Set(), traits: new Set(), mine: false,
  }
}

// ---------- сроки ----------

/** Границы периода отметки группы «Сроки»: { from, to } (у своего периода одна из границ может быть null) или null. */
export function periodRange(option, today, period = {}) {
  if (option === 'week' || option === 'nextWeek') {
    const from = addDays(startOfWeek(today), option === 'week' ? 0 : 7)
    return { from, to: addDays(from, 6) }
  }
  if (option === 'month') return { from: startOfMonth(today), to: endOfMonth(today) }
  if (option === 'period') {
    let from = isValidDay(period?.from) ? period.from : null
    let to = isValidDay(period?.to) ? period.to : null
    if (!from && !to) return null
    if (from && to && from > to) [from, to] = [to, from]
    return { from, to }
  }
  return null
}

/** Сроки строки на шкале: задача — от начала до окончания вместе со сдвигом-просрочкой, веха — её день. */
export function itemSpan(item) {
  if (item.type === 'milestone') return item.date ? { start: item.date, end: item.date } : null
  if (item.type !== 'task' || !item.start || !item.end) return null
  const shifted = shiftedEnd(item)
  return { start: item.start, end: shifted > item.end ? shifted : item.end }
}

/** Просрочена: не завершена, а плановое окончание раньше сегодняшнего дня (сдвиг срока просрочку не отменяет). */
export function isOverdue(item, today) {
  return item.type === 'task' && (item.status || 'new') !== 'done' && Boolean(item.end) && item.end < today
}

/** «Свой период» для подписи: «01.11.2026 — 15.11.2026», «с 01.11.2026», «по 15.11.2026». */
export function periodLabel(period) {
  const range = periodRange('period', null, period)
  if (!range) return 'Свой период'
  if (range.from && range.to) return range.from === range.to ? formatDay(range.from) : `${formatDay(range.from)} — ${formatDay(range.to)}`
  return range.from ? `с ${formatDay(range.from)}` : `по ${formatDay(range.to)}`
}

// ---------- проверки по группам ----------

function matchesDate(item, option, env) {
  if (option === 'overdue') return isOverdue(item, env.today)
  if (option === 'shifted') return item.type === 'task' && Number(item.offset || 0) !== 0
  if (option === 'noDates') return !itemSpan(item)
  const range = periodRange(option, env.today, env.filters?.period)
  const span = range && itemSpan(item)
  return Boolean(span) && (!range.to || span.start <= range.to) && (!range.from || span.end >= range.from)
}

function matchesJira(item, option, jira) {
  if (!jira) return false
  if (option === 'linked') return Boolean(jira.linked(item))
  if (option === 'unlinked') return item.type === 'task' && !jira.linked(item)
  if (option === 'differs') return Boolean(jira.linked(item)) && Boolean(jira.differs(item))
  if (option.startsWith('category:')) return Boolean(jira.linked(item)) && jira.category(item) === option.slice(9)
  return false
}

function matchesTrait(item, option, env) {
  if (option === 'milestones') return item.type === 'milestone'
  if (option === 'comments') return Number(item.commentCount || 0) > 0
  if (option === 'dependencies') return Boolean(env.linked?.has(item.key))
  return false
}

const TESTS = {
  statuses: (item, value) => item.type === 'task' && (item.status || 'new') === value,
  // Исполнитель есть и у вех: «Только вехи» + исполнитель или «Только мои» — вехи этого исполнителя
  assignees: (item, value) => (item.type === 'task' || item.type === 'milestone') && (item.assigneeKey || NONE) === value,
  categories: (item, value) => item.type === 'task' && (item.category || NONE) === value,
  dates: (item, value, env) => matchesDate(item, value, env),
  jira: (item, value, env) => matchesJira(item, value, env.jira),
  traits: (item, value, env) => matchesTrait(item, value, env),
  mine: (item, _value, env) => (item.type === 'task' || item.type === 'milestone') && Boolean(item.assigneeKey)
    && Boolean(env.mine?.has(item.assigneeKey)),
}

/** Задачи со связью «предшественник» — и ведомые, и их предшественники. */
function dependencyKeys(items) {
  const keys = new Set(items.map((i) => i.key))
  const out = new Set()
  for (const item of items) {
    if (item.dependencyKey && item.dependencyKey !== item.key && keys.has(item.dependencyKey)) {
      out.add(item.key)
      out.add(item.dependencyKey)
    }
  }
  return out
}

/**
 * Действующие условия: [{ group, values }]. Не действуют пустые группы, «Свой период» без дат и
 * отметки Jira, пока Jira не подключена.
 */
function activeConditions(filters, ctx = {}) {
  const out = []
  for (const group of SET_GROUPS) {
    let values = [...(filters[group] || [])]
    if (group === 'dates') values = values.filter((v) => v !== 'period' || periodRange('period', ctx.today, filters.period))
    if (group === 'jira' && !ctx.jira) values = []
    if (values.length) out.push({ group, values })
  }
  if (filters.mine) out.push({ group: 'mine', values: [true] })
  return out
}

/** Проверка строки по всем условиям или null, если ни одно не действует. */
function compile(filters, ctx, items) {
  const conditions = activeConditions(filters, ctx)
  if (!conditions.length) return null
  const wantsLinks = conditions.some((c) => c.group === 'traits' && c.values.includes('dependencies'))
  const env = { ...ctx, filters, linked: wantsLinks ? dependencyKeys(items) : null }
  // «Только вехи» — ограничение «и», а не одно из «или» группы «Тип и прочее»: отбираются только вехи, и к ним
  // применяются остальные условия (вехи с комментариями, вехи исполнителя)
  const milestones = conditions.some((c) => c.group === 'traits' && c.values.includes('milestones'))
  const rest = milestones
    ? conditions.map((c) => (c.group === 'traits' ? { ...c, values: c.values.filter((v) => v !== 'milestones') } : c)).filter((c) => c.values.length)
    : conditions
  return {
    milestones,
    test: (item) => item.type === (milestones ? 'milestone' : 'task')
      && rest.every(({ group, values }) => values.some((value) => TESTS[group](item, value, env))),
  }
}

export function hasActiveFilters(filters, ctx = {}) {
  return Boolean((filters.search || '').trim()) || activeConditions(filters, ctx).length > 0
}

/** Подходит ли строка под отмеченные фильтры (поиск не учитывается); items — все строки плана для связей. */
export function itemPassesFilters(item, filters, ctx = {}, items = [item]) {
  const compiled = compile(filters, ctx, items)
  return compiled ? compiled.test(item) : true
}

/** Функция видимости строки или null, если ничего не отфильтровано. */
export function visibilityPredicate(index, filters, ctx = {}) {
  const search = (filters.search || '').trim().toLowerCase()
  const items = [...index.byKey.values()]
  const compiled = compile(filters, ctx, items)
  if (!search && !compiled) return null

  let searchVisible = null
  if (search) {
    searchVisible = new Set()
    for (const item of items) {
      if ((item.name || '').toLowerCase().includes(search)) {
        searchVisible.add(item.key)
        for (const a of ancestorKeys(index, item.key)) searchVisible.add(a)
      }
    }
  }
  let filterVisible = null
  if (compiled) {
    filterVisible = new Set()
    for (const item of items) {
      if (compiled.test(item)) {
        filterVisible.add(item.key)
        for (const a of ancestorKeys(index, item.key)) filterVisible.add(a)
      }
    }
  }
  return (item) => {
    if (filterVisible && !filterVisible.has(item.key)) {
      if (item.type !== 'milestone' || compiled.milestones) return false
      const parent = parentOf(index, item)
      if (parent && !filterVisible.has(parent.key)) return false
    }
    if (searchVisible && !searchVisible.has(item.key)) return false
    return true
  }
}

/**
 * Числа рядом с пунктами фильтра: сколько задач подходит под каждое значение (у «Только вехи» — сколько вех).
 * Считаются по всему плану, без учёта других отметок, — как у статусов в 1.0.1.
 */
export function filterCounts(items, filters = {}, ctx = {}) {
  const counts = { statuses: {}, assignees: {}, categories: {}, dates: {}, jira: {}, traits: {}, mine: 0 }
  const env = { ...ctx, filters, linked: dependencyKeys(items) }
  const bump = (group, value) => { counts[group][value] = (counts[group][value] || 0) + 1 }
  const jiraValues = [...JIRA_OPTIONS, ...JIRA_CATEGORY_OPTIONS].map((o) => o.value)
  for (const item of items) {
    if (item.type === 'milestone') bump('traits', 'milestones')
    if (item.type !== 'task') continue
    bump('statuses', item.status || 'new')
    bump('assignees', item.assigneeKey || NONE)
    bump('categories', item.category || NONE)
    for (const { value } of DATE_OPTIONS) if (matchesDate(item, value, env)) bump('dates', value)
    if (ctx.jira) for (const value of jiraValues) if (matchesJira(item, value, ctx.jira)) bump('jira', value)
    for (const value of ['comments', 'dependencies']) if (matchesTrait(item, value, env)) bump('traits', value)
    if (TESTS.mine(item, true, env)) counts.mine += 1
  }
  return counts
}

// ---------- «мои задачи» ----------

function normalizeName(value) {
  return String(value || '').toLowerCase().replace(/ё/g, 'е').split(/\s+/).filter(Boolean).join(' ')
}

/**
 * Исполнители плана, которые относятся к сотруднику: связанные с ним в окне «Исполнители» (userId), а не
 * связанные ни с кем — с тем же ФИО (так приходят исполнители из 1.0.x). Связанный с другим сотрудником
 * исполнитель-тёзка не считается.
 */
export function myAssigneeKeys(assignees, user) {
  const out = new Set()
  if (!user) return out
  const name = normalizeName(user.name)
  for (const a of assignees || []) {
    const mine = a.userId != null ? a.userId === user.id : Boolean(name) && normalizeName(a.name) === name
    if (mine) out.add(a.key)
  }
  return out
}

// ---------- отметки: переключение, подписи, сохранение ----------

export function toggleFilter(filters, group, value) {
  if (group === 'mine') { filters.mine = !filters.mine; return }
  const set = filters[group]
  if (set.has(value)) set.delete(value)
  else set.add(value)
}

/**
 * Отметки для строки фильтров под панелью и списка сохранённых: [{ group, value, label }].
 * assignees — исполнители плана (подписи по ключу).
 */
export function filterChips(filters, { assignees = [] } = {}) {
  const names = Object.fromEntries(assignees.map((a) => [a.key, a.name]))
  const chips = []
  if (filters.mine) chips.push({ group: 'mine', value: true, label: 'Мои задачи' })
  for (const group of SET_GROUPS) {
    for (const value of filters[group] || []) {
      let label
      if (group === 'statuses') label = STATUS_BY_KEY[value]?.label || value
      else if (group === 'assignees') label = value === NONE ? 'Без исполнителя' : names[value] || 'Исполнитель удалён'
      else if (group === 'categories') label = value === NONE ? 'Без категории' : value
      else if (group === 'dates' && value === 'period') label = `Свой период: ${periodLabel(filters.period).replace(/^Свой период$/, 'даты не заданы')}`
      else label = OPTION_LABELS[group]?.[value] || value
      chips.push({ group, value, label })
    }
  }
  return chips
}

/** Отметки без поиска — в JSON для сохранённого фильтра. */
export function serializeFilters(filters) {
  const out = {}
  for (const group of SET_GROUPS) if (filters[group]?.size) out[group] = [...filters[group]].sort()
  if (filters.dates?.has('period')) out.period = { from: filters.period?.from || '', to: filters.period?.to || '' }
  if (filters.mine) out.mine = true
  return out
}

/**
 * Применить сохранённые отметки: прежние снимаются, поиск остаётся. Множества меняются на месте —
 * так вид остаётся реактивным.
 */
export function applyFilters(filters, data = {}) {
  for (const group of SET_GROUPS) {
    if (!filters[group]) filters[group] = new Set()
    filters[group].clear()
    for (const value of Array.isArray(data[group]) ? data[group] : []) if (typeof value === 'string') filters[group].add(value)
  }
  const period = data.period || {}
  filters.period = { from: isValidDay(period.from) ? period.from : '', to: isValidDay(period.to) ? period.to : '' }
  filters.mine = data.mine === true
  return filters
}

/** Снять все отметки (и поиск, если search). */
export function clearFilters(filters, { search = true } = {}) {
  applyFilters(filters, {})
  if (search) filters.search = ''
}

/** Совпадают ли отметки — чтобы показать, какой сохранённый фильтр сейчас включён. */
export function sameFilters(a, b) {
  return JSON.stringify(serializeFilters(a)) === JSON.stringify(serializeFilters(b))
}

/** Сохранённые фильтры из localStorage: [{ name, filters }] — повреждённая запись даёт пустой список. */
export function parseSavedFilters(text) {
  try {
    const list = JSON.parse(text || '[]')
    return Array.isArray(list)
      ? list.filter((e) => e && typeof e.name === 'string' && e.name.trim() && e.filters && typeof e.filters === 'object')
      : []
  } catch {
    return []
  }
}

export const SAVED_FILTERS_LIMIT = 30

/** Сохранить под именем: фильтр с тем же именем (без учёта регистра) заменяется, новый — в начало списка. */
export function upsertSavedFilter(list, name, filters) {
  const clean = name.trim().slice(0, 80)
  const rest = list.filter((e) => e.name.toLowerCase() !== clean.toLowerCase())
  return [{ name: clean, filters: serializeFilters(filters) }, ...rest].slice(0, SAVED_FILTERS_LIMIT)
}

export function removeSavedFilter(list, name) {
  return list.filter((e) => e.name !== name)
}
