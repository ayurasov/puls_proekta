import { ancestorKeys, parentOf } from './tree'

/**
 * Поиск и фильтры строк — та же логика, что в 1.0.1 (ADR-0006):
 * * поиск по названию показывает совпавшие строки и их предков;
 * * фильтры по категории, статусу и исполнителю отбирают задачи (внутри фильтра —
 *   «или», между фильтрами — «и») и показывают их с цепочкой предков;
 * * веха — контекст: видна, если видна строка, к которой она привязана, или если она
 *   ни к чему не привязана;
 * * при активном поиске или фильтре свёрнутые группы раскрываются.
 * Значение '__none__' — «без категории» / «без исполнителя».
 */
export const NONE = '__none__'

export function hasActiveFilters(filters) {
  return Boolean((filters.search || '').trim()) || Boolean(filters.categories?.size) ||
    Boolean(filters.statuses?.size) || Boolean(filters.assignees?.size)
}

export function taskPassesFilters(task, filters) {
  if (filters.categories?.size && !filters.categories.has(task.category || NONE)) return false
  if (filters.statuses?.size && !filters.statuses.has(task.status || 'new')) return false
  if (filters.assignees?.size && !filters.assignees.has(task.assigneeKey || NONE)) return false
  return true
}

/** Функция видимости строки или null, если ничего не отфильтровано. */
export function visibilityPredicate(index, filters) {
  const search = (filters.search || '').trim().toLowerCase()
  const anyFilter = Boolean(filters.categories?.size || filters.statuses?.size || filters.assignees?.size)
  if (!search && !anyFilter) return null

  let searchVisible = null
  if (search) {
    searchVisible = new Set()
    for (const item of index.byKey.values()) {
      if ((item.name || '').toLowerCase().includes(search)) {
        searchVisible.add(item.key)
        for (const a of ancestorKeys(index, item.key)) searchVisible.add(a)
      }
    }
  }
  let filterVisible = null
  if (anyFilter) {
    filterVisible = new Set()
    for (const item of index.byKey.values()) {
      if (item.type === 'task' && taskPassesFilters(item, filters)) {
        filterVisible.add(item.key)
        for (const a of ancestorKeys(index, item.key)) filterVisible.add(a)
      }
    }
  }
  return (item) => {
    if (filterVisible && !filterVisible.has(item.key)) {
      if (item.type !== 'milestone') return false
      const parent = parentOf(index, item)
      if (parent && !filterVisible.has(parent.key)) return false
    }
    if (searchVisible && !searchVisible.has(item.key)) return false
    return true
  }
}
