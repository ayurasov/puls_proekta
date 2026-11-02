import { addDays, diffDays } from './dates'
import { createCalendar } from './calendar'
import { shiftedEnd } from './tree'

/**
 * Связи «предшественник → последующая» (ADR-0012, 5.1): { key, from, to, type, lag, lagKind }. У строки сколько
 * угодно предшественников, последующей может быть и веха. Лаг — в календарных днях или, при lagKind 'working',
 * в рабочих днях по календарю плана (отрицательный — опережение).
 *
 * Автосдвиг (как applyDependencies в 1.0.1, для одной связи — то же самое):
 *   FS — начало после окончания предшественника (+лаг);
 *   SS — начало вместе с началом предшественника (+лаг);
 *   FF — окончание вместе с окончанием предшественника (+лаг), длительность сохраняется;
 *   SF — окончание вместе с началом предшественника (+лаг).
 * Предшественников несколько — как в MS Project: связи FS/SS дают начало (наибольшее из них), FF/SF — окончание
 * (наибольшее); если есть и те и другие, задача начинается так рано, как позволяют все. Длительность сохраняется.
 * Веха-последующая встаёт на дату: FS — окончание предшественника (+лаг, при нулевом лаге — тот же день),
 * SS — его начало, FF и SF — окончание или начало предшественника (+лаг).
 * «Начало не ранее» (constraintType 'snet') — начало задачи или дата вехи не раньше constraintDate, в том числе
 * у строки без связей. Окончание предшественника берётся со сдвигом (offset), у завершённой задачи — дата
 * фактического завершения. Задача-подзадача не выходит за сроки родительской задачи.
 * Возвращает Map(key → { start, end } у задачи или { date } у вехи) только для изменившихся строк.
 *
 * links — связи плана; без них (документы и тесты до 0.7.0) берутся поля строк dependencyKey/Type/Lag.
 * calendar — календарь плана (createCalendar) для лагов в рабочих днях.
 * only — сдвигать только эти строки (зависящие от только что изменённых); остальные считаются неподвижными.
 * Строки, чьи связи замкнуты в цикл, не сдвигаются: цикл сдвигал бы их бесконечно.
 *
 * fixed — Map(key → { start?, end? }): даты, заданные той же правкой (перенос из Jira), которые связи не
 * определяют, — окончание, если все связи задачи FS/SS, начало — если все FF/SF. Такая задача растягивается или
 * сжимается, а не сдвигается целиком: иначе срок из Jira перезаписался бы сдвигом вслед за предшественником.
 */
export function computeDependencyShifts(items, { links = null, calendar = null, maxLoops = 30, only = null, fixed = null } = {}) {
  const byKey = new Map()
  for (const item of items) byKey.set(item.key, { ...item })
  const incoming = incomingLinks(links || linksFromItems(items), byKey)
  const cyclic = cyclicKeys(incoming)
  const cal = calendar || DEFAULT_CALENDAR
  const order = topologicalOrder(byKey, incoming, cyclic)
  const changed = new Map()
  for (let loop = 0; loop < maxLoops; loop += 1) {
    let any = false
    for (const key of order) {
      const row = byKey.get(key)
      if (only && !only.has(key)) continue
      const next = row.type === 'milestone'
        ? shiftMilestone(row, incoming.get(key) || [], byKey, cal)
        : shiftTask(row, incoming.get(key) || [], byKey, cal, fixed?.get(key))
      if (!next) continue
      Object.assign(row, next)
      changed.set(key, next)
      any = true
    }
    if (!any) break
  }
  return changed
}

const DEFAULT_CALENDAR = createCalendar({ useRuCalendar: false })

/** Окончание строки для её последующих: у завершённой задачи — дата завершения, иначе со сдвигом; у вехи — дата. */
export function effectiveEnd(item) {
  if (item?.type === 'milestone') return item.date || ''
  if (item?.type !== 'task') return ''
  return item.status === 'done' && item.completedDate ? item.completedDate : shiftedEnd(item)
}

/** Сроки предшественника для связей: начало и окончание (effectiveEnd). */
function predecessorDates(dep) {
  if (dep.type === 'milestone') return { ds: dep.date, de: dep.date }
  return { ds: dep.start, de: effectiveEnd(dep) }
}

