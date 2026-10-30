import { reactive } from 'vue'
import { api } from '../api/client'
import { diffDays } from './domain/dates'

/**
 * Задачи Jira строк плана (ADR-0007): статус, исполнитель, сроки и прогресс из Jira рядом с данными строки,
 * сверка и перенос данных из Jira одной отменяемой правкой. Подключение к Jira одно на систему.
 */

export const ISSUE_KEY_RE = /^[A-Z][A-Z0-9_]*-\d+$/
export const STATUS_FROM_CATEGORY = { new: 'new', indeterminate: 'inwork', done: 'done' }
export const CATEGORY_LABELS = { new: 'К выполнению', indeterminate: 'В работе', done: 'Готово' }

/** Ключ задачи из ссылки на подключённую Jira (те же правила, что на сервере). */
export function issueKeyFromUrl(url, baseUrl) {
  if (!url || !baseUrl) return null
  let link
  let base
  try { link = new URL(url.trim()); base = new URL(baseUrl) } catch { return null }
  if (link.host.toLowerCase() !== base.host.toLowerCase()) return null
  const basePath = base.pathname.replace(/\/+$/, '')
  if (basePath && !(link.pathname === basePath || link.pathname.startsWith(`${basePath}/`))) return null
  const selected = (link.searchParams.get('selectedIssue') || '').toUpperCase()
  if (ISSUE_KEY_RE.test(selected)) return selected
  const match = /\/(?:browse|issues)\/([A-Za-z][A-Za-z0-9_]*-\d+)\/?$/.exec(link.pathname)
  return match ? match[1].toUpperCase() : null
}

/** Что ввёл сотрудник в поле «Задача в Jira»: ключ ABC-123 превращается в ссылку на подключённую Jira. */
export function jiraLinkFromInput(value, baseUrl) {
  const text = (value || '').trim()
  if (!text) return { url: null }
  if (ISSUE_KEY_RE.test(text.toUpperCase()) && !/^https?:/i.test(text)) {
    if (!baseUrl) return { error: 'Jira не подключена — вставьте ссылку на задачу целиком' }
    return { url: `${baseUrl.replace(/\/+$/, '')}/browse/${text.toUpperCase()}` }
  }
  if (!/^https?:\/\/\S+$/i.test(text)) return { error: 'Укажите ключ задачи (ABC-123) или ссылку, начинающуюся с http:// или https://' }
  return { url: text }
}

/**
 * Что можно перенести из задачи Jira в строку: [{ field, label, from, to, fields }].
 * Без данных в Jira поле не предлагается; «Заблокирована» не меняется на «В работе».
 */
export function jiraChanges(item, issue, { calendar, today } = {}) {
  if (!item || !issue) return []
  const out = []
  if (issue.summary && issue.summary !== item.name) {
    out.push({ field: 'name', label: 'Название', from: item.name, to: issue.summary, fields: { name: issue.summary } })
  }
  if (item.type === 'task') {
    const status = STATUS_FROM_CATEGORY[issue.statusCategory]
    const current = item.status || 'new'
    if (status && status !== current && !(current === 'blocked' && status === 'inwork')) {
      const fields = { status }
      if (status === 'done') {
        const done = issue.resolved || today
        Object.assign(fields, { completedDate: done, offset: diffDays(item.start, done) - diffDays(item.start, item.end), progress: 100 })
      } else if (current === 'done') {
        Object.assign(fields, { completedDate: null, offset: 0 })
      }
      out.push({ field: 'status', label: 'Статус', from: current, to: status, fields, jiraStatus: issue.status })
    }
    const start = issue.start || item.start
    const end = issue.due || item.end
    if ((start !== item.start || end !== item.end) && start <= end) {
      const fields = { start, end }
      if (item.endMode === 'duration') {
        fields.duration = item.durationType === 'working' && calendar ? calendar.countWorkingDays(start, end) : diffDays(start, end) + 1
      }
      out.push({ field: 'dates', label: 'Сроки', from: [item.start, item.end], to: [start, end], fields })
    }
    const progress = out.some((c) => c.fields.progress === 100) ? null : issue.progress
    if (progress !== null && progress !== undefined && progress !== (item.progress || 0) && item.status !== 'done') {
      out.push({ field: 'progress', label: 'Прогресс', from: item.progress || 0, to: progress, fields: { progress } })
    }
  } else if (item.type === 'milestone' && issue.due && issue.due !== item.date) {
    out.push({ field: 'date', label: 'Дата', from: item.date, to: issue.due, fields: { date: issue.due } })
  }
  return out
}

