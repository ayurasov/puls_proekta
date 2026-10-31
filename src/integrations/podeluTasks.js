import { computed, reactive } from 'vue'
import { api } from '../api/client'

/**
 * Задачи «По Делу», которые ссылаются на проект «Пульса» и строки его планов (ADR-0010).
 *
 * Включено ли — GET /api/podelu/links ({ enabled, baseUrl }); задачи проекта — GET /api/projects/<id>/podelu-tasks
 * (сервер «Пульса» читает их у «По Делу» по ключу и отдаёт только сотрудникам с доступом к проекту).
 *
 *   const tasks = createPodeluTasks(projectId); await tasks.load()
 *   tasks.forItem(planId, itemKey)   // задачи, связанные со строкой плана
 *   tasks.openCount                  // незавершённые задачи проекта
 */

let statusPending = null

/** Включён ли показ задач «По Делу» (один запрос на страницу). */
export function loadPodeluLinks({ force = false } = {}) {
  if (!statusPending || force) {
    statusPending = api.get('/podelu/links').catch(() => {
      statusPending = null
      return { enabled: false, baseUrl: null }
    })
  }
  return statusPending
}

/** Забыть состояние (администратор изменил настройки). */
export function resetPodeluLinks() {
  statusPending = null
}

/** Сначала открытые, по сроку (без срока — в конце), затем завершённые. */
export function sortTasks(tasks) {
  return [...tasks].sort((a, b) => (Number(a.done) - Number(b.done))
    || ((a.due || '9999-12-31') < (b.due || '9999-12-31') ? -1 : (a.due || '9999-12-31') > (b.due || '9999-12-31') ? 1 : 0)
    || String(a.title).localeCompare(String(b.title), 'ru'))
}

/**
 * Задачи по строкам плана: [{ itemKey, itemName, planId, tasks }]; задачи, связанные с проектом целиком, — первой
 * группой (itemKey: null). Название строки — из плана (items: { ключ: строка }), иначе сохранённое в «По Делу».
 */
export function groupByItem(tasks, items = {}) {
  const groups = new Map()
  for (const task of sortTasks(tasks)) {
    const id = task.itemKey ? `${task.planId || ''}:${task.itemKey}` : ''
    if (!groups.has(id)) {
      groups.set(id, { itemKey: task.itemKey || null, planId: task.planId || null, itemName: task.itemKey ? (items[task.itemKey]?.name || task.itemName || 'Строка плана') : null, tasks: [] })
    }
    groups.get(id).tasks.push(task)
  }
  return [...groups.values()].sort((a, b) => (a.itemKey ? 1 : 0) - (b.itemKey ? 1 : 0))
}

/** Просрочена ли незавершённая задача. */
export function isOverdue(task, today) {
  return !task.done && !!task.due && task.due < today
}

export function createPodeluTasks(projectId) {
  const state = reactive({ enabled: false, loaded: false, loading: false, tasks: [], error: null, truncated: false })
  let seq = 0

  async function load() {
    const status = await loadPodeluLinks()
    state.enabled = !!status.enabled
    if (!state.enabled) { state.loaded = true; return }
    const current = ++seq
    state.loading = true
    try {
      const res = await api.get(`/projects/${projectId}/podelu-tasks`)
      if (current !== seq) return
      state.enabled = res.enabled
      state.tasks = res.tasks || []
      state.truncated = !!res.truncated
      state.error = res.error || null
    } catch (err) {
      if (current === seq) state.error = err.message
    } finally {
      if (current === seq) { state.loading = false; state.loaded = true }
    }
  }

  function forItem(planId, key) {
    return sortTasks(state.tasks.filter((t) => t.itemKey === key && (!t.planId || !planId || t.planId === planId)))
  }

  const openCount = computed(() => state.tasks.filter((t) => !t.done).length)
  return reactive({ state, load, forItem, openCount })
}