/** Начало задачи из n рабочих дней, которая заканчивается в end (end входит в срок, если он рабочий). */
export function startForWorkingDays(calendar, end, n) {
  let remaining = Math.max(1, Math.round(Number(n) || 1))
  let day = end
  if (calendar.isWorkingDay(day)) remaining -= 1
  let guard = 0
  while (remaining > 0 && guard < 20000) {
    day = addDays(day, -1)
    if (calendar.isWorkingDay(day)) remaining -= 1
    guard += 1
  }
  return day
}

/** День, отстоящий от day на lag дней — календарных или рабочих. */
function withLag(day, lag, kind, calendar) {
  return kind === 'working' ? shiftWorkingDays(calendar, day, lag) : addDays(day, lag)
}

/** Сдвиг на n рабочих дней вперёд (n > 0) или назад (n < 0): считаются только рабочие дни. */
export function shiftWorkingDays(calendar, day, n) {
  let left = Math.abs(Math.round(Number(n) || 0))
  const step = n < 0 ? -1 : 1
  let guard = 0
  while (left > 0 && guard < 20000) {
    day = addDays(day, step)
    if (calendar.isWorkingDay(day)) left -= 1
    guard += 1
  }
  return day
}

/** Ограничения задачи по связям: наибольшее требуемое начало (FS/SS) и окончание (FF/SF). */
function linkBounds(links, byKey, calendar) {
  let start = null
  let end = null
  for (const link of links) {
    const dep = byKey.get(link.from)
    const { ds, de } = predecessorDates(dep)
    if (!ds || !de) continue
    const lag = Number(link.lag || 0)
    const type = link.type || 'FS'
    // FS: следующий день после окончания; с лагом в рабочих днях — следующий рабочий день (+ лаг)
    const value = type === 'FS' ? (link.lagKind === 'working' ? shiftWorkingDays(calendar, de, 1 + lag) : addDays(de, 1 + lag))
      : withLag(type === 'FF' ? de : ds, lag, link.lagKind, calendar)
    if (type === 'FS' || type === 'SS') start = maxDay(start, value)
    else end = maxDay(end, value)
  }
  return { start, end }
}

function snetDate(row) {
  return row.constraintType === 'snet' && row.constraintDate ? row.constraintDate : null
}

function shiftTask(task, links, byKey, calendar, pin) {
  if (task.type !== 'task' || !task.start || !task.end) return null
  const bounds = linkBounds(links, byKey, calendar)
  const snet = snetDate(task)
  if (bounds.start === null && bounds.end === null && !(snet && task.start < snet)) return null
  const dur = Math.max(1, diffDays(task.start, task.end) + 1)
  // Задача «по длительности» в рабочих днях сохраняет число рабочих дней, а не календарный размах
  const working = task.endMode === 'duration' && task.durationType === 'working'
  const workDays = working ? Math.max(1, Number(task.duration) || calendar.countWorkingDays(task.start, task.end)) : 0
  const endFrom = (start) => (working ? calendar.addWorkingDays(start, workDays) : addDays(start, dur - 1))
  const startFrom = (end) => (working ? startForWorkingDays(calendar, end, workDays) : addDays(end, -(dur - 1)))
  // Что задают связи: начало (все FS/SS), окончание (все FF/SF) или и то и другое
  let mode = bounds.end === null ? 'start' : bounds.start === null ? 'end' : 'both'
  let ns = task.start
  let ne = task.end
  if (bounds.start !== null && mode === 'start') ns = bounds.start
  if (mode === 'end') {
    ne = bounds.end
    ns = pin?.start && pin.start <= ne ? pin.start : startFrom(ne)
  }
  if (mode === 'both') ns = maxDay(bounds.start, startFrom(bounds.end))
  if (snet && ns < snet) {
    // «Начало не ранее»: начало упирается в дату ограничения, длительность сохраняется
    ns = snet
    if (mode === 'end') mode = ne >= ns && pin?.start ? 'end' : 'start'
  }
  const parent = task.parentKey ? byKey.get(task.parentKey) : null
  if (parent && parent.type === 'task' && parent.start && parent.end) {
    if (ns < parent.start) ns = parent.start
    if (ne > parent.end) ne = parent.end
  }
  let end = mode === 'end' ? ne : mode === 'start' && pin?.end && pin.end >= ns ? pin.end : endFrom(ns)
  if (end < ns) {
    // FF/SF-подзадача упёрлась в окончание родителя: начало сдвигается назад, но не раньше родителя
    const back = startFrom(end)
    ns = parent && parent.type === 'task' && back < parent.start ? parent.start : back
    if (end < ns) end = ns
  }
  return ns !== task.start || end !== task.end ? { start: ns, end } : null
}

