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

/** Средний прогресс плана по его сводке (stats плана в списке проектов). */
export function planProgress(stats) {
  if (!stats?.total) return 0
  return stats.progress ?? Math.round((stats.done / stats.total) * 100)
}

function statsState(s) {
  if (!s?.total) return 'empty'
  if (s.overdue > 0) return 'overdue'
  if (s.done === s.total || planProgress(s) >= 100) return 'done'
  return 'ontrack'
}

/**
 * Состояние проекта: 'overdue', 'done', 'ontrack' или 'empty' (задач нет). С 0.6.1 — по всем открытым планам:
 * просрочена задача в любом плане — проект «просрочен»; «завершён» — когда завершены все планы с задачами.
 */
export function projectState(project) {
  const plans = (project.plans || []).filter((p) => p.stats)
  if (!plans.length) return statsState(project.stats)
  const states = plans.map((p) => statsState(p.stats)).filter((s) => s !== 'empty')
  if (!states.length) return 'empty'
  if (states.includes('overdue')) return 'overdue'
  if (states.every((s) => s === 'done')) return 'done'
  return 'ontrack'
}

/**
 * Планы для карточки проекта (0.6.1): по порядку вкладок, не больше limit; если планов больше — сначала
 * отбрасываются пустые. { shown: [{ id, title, stats }], hidden: число непоказанных }.
 */
export function cardPlans(project, limit = 3) {
  const plans = (project.plans || []).filter((p) => p.stats)
  if (plans.length <= limit) return { shown: plans, hidden: 0 }
  const withTasks = plans.filter((p) => p.stats.total)
  const keep = new Set((withTasks.length >= limit ? withTasks : [...withTasks, ...plans.filter((p) => !p.stats.total)]).slice(0, limit).map((p) => p.id))
  return { shown: plans.filter((p) => keep.has(p.id)), hidden: plans.length - keep.size }
}

/** Ближайшая веха по всем открытым планам: { name, date, planTitle } или null. */
export function nearestMilestone(project) {
  const plans = (project.plans || []).filter((p) => p.stats?.nextMilestone)
  if (!plans.length) return project.stats?.nextMilestone ? { ...project.stats.nextMilestone, planTitle: null } : null
  const best = plans.reduce((a, b) => (b.stats.nextMilestone.date < a.stats.nextMilestone.date ? b : a))
  return { ...best.stats.nextMilestone, planTitle: plans.length > 1 || project.plans.length > 1 ? best.title : null }
}

/**
 * «Мои» — проекты, которые сотрудник создал или может редактировать. Администратор может редактировать всё,
 * поэтому для него «Мои» — созданные им.
 */
export function isMyProject(project, user) {
  if (!user) return false
  if (project.createdById === user.id) return true
  return user.role !== 'admin' && ['edit', 'manage'].includes(project.access)
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
