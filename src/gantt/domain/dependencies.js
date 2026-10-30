import { addDays, diffDays } from './dates'
import { shiftedEnd } from './tree'

/**
 * Автосдвиг по связям «предшественник» (как applyDependencies в 1.0.1):
 *   FS — начало после окончания предшественника (+лаг);
 *   SS — начало вместе с началом предшественника (+лаг);
 *   FF — окончание вместе с окончанием предшественника (+лаг), длительность сохраняется;
 *   SF — окончание вместе с началом предшественника (+лаг).
 * Окончание предшественника берётся со сдвигом (offset), у завершённой задачи — дата
 * фактического завершения. Задача-подзадача не выходит за сроки родительской задачи.
 * Возвращает Map(key → { start, end }) только для изменившихся задач.
 *
 * only — сдвигать только эти задачи (зависящие от только что изменённых); остальные считаются
 * неподвижными. Задачи, чьи связи замкнуты в цикл, не сдвигаются: цикл сдвигал бы их бесконечно.
 *
 * fixed — Map(key → { start?, end? }): даты, заданные той же правкой (перенос из Jira), которые связь не
 * определяет, — окончание при FS/SS, начало при FF/SF. Такая задача растягивается или сжимается, а не
 * сдвигается целиком: иначе срок из Jira перезаписался бы сдвигом вслед за предшественником.
 */
export function computeDependencyShifts(items, { maxLoops = 30, only = null, fixed = null } = {}) {
  const byKey = new Map()
  for (const item of items) byKey.set(item.key, { ...item })
  const cyclic = cyclicKeys(byKey)
  const changed = new Map()
  for (let loop = 0; loop < maxLoops; loop += 1) {
    let any = false
    for (const task of byKey.values()) {
      if (task.type !== 'task' || !task.dependencyKey) continue
      if (cyclic.has(task.key) || (only && !only.has(task.key))) continue
      const dep = byKey.get(task.dependencyKey)
      if (!dep || dep.key === task.key || (dep.type !== 'task' && dep.type !== 'milestone')) continue
      const ds = dep.type === 'task' ? dep.start : dep.date
      const de = dep.type === 'task'
        ? (dep.status === 'done' && dep.completedDate ? dep.completedDate : shiftedEnd(dep))
        : dep.date
      if (!ds || !de || !task.start || !task.end) continue
      const dur = Math.max(1, diffDays(task.start, task.end) + 1)
      const lag = Number(task.dependencyLag || 0)
      const type = task.dependencyType || 'FS'
      const pin = fixed?.get(task.key)
      let ns = task.start
      let ne = task.end
      if (type === 'FS') ns = addDays(de, 1 + lag)
      if (type === 'SS') ns = addDays(ds, lag)
      if (type === 'FF' || type === 'SF') {
        ne = type === 'FF' ? addDays(de, lag) : addDays(ds, lag)
        ns = pin?.start && pin.start <= ne ? pin.start : addDays(ne, -(dur - 1))
      }
      const parent = task.parentKey ? byKey.get(task.parentKey) : null
      if (parent && parent.type === 'task' && parent.start && parent.end) {
        if (ns < parent.start) ns = parent.start
        if (ne > parent.end) ne = parent.end
      }
      let end = type === 'FF' || type === 'SF' ? ne : pin?.end && pin.end >= ns ? pin.end : addDays(ns, dur - 1)
      if (end < ns) {
        // FF/SF-подзадача упёрлась в окончание родителя: начало сдвигается назад, но не раньше родителя
        const back = addDays(end, -(dur - 1))
        ns = parent && parent.type === 'task' && back < parent.start ? parent.start : back
        if (end < ns) end = ns
      }
      if (ns !== task.start || end !== task.end) {
        task.start = ns
        task.end = end
        changed.set(task.key, { start: ns, end })
        any = true
      }
    }
    if (!any) break
  }
  return changed
}

/** Задачи, чья цепочка «предшественник» возвращается к ним самим. */
function cyclicKeys(byKey) {
  const cyclic = new Set()
  for (const start of byKey.keys()) {
    const seen = new Set([start])
    let node = byKey.get(start)?.dependencyKey
    while (node && byKey.has(node)) {
      if (node === start) { for (const k of seen) cyclic.add(k); break }
      if (seen.has(node)) break
      seen.add(node)
      node = byKey.get(node).dependencyKey
    }
  }
  return cyclic
}

/**
 * Подсказки и ограничения дат в окне задачи (как refreshTaskConstraints в 1.0.1):
 * сроки родительской задачи и требования связи с учётом лага.
 */
export function dependencyConstraints(task, byKey) {
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
  const dep = task.dependencyKey ? byKey.get(task.dependencyKey) : null
  if (dep && (dep.type === 'task' || dep.type === 'milestone')) {
    const ds = dep.type === 'task' ? dep.start : dep.date
    const de = dep.type === 'task' ? shiftedEnd(dep) : dep.date
    const lag = Number(task.dependencyLag || 0)
    const type = task.dependencyType || 'FS'
    const max = (a, b) => (!a ? b : !b ? a : a > b ? a : b)
    if (type === 'FS') { const v = addDays(de, 1 + lag); minStart = max(minStart, v); hints.push({ kind: 'start', value: v }) }
    if (type === 'SS') { const v = addDays(ds, lag); minStart = max(minStart, v); hints.push({ kind: 'start', value: v }) }
    if (type === 'FF') { const v = addDays(de, lag); minEnd = max(minEnd, v); hints.push({ kind: 'end', value: v }) }
    if (type === 'SF') { const v = addDays(ds, lag); minEnd = max(minEnd, v); hints.push({ kind: 'end', value: v }) }
  }
  return { minStart, maxStart, minEnd, maxEnd, hints }
}

/** Ключи задач, зависящих (прямо или через цепочку) от данной. */
export function dependentKeys(items, key) {
  const result = new Set()
  const stack = [key]
  while (stack.length) {
    const current = stack.pop()
    for (const item of items) {
      if (item.dependencyKey === current && !result.has(item.key)) {
        result.add(item.key)
        stack.push(item.key)
      }
    }
  }
  return result
}