function shiftMilestone(milestone, links, byKey, calendar) {
  if (!milestone.date) return null
  let date = null
  for (const link of links) {
    const { ds, de } = predecessorDates(byKey.get(link.from))
    const base = link.type === 'SS' || link.type === 'SF' ? ds : de
    if (base) date = maxDay(date, withLag(base, Number(link.lag || 0), link.lagKind, calendar))
  }
  const snet = snetDate(milestone)
  if (date === null && !(snet && milestone.date < snet)) return null
  if (snet) date = maxDay(date, snet)
  return date !== milestone.date ? { date } : null
}

const maxDay = (a, b) => (!a ? b : !b ? a : a > b ? a : b)

/** Связи строк по последующей: Map(to → [связь]) — только между существующими задачами и вехами. */
function incomingLinks(links, byKey) {
  const incoming = new Map()
  for (const link of links || []) {
    if (!isLinkable(byKey.get(link.from)) || !isLinkable(byKey.get(link.to)) || link.from === link.to) continue
    if (!incoming.has(link.to)) incoming.set(link.to, [])
    incoming.get(link.to).push(link)
  }
  return incoming
}

function isLinkable(item) {
  return Boolean(item) && (item.type === 'task' || item.type === 'milestone')
}

/** Строки, лежащие на цикле связей (сильно связные компоненты больше одной строки), — алгоритм Тарьяна без рекурсии. */
function cyclicKeys(incoming) {
  const succ = new Map()
  for (const [to, list] of incoming) {
    for (const link of list) {
      if (!succ.has(link.from)) succ.set(link.from, [])
      succ.get(link.from).push(to)
    }
  }
  const index = new Map()
  const low = new Map()
  const onStack = new Set()
  const stack = []
  const cyclic = new Set()
  let counter = 0
  for (const root of succ.keys()) {
    if (index.has(root)) continue
    const work = [[root, 0]]
    index.set(root, counter); low.set(root, counter); counter += 1
    stack.push(root); onStack.add(root)
    while (work.length) {
      const frame = work[work.length - 1]
      const [node, i] = frame
      const next = (succ.get(node) || [])[i]
      if (next !== undefined) {
        frame[1] += 1
        if (!index.has(next)) {
          index.set(next, counter); low.set(next, counter); counter += 1
          stack.push(next); onStack.add(next)
          work.push([next, 0])
        } else if (onStack.has(next)) {
          low.set(node, Math.min(low.get(node), index.get(next)))
        }
        continue
      }
      work.pop()
      if (work.length) {
        const parent = work[work.length - 1][0]
        low.set(parent, Math.min(low.get(parent), low.get(node)))
      }
      if (low.get(node) === index.get(node)) {
        const component = []
        let member
        do {
          member = stack.pop()
          onStack.delete(member)
          component.push(member)
        } while (member !== node)
        if (component.length > 1) for (const k of component) cyclic.add(k)
      }
    }
  }
  return cyclic
}

/**
 * Порядок расчёта: предшественники раньше последующих (один проход сдвигает всю цепочку), строки цикла не
 * считаются; строки без связей — тоже в порядке (их держит «Начало не ранее» и рамка родительской задачи).
 */
function topologicalOrder(byKey, incoming, cyclic) {
  const succ = new Map()
  const waiting = new Map()
  for (const [to, list] of incoming) {
    if (cyclic.has(to)) continue
    let count = 0
    for (const link of list) {
      if (cyclic.has(link.from)) continue
      if (!succ.has(link.from)) succ.set(link.from, [])
      succ.get(link.from).push(to)
      count += 1
    }
    waiting.set(to, count)
  }
  const order = []
  const queue = [...byKey.keys()].filter((key) => !cyclic.has(key) && !waiting.get(key))
  for (let i = 0; i < queue.length; i += 1) {
    const key = queue[i]
    order.push(key)
    for (const next of succ.get(key) || []) {
      const left = waiting.get(next) - 1
      waiting.set(next, left)
      if (left === 0) queue.push(next)
    }
  }
  return order
}

