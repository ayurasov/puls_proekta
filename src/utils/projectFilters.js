/**
 * Фильтры и сортировка проектов на главной (0.4). Показатели — из project.stats (основной план проекта,
 * backend/app/api/projects.py: total, done, overdue, progress, start, end).
 * Выбор запоминается в браузере (у каждого сотрудника свой); перетаскивание карточек — только
 * в порядке по умолчанию без фильтров.
 */

export const PROJECT_SORTS = [
  { value: 'order', label: 'По порядку' },
  { value: 'title', label: 'По названию' },
  { value: 'progress', label: 'По прогрессу', hint: 'Сначала отстающие' },
  { value: 'end', label: 'По сроку окончания', hint: 'Сначала те, что заканчиваются раньше' },
]
export const PROJECT_STATES = [
  { value: 'overdue', label: 'Просрочены', hint: 'Есть незавершённые задачи с прошедшим сроком' },
  { value: 'ontrack', label: 'В срок', hint: 'Есть задачи, просроченных нет, не завершены' },
  { value: 'done', label: 'Завершены', hint: 'Прогресс 100 %: все задачи выполнены' },
]
export const DEFAULT_PROJECT_FILTERS = { direction: '', state: '', mine: false, sort: 'order' }

/** Сохранённый выбор, очищенный от устаревших и чужих значений. */
export function normalizeProjectFilters(saved) {
  const out = { ...DEFAULT_PROJECT_FILTERS }
  if (!saved || typeof saved !== 'object') return out
  if (typeof saved.direction === 'string' && (saved.direction === 'none' || /^\d+$/.test(saved.direction))) out.direction = saved.direction
  if (PROJECT_STATES.some((s) => s.value === saved.state)) out.state = saved.state
  if (PROJECT_SORTS.some((s) => s.value === saved.sort)) out.sort = saved.sort
  out.mine = saved.mine === true
  return out
}

/** Сужают ли список направление, состояние или «Мои» (сортировка не сужает). */
export function projectFiltersActive(filters) {
  return Boolean(filters.direction || filters.state || filters.mine)
}

/** Тот же показатель, что «Средний прогресс» в окне «Статистика» основного плана. */
export function projectProgress(project) {
  const s = project.stats
  if (!s || !s.total) return 0
  return s.progress ?? Math.round((s.done / s.total) * 100)
}

/** Состояние проекта: 'overdue', 'done', 'ontrack' или 'empty' (задач нет). */
export function projectState(project) {
  const s = project.stats
  if (!s?.total) return 'empty'
  if (s.overdue > 0) return 'overdue'
  if (s.done === s.total || projectProgress(project) >= 100) return 'done'
  return 'ontrack'
}

/** «Мои» — проекты, которые сотрудник создал или может редактировать. */
export function isMyProject(project, user) {
  if (!user) return false
  return project.createdById === user.id || ['edit', 'manage'].includes(project.access)
}

/** Направление группы на главной (null — «Без направления») подходит под выбранное. */
export function directionMatches(directionId, filter) {
  if (!filter) return true
  return filter === 'none' ? directionId == null : String(directionId) === filter
}

/** Состояние и «Мои» (направление — по группам, directionMatches). */
export function matchesProjectFilters(project, filters, user) {
  if (filters.state && projectState(project) !== filters.state) return false
  if (filters.mine && !isMyProject(project, user)) return false
  return true
}

const collator = new Intl.Collator('ru', { numeric: true, sensitivity: 'base' })
const byTitle = (a, b) => collator.compare(a.title, b.title)

/** Порядок внутри направления; проекты без задач (без прогресса и сроков) — в конце. */
export function sortProjects(list, sort) {
  if (sort === 'title') return [...list].sort(byTitle)
  if (sort === 'progress') {
    const key = (p) => (p.stats?.total ? projectProgress(p) : Infinity)
    return [...list].sort((a, b) => (key(a) - key(b)) || byTitle(a, b))
  }
  if (sort === 'end') {
    return [...list].sort((a, b) => {
      const x = a.stats?.end || ''
      const y = b.stats?.end || ''
      if (x !== y) return !x ? 1 : !y ? -1 : x < y ? -1 : 1
      return byTitle(a, b)
    })
  }
  return list
}