export function createJira(editor, ui) {
  const state = reactive({
    connected: false, baseUrl: null, hasStartField: false,
    issues: {}, error: null, fetchedAt: null, loading: false, loaded: false,
  })
  let loadSeq = 0

  async function init() {
    try {
      Object.assign(state, await api.get('/jira'))
    } catch { state.connected = false }
    if (state.connected) await load()
  }

  function keyFor(item) {
    return state.connected ? issueKeyFromUrl(item?.jiraUrl, state.baseUrl) : null
  }

  /** Данные задачи строки: undefined — нет ссылки на Jira или ещё не загружено, null — нет в Jira или нет прав. */
  function issueFor(item) {
    const key = keyFor(item)
    return key ? state.issues[key] : undefined
  }

  function linkedItems() {
    return editor.itemList.filter((item) => keyFor(item))
  }

  async function load({ refresh = false } = {}) {
    if (!state.connected) return
    const seq = ++loadSeq
    state.loading = true
    try {
      const res = await api.get(`/plans/${editor.state.plan.id}/jira`, refresh ? { refresh: 1 } : undefined)
      if (seq !== loadSeq) return
      state.connected = res.connected
      state.issues = { ...state.issues, ...res.issues }
      state.error = res.error
      state.fetchedAt = res.fetchedAt
      state.loaded = true
      if (refresh && !res.error) ui.success(`Данные Jira обновлены (${Object.keys(res.issues).length})`)
      else if (refresh && res.error) ui.error(res.error)
    } catch (err) {
      if (seq === loadSeq) { state.error = err.message; if (refresh) ui.error(err) }
    } finally {
      if (seq === loadSeq) state.loading = false
    }
  }

  /** Задача по ключу — сразу после того, как сотрудник привязал строку (план ещё не сохранён на сервере). */
  async function fetchKeys(keys, { refresh = false } = {}) {
    const wanted = keys.filter((k) => refresh || !(k in state.issues))
    if (!state.connected || !wanted.length || editor.state.access === 'view') return
    try {
      const res = await api.post('/jira/search', { keys: wanted.join(' '), refresh })
      const next = { ...state.issues }
      for (const issue of res.issues) next[issue.key] = issue
      for (const key of res.missing) next[key] = null
      state.issues = next
    } catch (err) {
      if (refresh) ui.error(err)
    }
  }

  function changesFor(item) {
    return jiraChanges(item, issueFor(item), { calendar: editor.calendar, today: editor.state.today })
  }

  /** Перенести выбранные изменения: [{ key, fields }] — одна правка, отменяется целиком. */
  function apply(list, label = 'Данные из Jira') {
    const merged = new Map()
    for (const { key, fields } of list) merged.set(key, { ...(merged.get(key) || {}), ...fields })
    for (const [key, fields] of merged) {
      // Сдвиг завершения считается от сроков, которые будут у строки после переноса
      const item = editor.state.items[key]
      if (item && fields.completedDate) {
        const start = fields.start ?? item.start
        fields.offset = diffDays(start, fields.completedDate) - diffDays(start, fields.end ?? item.end)
      }
    }
    editor.updateItems([...merged].map(([key, fields]) => ({ key, fields })), label)
  }

  return reactive({ state, init, load, fetchKeys, keyFor, issueFor, linkedItems, changesFor, apply })
}
