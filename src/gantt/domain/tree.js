import { addDays, diffDays, maxDay, minDay } from './dates'

/**
 * Дерево строк плана. Родитель строки — parentKey (группа или задача); порядок среди
 * братьев — position. Строка с несуществующим родителем считается корневой.
 */
export function buildIndex(items) {
  const byKey = new Map()
  for (const item of items) byKey.set(item.key, item)
  const children = new Map()
  for (const item of items) {
    const parent = item.parentKey && byKey.has(item.parentKey) && item.parentKey !== item.key ? item.parentKey : null
    if (!children.has(parent)) children.set(parent, [])
    children.get(parent).push(item)
  }
  for (const list of children.values()) list.sort(comparePosition)
  return { byKey, children, roots: children.get(null) || [] }
}

export function comparePosition(a, b) {
  return (a.position - b.position) || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0)
}

export function parentOf(index, item) {
  const key = item?.parentKey
  return key && index.byKey.has(key) && key !== item.key ? index.byKey.get(key) : null
}

/** Видимые строки в порядке вывода: [{ item, depth, hasChildren }]. */
export function flattenTree(index, { collapsed = new Set(), expandAll = false, isVisible = null } = {}) {
  const out = []
  const seen = new Set()
  const walk = (item, depth) => {
    if (seen.has(item.key)) return
    seen.add(item.key)
    if (isVisible && !isVisible(item)) return
    const kids = index.children.get(item.key) || []
    out.push({ item, depth, hasChildren: kids.length > 0, childCount: kids.length })
    if (kids.length && (expandAll || !collapsed.has(item.key))) {
      for (const kid of kids) walk(kid, depth + 1)
    }
  }
  for (const root of index.roots) walk(root, 0)
  return out
}

export function ancestorKeys(index, key) {
  const out = []
  const seen = new Set([key])
  let node = index.byKey.get(key)
  while (node) {
    const parent = parentOf(index, node)
    if (!parent || seen.has(parent.key)) break
    out.push(parent.key)
    seen.add(parent.key)
    node = parent
  }
  return out
}

export function descendantKeys(index, key) {
  const out = new Set()
  const stack = [...(index.children.get(key) || [])]
  while (stack.length) {
    const item = stack.pop()
    if (out.has(item.key)) continue
    out.add(item.key)
    stack.push(...(index.children.get(item.key) || []))
  }
  return out
}

export function isDescendant(index, key, possibleAncestor) {
  return ancestorKeys(index, key).includes(possibleAncestor)
}

/** Окончание задачи с учётом временного сдвига (+ просрочка, − ранняя сдача). */
export function shiftedEnd(task) {
  if (!task || task.type !== 'task') return task?.date || task?.end || ''
  const offset = Number(task.offset || 0)
  return offset ? addDays(task.end, offset) : task.end
}

/**
 * Сроки группы (и задачи с подзадачами) по всем вложенным задачам: начало, окончание без
 * сдвигов, окончание со сдвигами и сдвиг группы. В 1.0.x учитывались только прямые
 * дочерние задачи — группа из подгрупп не показывала полосу.
 */
export function groupSpan(index, key) {
  let start = null
  let end = null
  let shifted = null
  let count = 0
  let done = 0
  let progressSum = 0
  for (const childKey of descendantKeys(index, key)) {
    const item = index.byKey.get(childKey)
    if (!item || item.type !== 'task' || !item.start || !item.end) continue
    start = minDay(start, item.start)
    end = maxDay(end, item.end)
    shifted = maxDay(shifted, shiftedEnd(item))
    count += 1
    if (item.status === 'done') done += 1
    progressSum += Number(item.progress || 0)
  }
  if (!count) return null
  const offset = shifted > end ? diffDays(end, shifted) : shifted < end ? -diffDays(shifted, end) : 0
  return { start, end, shiftedEnd: shifted, offset, count, done, progress: Math.round(progressSum / count) }
}