/** Связи из полей строк до 0.7.0 (dependencyKey/Type/Lag): снимки старого формата и сценарии 1.0.1. */
export function linksFromItems(items) {
  const out = []
  for (const item of items || []) {
    if (item.type !== 'task' || !item.dependencyKey) continue
    out.push({
      key: `dep-${item.key}`, from: item.dependencyKey, to: item.key, type: item.dependencyType || 'FS',
      lag: Number(item.dependencyLag || 0), lagKind: 'calendar',
    })
  }
  return out
}

/**
 * Подсказки и ограничения дат в карточке задачи (как refreshTaskConstraints в 1.0.1): сроки родительской задачи,
 * требования каждой связи с учётом лага (hint.from — предшественник) и «Начало не ранее».
 */
export function dependencyConstraints(task, byKey, links = null, calendar = null) {
  let minStart = ''
  let maxStart = ''
  let minEnd = ''
  let maxEnd = ''
  const hints = []
  const parent = task.parentKey ? byKey.get(task.parentKey) : null
  if (parent && parent.type === 'task' && parent.start && parent.end) {
    minStart = parent.start
    maxStart = parent.end
    minEnd = parent.start
    maxEnd = parent.end
    hints.push({ kind: 'parent', from: parent.start, to: parent.end })
  }
  const cal = calendar || DEFAULT_CALENDAR
  const own = (links || linksFromItems([task])).filter((l) => l.to === task.key && isLinkable(byKey.get(l.from)))
  for (const link of own) {
    const { start, end } = linkBounds([link], byKey, cal)
    const pred = byKey.get(link.from)
    if (start) { minStart = maxDay(minStart, start); hints.push({ kind: 'start', value: start, name: pred.name }) }
    if (end) { minEnd = maxDay(minEnd, end); hints.push({ kind: 'end', value: end, name: pred.name }) }
  }
  const snet = snetDate(task)
  if (snet) { minStart = maxDay(minStart, snet); hints.push({ kind: 'snet', value: snet }) }
  return { minStart, maxStart, minEnd, maxEnd, hints }
}

/** Ключи строк, зависящих (прямо или через цепочку связей) от данной. */
export function dependentKeys(links, key) {
  const succ = new Map()
  for (const link of links || []) {
    if (!succ.has(link.from)) succ.set(link.from, [])
    succ.get(link.from).push(link.to)
  }
  const result = new Set()
  const stack = [key]
  while (stack.length) {
    for (const next of succ.get(stack.pop()) || []) {
      if (!result.has(next)) {
        result.add(next)
        stack.push(next)
      }
    }
  }
  result.delete(key)
  return result
}

/** Строка key вложена (на любой глубине) в строку ancestor. */
export function isNested(byKey, key, ancestor) {
  const seen = new Set([key])
  let node = byKey.get(key)
  while (node?.parentKey && !seen.has(node.parentKey)) {
    if (node.parentKey === ancestor) return true
    seen.add(node.parentKey)
    node = byKey.get(node.parentKey)
  }
  return false
}

/**
 * Почему связь from → to нельзя добавить (или изменить связь ignoreKey на такую): текст для сотрудника или ''.
 * Те же проверки, что на сервере: строки есть, это задачи или вехи, не сама на себя и не вложенная (родительская),
 * без повтора и цикла.
 */
export function linkProblem(links, byKey, from, to, ignoreKey = null) {
  const a = byKey.get(from)
  const b = byKey.get(to)
  if (!a || !b) return 'Строка связи удалена'
  if (!isLinkable(a) || !isLinkable(b)) return 'Связь возможна только между задачами и вехами'
  if (from === to) return 'Строка не может быть предшественником самой себя'
  // Как в MS Project: вложенная задача и так в сроках родительской, связь между ними выталкивала бы её из них
  if (isNested(byKey, from, to) || isNested(byKey, to, from)) return `Нельзя связать «${a.name}» и «${b.name}»: одна строка вложена в другую`
  const others = (links || []).filter((l) => l.key !== ignoreKey)
  if (others.some((l) => l.from === from && l.to === to)) return `Связь «${a.name}» → «${b.name}» уже есть`
  if (dependentKeys(others, to).has(from)) {
    return `Нельзя связать «${a.name}» → «${b.name}»: «${b.name}» уже предшествует «${a.name}» — получился бы цикл`
  }
  return ''
}
