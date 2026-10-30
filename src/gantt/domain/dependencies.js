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
 */
export function computeDependencyShifts(items, { maxLoops = 30 } = {}) {
  const byKey = new Map()
  for (const item of items) byKey.set(item.key, { ...item })
  const changed = new Map()
  for (let loop = 0; loop < maxLoops; loop += 1) {
    let any = false
    for (const task of byKey.values()) {
      if (task.type !== 'task' || !task.dependencyKey) continue
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
      let ns = task.start
      let ne = task.end
      if (type === 'FS') ns = addDays(de, 1 + lag)
      if (type === 'SS') ns = addDays(ds, lag)
      if (type === 'FF') { ne = addDays(de, lag); ns = addDays(ne, -(dur - 1)) }
      if (type === 'SF') { ne = addDays(ds, lag); ns = addDays(ne, -(dur - 1)) }
      const parent = task.parentKey ? byKey.get(task.parentKey) : null
      if (parent && parent.type === 'task' && parent.start && parent.end) {
        if (ns < parent.start) ns = parent.start
        if (ne > parent.end) ne = parent.end
      }
      const end = type === 'FF' || type === 'SF' ? ne : addDays(ns, dur - 1)
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
