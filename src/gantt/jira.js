import { reactive } from 'vue'
import { api } from '../api/client'
import { localStorageGet, localStorageSet } from '../stores/ui'
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
 * Что можно перенести из задачи Jira в строку: [{ field, label, from, to, fields, note? }].
 * Без данных в Jira поле не предлагается. «Заблокирована» меняется только на «Завершена»: блокировка в Jira
 * обычно не отдельная категория статуса. Если сроки задачи задаёт связь (автосдвиг), из Jira берётся только
 * та дата, которую связь не определяет: при FS/SS — окончание, при FF/SF — начало.
 */
export function jiraChanges(item, issue, { calendar, today, linked = false } = {}) {
  if (!item || !issue) return []
  const out = []
  if (issue.summary && issue.summary !== item.name) {
    out.push({ field: 'name', label: 'Название', from: item.name, to: issue.summary, fields: { name: issue.summary } })
  }
  if (item.type === 'task') {
    const status = STATUS_FROM_CATEGORY[issue.statusCategory]
    const current = item.status || 'new'
    if (status && status !== current && !(current === 'blocked' && status !== 'done')) {
      const fields = { status }
      if (status === 'done') {
        const done = issue.resolved || today
        Object.assign(fields, { completedDate: done, offset: diffDays(item.start, done) - diffDays(item.start, item.end), progress: 100 })
      } else if (current === 'done') {
        Object.assign(fields, { completedDate: null, offset: 0 })
      }
      out.push({ field: 'status', label: 'Статус', from: current, to: status, fields, jiraStatus: issue.status })
    }
    const endDriven = linked && ['FF', 'SF'].includes(item.dependencyType)
    const start = linked && !endDriven ? item.start : issue.start || item.start
    const end = endDriven ? item.end : issue.due || item.end
    if ((start !== item.start || end !== item.end) && start <= end) {
      const fields = { start, end }
      if (item.endMode === 'duration') {
        fields.duration = item.durationType === 'working' && calendar ? calendar.countWorkingDays(start, end) : diffDays(start, end) + 1
      }
      const note = linked ? (endDriven ? 'окончание задаёт связь' : 'начало задаёт связь') : null
      out.push({ field: 'dates', label: 'Сроки', from: [item.start, item.end], to: [start, end], fields, note })
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
    // По задачам, запрошенным отдельно: почему не получены и когда получены
    failed: {}, fetchedAtByKey: {},
  })
  let loadSeq = 0
  const inFlight = new Set()
  let reloadTimer = null

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
      const applied = autoApply()
      if (refresh && !res.error && !applied) ui.success(`Данные Jira обновлены (${Object.keys(res.issues).length})`)
      else if (refresh && res.error) ui.error(res.error)
    } catch (err) {
      if (seq === loadSeq) { state.error = err.message; if (refresh) ui.error(err) }
    } finally {
      if (seq === loadSeq) state.loading = false
    }
  }

  /**
   * Задачи по ключам — сразу после того, как строку привязали (сам сотрудник или коллега). Ключ, который уже
   * запрашивается, повторно не запрашивается. Сотрудник с доступом «просмотр» поиск Jira не вызывает — для него
   * перечитываются задачи плана (сервер знает ссылку: она пришла с изменениями коллеги).
   */
  async function fetchKeys(keys, { refresh = false } = {}) {
    if (!state.connected) return
    if (editor.state.access === 'view') {
      clearTimeout(reloadTimer)
      reloadTimer = setTimeout(() => load({ refresh }), refresh ? 0 : 800)
      return
    }
    const wanted = keys.filter((k) => !inFlight.has(k) && (refresh || !(k in state.issues) || state.failed[k]))
    if (!wanted.length) return
    for (const key of wanted) inFlight.add(key)
    try {
      const res = await api.post('/jira/search', { planId: editor.state.plan.id, keys: wanted.join(' '), refresh })
      const next = { ...state.issues }
      const failed = { ...state.failed }
      const now = new Date().toISOString()
      for (const issue of res.issues) { next[issue.key] = issue; delete failed[issue.key]; state.fetchedAtByKey[issue.key] = now }
      for (const key of res.missing) { next[key] = null; delete failed[key]; state.fetchedAtByKey[key] = now }
      // Jira не ответила: задачи без сохранённых данных помечаются, карточка предложит повторить
      for (const key of wanted) if (!(key in next) && res.error) failed[key] = res.error
      state.issues = next
      state.failed = failed
      autoApply(wanted)
    } catch (err) {
      const failed = { ...state.failed }
      for (const key of wanted) if (!(key in state.issues) || refresh) failed[key] = err.message
      state.failed = failed
      if (refresh) ui.error(err)
    } finally {
      for (const key of wanted) inFlight.delete(key)
    }
  }

  function changesFor(item) {
    // Сроки задачи со связью задаёт автосдвиг — из Jira берётся только свободная дата
    const linked = !!(item?.dependencyKey && editor.state.items[item.dependencyKey] && editor.settings.display.autoShift)
    return jiraChanges(item, issueFor(item), { calendar: editor.calendar, today: editor.state.today, linked })
  }

  /** Строки, которые расходятся с Jira не только названием: названия в плане часто короче темы в Jira. */
  function differs(item) {
    return changesFor(item).some((c) => c.field !== 'name')
  }

  /**
   * Перенести выбранные изменения: [{ key, fields }] — одна правка, отменяется целиком. Сроки из Jira, которые
   * связь не задаёт (окончание последующей задачи), автосдвиг не перезаписывает, даже если в той же правке
   * сдвинулся предшественник. auto — автоперенос: не отнимает у сотрудника «Повторить».
   */
  function apply(list, label = 'Данные из Jira', { auto = false } = {}) {
    // Сдвиг завершённой задачи редактор пересчитывает сам от итоговых сроков и даты завершения
    const merged = new Map()
    for (const { key, fields } of list) merged.set(key, { ...(merged.get(key) || {}), ...fields })
    for (const fields of merged.values()) if (fields.status === undefined || fields.status === 'done') delete fields.offset
    editor.updateItems([...merged].map(([key, fields]) => ({ key, fields })), label, { keepFreeDates: true, keepRedo: auto })
  }

  /** Поля, которые план переносит из Jira сам (настройка плана «Автоперенос из Jira»). */
  function autoFields() {
    return editor.settings.jiraAuto || []
  }

  // Уже перенесённое автоматически в этом браузере (по плану): отменённый Ctrl+Z перенос не повторяется ни при
  // обновлении, ни после перезагрузки, а правка, которую сразу изменил автосдвиг или коллега, не повторяется
  // при каждом обновлении — расхождение остаётся видно в «Сверке с Jira». Новое значение в Jira — новая отметка.
  const AUTO_DONE_LIMIT = 2000
  let autoDone = null
  function autoDoneKey() {
    return `puls.jiraAuto.${editor.state.plan?.key || editor.state.plan?.id}`
  }
  function autoDoneSet() {
    if (!autoDone) {
      let saved = []
      try { saved = JSON.parse(localStorageGet(autoDoneKey()) || '[]') } catch { saved = [] }
      autoDone = new Set(Array.isArray(saved) ? saved.filter((m) => typeof m === 'string') : [])
    }
    return autoDone
  }
  function rememberAutoDone(marks) {
    const set = autoDoneSet()
    for (const mark of marks) set.add(mark)
    const list = [...set].slice(-AUTO_DONE_LIMIT)
    autoDone = new Set(list)
    localStorageSet(autoDoneKey(), JSON.stringify(list))
  }

  /**
   * Автоперенос: при открытии плана, обновлении из Jira и привязке строки выбранные в настройке поля переносятся
   * из Jira одной правкой (отменяется Ctrl+Z, видна в истории как «Из Jira автоматически»). Только у того, кто
   * может править план. keys — только эти задачи Jira (после привязки строк). Возвращает число строк.
   */
  function autoApply(keys = null) {
    const fields = autoFields()
    if (!fields.length || !editor.canEdit) return 0
    const only = keys ? new Set(keys) : null
    const list = []
    const marks = []
    const done = autoDoneSet()
    for (const item of linkedItems()) {
      const key = keyFor(item)
      if (only && !only.has(key)) continue
      if (!issueFor(item)) continue
      for (const change of changesFor(item)) {
        // Срок вехи («Дата») переносится вместе со сроками задач
        const setting = change.field === 'date' ? 'dates' : change.field
        if (!fields.includes(setting)) continue
        const mark = `${item.key}:${change.field}:${JSON.stringify(change.to)}`
        if (done.has(mark)) continue
        marks.push(mark)
        list.push({ key: item.key, fields: change.fields })
      }
    }
    if (!list.length) return 0
    rememberAutoDone(marks)
    const count = new Set(list.map((c) => c.key)).size
    apply(list, `Из Jira автоматически (${count})`, { auto: true })
    ui.success(`Из Jira перенесено в строки: ${count}. Отменить — Ctrl+Z; выключить — в «Сверке с Jira»`)
    return count
  }

  /** Включить или выключить автоперенос полей (одна правка настроек плана). */
  function setAutoFields(list) {
    editor.updatePlan({ settings: { jiraAuto: list } }, list.length ? 'Автоперенос из Jira' : 'Автоперенос из Jira выключен')
    if (list.length) autoApply()
  }

  return reactive({
    state, init, load, fetchKeys, keyFor, issueFor, linkedItems, changesFor, differs, apply, autoFields, autoApply,
    setAutoFields,
  })
}
