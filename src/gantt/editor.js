/**
 * Редактор плана (ADR-0006): состояние открытого плана, правки, отправка и синхронизация.
 *
 * * Правка сразу применяется на экране и становится операцией в очереди отправки.
 *   Очередь отправляется пакетами по одному; при обрыве связи пакеты ждут и повторяются,
 *   уход со страницы с неотправленными правками предупреждает.
 * * Раз в несколько секунд редактор забирает изменения коллег после известной ревизии и
 *   список тех, кто сейчас на плане. Строки, которые ждут отправки, не перезаписываются.
 * * «Отменить» / «Повторить» — обратные операции, отправляемые как обычные правки:
 *   отмена работает и после сохранения и не затирает чужие изменения других полей.
 */
import { computed, reactive, toRaw } from 'vue'
import { api, AuthRequiredError, NetworkError } from '../api/client'
import { applyBatch, touchedKeys } from './domain/ops'
import { buildIndex, descendantKeys, flattenTree, parentOf } from './domain/tree'
import { hasActiveFilters, visibilityPredicate } from './domain/filters'
import { computeDependencyShifts } from './domain/dependencies'
import { createCalendar } from './domain/calendar'
import { STEP, insertPosition } from './domain/positions'
import { addDays, diffDays, todayLocal } from './domain/dates'
import { localStorageGet, localStorageSet } from '../stores/ui'
import { countLabel } from '../utils/format'

const POLL_VISIBLE_MS = 4000
const POLL_HIDDEN_MS = 30000
const RETRY_STEPS_MS = [1000, 2000, 5000, 10000, 20000, 30000]
// Поля, от которых зависят сроки по связям: только их изменение запускает автосдвиг
const SCHEDULE_FIELDS = new Set(['start', 'end', 'date', 'dependencyKey', 'dependencyType', 'dependencyLag', 'offset',
  'status', 'completedDate', 'parentKey', 'duration', 'endMode', 'durationType'])

export const DEFAULT_DISPLAY = {
  useRuCalendar: true, showWeekends: true, showHolidays: true, showProgress: true, showNotes: true,
  autoShift: true, legendEnabled: false, legendPosition: 'bottom', legendMode: 'all', showTaskNamesOnBars: false,
  showOffsetLabels: true, showDependencies: true,
  tooltipFields: ['status', 'dates', 'progress', 'dependency', 'assignee', 'lastChange'],
}

export function randomKey(prefix) {
  const bytes = new Uint8Array(5)
  crypto.getRandomValues(bytes)
  return prefix + [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('')
}

// Редакторы, у которых есть неотправленные правки, — в том числе закрытых планов: закрытый план
// (переход на другую вкладку, другой проект, страницу входа) досылает очередь в фоне, пока открыта
// страница. Уход со страницы при неотправленных правках предупреждает (beforeunload).
const liveEditors = new Set()
export function hasUnsavedEdits() {
  for (const editor of liveEditors) if (editor.pending()) return true
  return false
}
if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', (event) => {
    if (!hasUnsavedEdits()) return
    event.preventDefault()
    event.returnValue = ''
  })
}

/** Ширина колонки названий по умолчанию — от ширины экрана: 160 px на телефоне, до 380 px на большом мониторе. */
export function defaultGridWidth(screenWidth = typeof window !== 'undefined' ? window.innerWidth : 1366) {
  return Math.round(Math.min(380, Math.max(160, screenWidth * 0.22)))
}
/** Сохранённая ширина не занимает на узком экране больше половины ширины. */
export function fitGridWidth(width, screenWidth = typeof window !== 'undefined' ? window.innerWidth : 1366) {
  return Math.round(Math.min(width, Math.max(160, screenWidth * 0.45)))
}

let calendarCache = null
async function loadCalendarDays() {
  if (!calendarCache) {
    calendarCache = api.get('/calendar').then((days) => ({
      holidays: days.filter((d) => d.kind === 'holiday').map((d) => d.date),
      workdays: days.filter((d) => d.kind === 'workday').map((d) => d.date),
      titles: Object.fromEntries(days.map((d) => [d.date, d.title])),
    })).catch((err) => { calendarCache = null; throw err })
  }
  return calendarCache
}

export function createPlanEditor({ planId, ui, onRemoteChange = () => {} }) {
  const state = reactive({
    planId,
    loading: true,
    loadError: null,
    plan: null,
    access: 'view',
    items: {},
    notes: {},
    assignees: {},
    graveyard: { items: {}, notes: {}, assignees: {} },
    users: {},
    revision: 0,
    queue: [],
    inFlight: null,
    offline: false,
    authLost: false,
    lastSavedAt: null,
    presence: [],
    undo: [],
    redo: [],
    calendarDays: { holidays: [], workdays: [], titles: {} },
    today: todayLocal(),
    remoteFlash: {},
    view: {
      scale: 'day', zoom: 1, gridWidth: 300, collapsed: new Set(), search: '',
      categories: new Set(), statuses: new Set(), assignees: new Set(),
      selectedKey: null, panelKey: null, columns: { start: false, end: false, duration: false, assignee: false, progress: false, jira: false },
    },
    previewSnapshot: null,
  })

  let pollTimer = null
  let retryTimer = null
  let retryStep = 0
  let todayTimer = null
  let stopped = false

  // ---------- вычисляемые ----------
  const settings = computed(() => {
    const s = state.plan?.settings || {}
    return { ...s, display: { ...DEFAULT_DISPLAY, ...(s.display || {}) }, range: s.range || { start: state.today, end: addDays(state.today, 180) } }
  })
  const range = computed(() => settings.value.range)
  const canEdit = computed(() => !state.previewSnapshot && ['edit', 'manage'].includes(state.access) && !state.plan?.isArchived)
  const source = computed(() => state.previewSnapshot || state)
  const itemList = computed(() => Object.values(source.value.items))
  const index = computed(() => buildIndex(itemList.value))
  const calendar = computed(() => createCalendar({
    holidays: state.calendarDays.holidays,
    workdays: state.calendarDays.workdays,
    useRuCalendar: settings.value.display.useRuCalendar,
  }))
  const filtersActive = computed(() => hasActiveFilters(state.view))
  const visibleRows = computed(() => {
    const predicate = visibilityPredicate(index.value, state.view)
    return flattenTree(index.value, { collapsed: state.view.collapsed, expandAll: Boolean(predicate), isVisible: predicate })
  })
  const assigneeList = computed(() => Object.values(source.value.assignees).sort((a, b) => a.position - b.position))
  const noteList = computed(() => Object.values(source.value.notes))
  const todayMarker = computed(() => {
    const t = settings.value.today || {}
    return t.auto === false && t.date ? t.date : state.today
  })
  const pendingCount = computed(() => state.queue.reduce((n, b) => n + b.ops.length, 0) + (state.inFlight ? state.inFlight.ops.length : 0))
  const categories = computed(() => {
    const list = [...(settings.value.categories || [])]
    for (const item of itemList.value) if (item.category && !list.includes(item.category)) list.push(item.category)
    return list
  })

  // ---------- вид (у каждого сотрудника свой) ----------
  function viewKey() {
    return `puls.view.${state.plan?.key || planId}`
  }
  function restoreView() {
    const s = state.plan?.settings || {}
    let saved = null
    try { saved = JSON.parse(localStorageGet(viewKey()) || 'null') } catch { saved = null }
    state.view.scale = saved?.scale || s.scale || 'day'
    state.view.zoom = saved?.zoom || s.zoom || 1
    state.view.gridWidth = fitGridWidth(saved?.gridWidth || Math.max(s.gridWidth || 0, defaultGridWidth()))
    state.view.collapsed = new Set(saved?.collapsed || s.defaultCollapsed || [])
    if (saved?.columns) state.view.columns = { ...state.view.columns, ...saved.columns }
  }
  function saveView() {
    localStorageSet(viewKey(), JSON.stringify({
      scale: state.view.scale, zoom: state.view.zoom, gridWidth: state.view.gridWidth,
      collapsed: [...state.view.collapsed], columns: state.view.columns,
    }))
  }

  // ---------- загрузка ----------
  function replaceCollection(name, list) {
    const map = {}
    for (const entity of list) map[entity.key] = entity
    state[name] = map
    state.graveyard[name] = {}
  }

  function applyDocument(doc) {
    state.plan = doc.plan
    state.access = doc.plan.access
    state.revision = doc.plan.revision
    state.users = doc.users || {}
    replaceCollection('items', doc.items)
    replaceCollection('notes', doc.notes)
    replaceCollection('assignees', doc.assignees)
  }

  /** resetHistory — после восстановления версии: прежние «Отменить» относятся к другому состоянию. */
  async function load({ keepView = false, resetHistory = false } = {}) {
    state.loading = !state.plan
    state.loadError = null
    try {
      const [doc, days] = await Promise.all([api.get(`/plans/${planId}`), loadCalendarDays().catch(() => null)])
      if (days) state.calendarDays = days
      applyDocument(doc)
      if (resetHistory) { state.undo = []; state.redo = [] }
      if (!keepView) restoreView()
    } catch (err) {
      state.loadError = err
      throw err
    } finally {
      state.loading = false
    }
  }

  // ---------- очередь отправки ----------
  // Каждая правка — отдельная запись очереди { ops, tags, label, undoRef }. В одном запросе уходят
  // несколько правок подряд (до 1500 операций); если сервер отклонил запрос, правки отправляются по
  // одной, и отбрасывается только та, что не прошла проверку, — остальные сохраняются.
  let isolate = false
  let resyncNeeded = false
  const tracker = { pending: () => state.queue.length > 0 || Boolean(state.inFlight) }

  function pendingTags() {
    const tags = new Set()
    for (const entry of state.queue) for (const tag of entry.tags) tags.add(tag)
    if (state.inFlight) for (const tag of state.inFlight.tags) tags.add(tag)
    return tags
  }

  function enqueue(ops, label, undoRef = null) {
    state.queue.push({ ops: [...ops], tags: touchedKeys(ops), label, undoRef })
    liveEditors.add(tracker)
    flushSoon()
  }

  let flushTimer = null
  function flushSoon() {
    clearTimeout(flushTimer)
    flushTimer = setTimeout(flush, 120)
  }

  function mergeEntities(name, entityName, list, skip) {
    if (!list) return []
    const changed = []
    for (const entity of list) {
      if (skip.has(`${entityName}:${entity.key}`)) continue
      // Ответ, пришедший позже более свежего (медленный опрос после собственного сохранения), не
      // откатывает строку к старому значению
      const current = state[name][entity.key] || state.graveyard[name][entity.key]
      if (current && entity.updatedRev != null && current.updatedRev != null && current.updatedRev > entity.updatedRev) continue
      if (entity.deleted) {
        if (state[name][entity.key]) {
          state.graveyard[name][entity.key] = state[name][entity.key]
          delete state[name][entity.key]
          changed.push(entity.key)
        } else {
          state.graveyard[name][entity.key] = entity
        }
      } else {
        const before = state[name][entity.key]
        state[name][entity.key] = entity
        delete state.graveyard[name][entity.key]
        if (!before || before.updatedRev !== entity.updatedRev) changed.push(entity.key)
      }
    }
    return changed
  }

  function takeBatch() {
    const entries = []
    let count = 0
    for (const entry of state.queue) {
      if (entries.length && (isolate || count + entry.ops.length > 1500)) break
      entries.push(entry)
      count += entry.ops.length
    }
    state.queue.splice(0, entries.length)
    const tags = new Set()
    for (const entry of entries) for (const tag of entry.tags) tags.add(tag)
    return { entries, ops: entries.flatMap((e) => e.ops), tags }
  }

  // Записи истории сравниваются по исходным объектам: в state они хранятся реактивными обёртками
  function dropUndo(ref) {
    if (!ref) return
    const raw = toRaw(ref)
    state.undo = state.undo.filter((e) => toRaw(e) !== raw)
    state.redo = state.redo.filter((e) => toRaw(e) !== raw)
  }

  function drained() {
    if (state.queue.length || state.inFlight) return
    isolate = false
    if (stopped) { liveEditors.delete(tracker); return }
    if (resyncNeeded) resync()
  }

  /** Вернуть на экран состояние сервера после отклонённой правки — когда очередь пуста. */
  async function resync() {
    resyncNeeded = false
    try {
      const doc = await api.get(`/plans/${planId}`)
      if (state.queue.length || state.inFlight) { resyncNeeded = true; return }
      applyDocument(doc)
    } catch {
      resyncNeeded = true
    }
  }

  async function flush() {
    if (state.inFlight || !state.queue.length) return
    clearTimeout(retryTimer)
    const batch = takeBatch()
    state.inFlight = batch
    try {
      const res = await api.post(`/plans/${planId}/ops`, { baseRevision: state.revision, ops: batch.ops })
      state.inFlight = null
      const skip = pendingTags()
      mergeEntities('items', 'item', res.items, skip)
      mergeEntities('notes', 'note', res.notes, skip)
      mergeEntities('assignees', 'assignee', res.assignees, skip)
      if (res.plan && !skip.has('plan')) {
        state.plan = { ...res.plan, access: state.access }
      }
      state.lastSavedAt = new Date()
      state.offline = false
      state.authLost = false
      retryStep = 0
      if (res.conflicts?.length && !stopped) {
        const names = [...new Set(res.conflicts.map((c) => c.byName).filter(Boolean))]
        const rows = res.conflicts.map((c) => state.items[c.key]?.name).filter(Boolean)
        ui.toast({
          title: 'Одновременная правка',
          body: `${names.join(', ') || 'Коллега'} только что менял(а) ${rows.length > 1 ? 'строки' : 'строку'} «${rows.slice(0, 2).join('», «')}». Ваши изменения сохранены поверх, прежние значения — в истории.`,
          kind: 'info', timeout: 8000,
        })
      }
      if (res.fixes?.length && !stopped) {
        ui.toast({ title: 'Ссылки исправлены', body: res.fixes.map((f) => f.reason).filter(unique).join('; '), kind: 'info' })
      }
    } catch (err) {
      state.inFlight = null
      if (err instanceof NetworkError || (err.status >= 500 && err.status < 600) || err.status === 429) {
        // Сервер мог применить пакет, а ответ потерялся: повтор безопасен (сервер распознаёт повтор)
        state.queue.unshift(...batch.entries)
        state.offline = true
        scheduleRetry()
        return
      }
      if (err instanceof AuthRequiredError) {
        // Сессия истекла: правки ждут, пока сотрудник снова войдёт, и уходят сами
        state.queue.unshift(...batch.entries)
        state.authLost = true
        scheduleRetry()
        return
      }
      if (err.status === 403 || err.status === 404 || err.code === 'archived') {
        // План недоступен для правки целиком: каждая правка будет отклонена
        for (const entry of [...batch.entries, ...state.queue]) dropUndo(entry.undoRef)
        const lost = batch.entries.length + state.queue.length
        state.queue = []
        if (err.code === 'archived' && state.plan) state.plan.isArchived = true
        ui.error(err.message || 'Изменения не сохранены', `Не сохранено: ${countLabel(lost, 'правка', 'правки', 'правок')}. План обновлён с сервера.`)
        resyncNeeded = !stopped
        drained()
        return
      }
      if (batch.entries.length > 1) {
        // Отклонён пакет из нескольких правок — отправляем их по одной
        isolate = true
        state.queue.unshift(...batch.entries)
        flush()
        return
      }
      const [entry] = batch.entries
      dropUndo(entry.undoRef)
      ui.error(`Не сохранено: ${entry.label || 'изменение'}`, err.message || 'Сервер отклонил изменение')
      resyncNeeded = !stopped
    }
    if (state.queue.length) flush()
    else drained()
  }

  function scheduleRetry() {
    clearTimeout(retryTimer)
    const delay = RETRY_STEPS_MS[Math.min(retryStep, RETRY_STEPS_MS.length - 1)]
    retryStep += 1
    retryTimer = setTimeout(flush, delay)
  }

  /** Дождаться отправки очереди (например, перед восстановлением версии). */
  async function flushNow(timeoutMs = 6000) {
    clearTimeout(flushTimer)
    const startedAt = Date.now()
    flush()
    while ((state.queue.length || state.inFlight) && Date.now() - startedAt < timeoutMs) {
      await new Promise((resolve) => setTimeout(resolve, 100))
      if (!state.inFlight && state.queue.length && !state.offline && !state.authLost) flush()
    }
    return !state.queue.length && !state.inFlight
  }

  // ---------- синхронизация ----------
  let polling = false
  async function poll() {
    if (stopped || !state.plan || polling) return
    if (state.inFlight || state.queue.length) return
    polling = true
    try {
      const editing = canEdit.value && (Boolean(state.view.panelKey) || pendingCount.value > 0)
      const res = await api.get(`/plans/${planId}/changes`, { since: state.revision, editing: editing ? 1 : 0 })
      state.presence = res.presence || []
      if (res.access && res.access !== state.access) state.access = res.access
      if (state.plan && res.isArchived !== undefined) state.plan.isArchived = res.isArchived
      if (res.unchanged || state.inFlight || state.queue.length) return
      const skip = pendingTags()
      const openKey = state.view.panelKey
      const openName = openKey ? state.items[openKey]?.name : null
      const changed = [
        ...mergeEntities('items', 'item', res.items, skip),
        ...mergeEntities('notes', 'note', res.notes, skip).map((k) => `note:${k}`),
        ...mergeEntities('assignees', 'assignee', res.assignees, skip).map((k) => `assignee:${k}`),
      ]
      if (res.plan && !skip.has('plan')) state.plan = { ...res.plan, access: state.access }
      state.revision = res.revision
      const remoteEvents = res.events || []
      if (state.view.selectedKey && !state.items[state.view.selectedKey]) state.view.selectedKey = null
      if (openKey && !state.items[openKey]) {
        // Открытую карточку удалил коллега: карточка закрывается не молча
        const event = remoteEvents.find((e) => e.entityKey === openKey && e.action === 'delete')
        const deleted = state.graveyard.items[openKey]
        const who = event?.userName || (deleted?.updatedById && state.users[String(deleted.updatedById)]?.name) || 'Коллега'
        state.view.panelKey = null
        ui.toast({
          title: `Строку «${openName || 'без названия'}» удалил(а) ${who}`,
          body: 'Карточка закрыта. Вернуть строку можно из истории плана.', kind: 'info', timeout: 9000,
        })
      }
      if (remoteEvents.length) {
        const flashUntil = Date.now() + 2500
        for (const key of changed) state.remoteFlash[key] = flashUntil
        setTimeout(() => {
          const now = Date.now()
          for (const [key, until] of Object.entries(state.remoteFlash)) if (until <= now) delete state.remoteFlash[key]
        }, 2600)
        onRemoteChange(remoteEvents)
      }
      if (state.offline) { state.offline = false; flush() }
    } catch (err) {
      if (err instanceof NetworkError) state.offline = true
      if (err?.status === 404) {
        ui.error('План удалён или доступ закрыт')
        stop()
      }
    } finally {
      polling = false
    }
  }

  function schedulePoll() {
    clearTimeout(pollTimer)
    if (stopped) return
    const delay = document.visibilityState === 'visible' ? POLL_VISIBLE_MS : POLL_HIDDEN_MS
    pollTimer = setTimeout(async () => { await poll(); schedulePoll() }, delay)
  }

  function onVisibility() {
    if (document.visibilityState === 'visible') { state.today = todayLocal(); poll() }
  }

  function start() {
    stopped = false
    clearInterval(todayTimer)
    schedulePoll()
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('focus', onVisibility)
    todayTimer = setInterval(() => { state.today = todayLocal() }, 60000)
  }

  /** Закрыть план: опрос и таймеры останавливаются, неотправленные правки досылаются в фоне. */
  function stop() {
    stopped = true
    clearTimeout(pollTimer)
    clearInterval(todayTimer)
    document.removeEventListener('visibilitychange', onVisibility)
    window.removeEventListener('focus', onVisibility)
    api.delete(`/plans/${planId}/presence`).catch(() => {})
    if (tracker.pending()) {
      clearTimeout(flushTimer)
      flush()
    } else {
      clearTimeout(retryTimer)
      liveEditors.delete(tracker)
    }
  }

  // ---------- правки ----------
  function commit(ops, label, { undoable = true } = {}) {
    if (!ops.length) return null
    if (!canEdit.value) {
      ui.error(state.plan?.isArchived ? 'План в архиве — изменения недоступны' : 'У вас доступ только для просмотра')
      return null
    }
    const inverse = applyBatch(state, ops)
    let entry = null
    if (undoable) {
      entry = { label, inverse }
      state.undo.push(entry)
      if (state.undo.length > 100) state.undo.shift()
      state.redo = []
    }
    enqueue(ops, label, entry)
    return entry
  }

  /**
   * Отменить последнюю правку или конкретную (entry — например, удаление из уведомления
   * «Отменить», даже если после него была ещё правка).
   */
  function undo(target = null) {
    if (!canEdit.value) return
    const entry = target ? state.undo.find((e) => toRaw(e) === toRaw(target)) : state.undo[state.undo.length - 1]
    if (!entry) return
    state.undo = state.undo.filter((e) => e !== entry)
    const inverse = applyBatch(state, entry.inverse)
    const redoEntry = { label: entry.label, inverse }
    state.redo.push(redoEntry)
    enqueue(entry.inverse, `Отмена: ${entry.label}`, redoEntry)
    ui.toast({ title: `Отменено: ${entry.label}`, timeout: 2200 })
  }

  function redo() {
    if (!canEdit.value) return
    const entry = state.redo.pop()
    if (!entry) return
    const inverse = applyBatch(state, entry.inverse)
    const undoEntry = { label: entry.label, inverse }
    state.undo.push(undoEntry)
    enqueue(entry.inverse, `Повтор: ${entry.label}`, undoEntry)
    ui.toast({ title: `Повторено: ${entry.label}`, timeout: 2200 })
  }

  /**
   * Сдвиги по связям после правок (если включён автосдвиг) — только для изменённых задач и тех, что от
   * них зависят (по связи или как подзадачи). Несогласованные сроки в других местах плана не трогаются:
   * их выравнивает сотрудник кнопкой «Выровнять по связям».
   */
  function cascadeOps(changes, { all = false } = {}) {
    if (!settings.value.display.autoShift) return []
    const hypothetical = itemList.value.map((item) => (changes.has(item.key) ? { ...item, ...changes.get(item.key) } : item))
    for (const [key, fields] of changes) {
      if (!state.items[key] && fields.type) hypothetical.push(fields)
    }
    let only = null
    if (!all) {
      only = new Set([...changes].filter(([key, fields]) => !state.items[key] || Object.keys(fields).some((f) => SCHEDULE_FIELDS.has(f))).map(([key]) => key))
      if (!only.size) return []
      let grew = true
      while (grew) {
        grew = false
        for (const item of hypothetical) {
          if (!only.has(item.key) && (only.has(item.dependencyKey) || only.has(item.parentKey))) { only.add(item.key); grew = true }
        }
      }
    }
    const shifts = computeDependencyShifts(hypothetical, { only })
    return [...shifts].map(([key, dates]) => ({ op: 'item.update', key, fields: dates }))
  }

  function updateItem(key, fields, label = 'Изменение') {
    return updateItems([{ key, fields }], label)
  }

  function updateItems(list, label = 'Изменение') {
    const ops = []
    const changes = new Map()
    for (const { key, fields } of list) {
      const item = state.items[key]
      if (!item) continue
      const diff = {}
      for (const [field, value] of Object.entries(fields)) {
        if ((item[field] ?? null) !== (value ?? null)) diff[field] = value
      }
      // Сдвиг завершённой задачи — от планового окончания до даты завершения: перенос сроков или даты
      // завершения пересчитывает его, иначе «просрочка» на графике не совпала бы с фактической датой
      const next = { ...item, ...diff }
      if (item.type === 'task' && next.status === 'done' && next.completedDate && !('offset' in fields)
          && ('end' in diff || 'completedDate' in diff || 'status' in diff)) {
        const offset = diffDays(next.end, next.completedDate)
        if (offset !== (item.offset || 0)) diff.offset = offset
      }
      if (!Object.keys(diff).length) continue
      ops.push({ op: 'item.update', key, fields: diff })
      changes.set(key, { ...(changes.get(key) || {}), ...diff })
    }
    if (!ops.length) return
    commit([...ops, ...cascadeOps(changes)], label)
  }

  function siblingsOf(parentKey) {
    return (index.value.children.get(parentKey || null) || [])
  }

  function nextPosition(parentKey, afterKey = null) {
    const siblings = siblingsOf(parentKey)
    let at = siblings.length
    if (afterKey) {
      const i = siblings.findIndex((s) => s.key === afterKey)
      if (i >= 0) at = i + 1
    }
    return insertPosition(siblings, at)
  }

  function defaultDates(start) {
    const r = range.value
    let begin = start || (state.today >= r.start && state.today <= r.end ? state.today : r.start)
    if (begin > r.end) begin = r.start
    return { start: begin, end: addDays(begin, 4) }
  }

  /** Новая строка со значениями по умолчанию для своего типа. */
  function newItem(type, data, key, parent, position) {
    const item = { key, type, parentKey: parent || null, position, ...data }
    if (type === 'task') {
      Object.assign(item, {
        status: 'new', endMode: 'date', durationType: 'calendar', progress: 0, offset: 0, dependencyType: 'FS',
        dependencyLag: 0, color: 'blue', ...defaultDates(data.start), ...data,
      })
      item.duration = diffDays(item.start, item.end) + 1
    } else if (type === 'milestone') {
      Object.assign(item, { date: data.date || defaultDates().start, color: 'teal', lineStyle: 'dashed', ...data })
      item.lineColor = item.lineColor || item.color
    } else {
      Object.assign(item, { color: 'slate', ...data })
    }
    item.name = (item.name || '').trim() || { task: 'Новая задача', milestone: 'Новая веха', group: 'Новая группа' }[type]
    return item
  }

  /** Создать строку; возвращает её ключ. Вставляется после afterKey (среди тех же братьев). */
  function createItem(type, data = {}, { afterKey = null, parentKey = undefined, label } = {}) {
    let parent = parentKey
    if (parent === undefined) {
      const after = afterKey ? state.items[afterKey] : null
      parent = after ? (after.parentKey && state.items[after.parentKey] ? after.parentKey : null) : null
    }
    const key = randomKey('i')
    const { position, renumber } = nextPosition(parent, afterKey)
    const ops = renumber.map((r) => ({ op: 'item.update', key: r.key, fields: { position: r.position } }))
    const item = newItem(type, data, key, parent, position)
    ops.push({ op: 'item.create', item })
    const changes = new Map([[key, item]])
    commit([...ops, ...(type === 'task' && item.dependencyKey ? cascadeOps(changes) : [])], label || { task: 'Новая задача', milestone: 'Новая веха', group: 'Новая группа' }[type])
    return key
  }

  /** Несколько строк в конец родителя одной правкой (отменяется целиком); возвращает их ключи. */
  function createItems(list, { parentKey = null, label = 'Новые строки' } = {}) {
    const siblings = siblingsOf(parentKey)
    let position = siblings.length ? siblings[siblings.length - 1].position : 0
    const keys = []
    const ops = list.map(({ type, data }) => {
      position += STEP
      const key = randomKey('i')
      keys.push(key)
      return { op: 'item.create', item: newItem(type, data || {}, key, parentKey, position) }
    })
    return commit(ops, label) ? keys : []
  }

  function duplicateItem(key, { shiftDays = 0 } = {}) {
    const item = state.items[key]
    if (!item) return null
    const copyKey = randomKey('i')
    const { position, renumber } = nextPosition(item.parentKey && state.items[item.parentKey] ? item.parentKey : null, key)
    const copy = { ...item, key: copyKey, name: `${item.name} (копия)`, position, commentCount: 0 }
    delete copy.updatedAt; delete copy.updatedById; delete copy.updatedRev; delete copy.deleted
    if (item.type === 'task') {
      copy.start = addDays(item.start, shiftDays)
      copy.end = addDays(item.end, shiftDays)
      copy.dependencyKey = null
    }
    if (item.type === 'milestone') copy.date = addDays(item.date, shiftDays)
    const ops = renumber.map((r) => ({ op: 'item.update', key: r.key, fields: { position: r.position } }))
    ops.push({ op: 'item.create', item: copy })
    commit(ops, 'Копирование')
    return copyKey
  }

  /**
   * Удалить строки. withChildren — вместе с вложенными; иначе вложенные строки поднимаются
   * на уровень удалённой. Связи на удалённые строки снимаются, их пометки удаляются.
   */
  function deleteItems(keys, { withChildren = false, label = 'Удаление' } = {}) {
    const idx = index.value
    const toDelete = new Set()
    for (const key of keys) {
      if (!state.items[key]) continue
      toDelete.add(key)
      if (withChildren) for (const d of descendantKeys(idx, key)) toDelete.add(d)
    }
    if (!toDelete.size) return
    const ops = []
    for (const key of toDelete) {
      const item = state.items[key]
      for (const child of idx.children.get(key) || []) {
        if (toDelete.has(child.key)) continue
        let newParent = item.parentKey
        while (newParent && toDelete.has(newParent)) newParent = state.items[newParent]?.parentKey || null
        ops.push({ op: 'item.update', key: child.key, fields: { parentKey: newParent || null } })
      }
    }
    for (const item of itemList.value) {
      if (!toDelete.has(item.key) && item.dependencyKey && toDelete.has(item.dependencyKey)) {
        ops.push({ op: 'item.update', key: item.key, fields: { dependencyKey: null } })
      }
    }
    for (const note of noteList.value) {
      if (note.rowKey && toDelete.has(note.rowKey)) ops.push({ op: 'note.delete', key: note.key })
    }
    for (const key of toDelete) ops.push({ op: 'item.delete', key })
    const entry = commit(ops, label)
    if (toDelete.has(state.view.panelKey)) state.view.panelKey = null
    if (toDelete.has(state.view.selectedKey)) state.view.selectedKey = null
    return entry
  }

  /** Переместить строку: в родителя parentKey перед строкой beforeKey (или в конец). */
  function moveItem(key, { parentKey = null, beforeKey = null } = {}) {
    const item = state.items[key]
    if (!item) return
    if (parentKey === key || (parentKey && descendantKeys(index.value, key).has(parentKey))) {
      ui.error('Нельзя переместить строку внутрь неё самой')
      return
    }
    const parent = parentKey ? state.items[parentKey] : null
    if (parent && parent.type === 'milestone') return
    const siblings = siblingsOf(parentKey).filter((s) => s.key !== key)
    let at = siblings.length
    if (beforeKey) {
      const i = siblings.findIndex((s) => s.key === beforeKey)
      if (i >= 0) at = i
    }
    const { position, renumber } = insertPosition(siblings, at)
    const ops = renumber.map((r) => ({ op: 'item.update', key: r.key, fields: { position: r.position } }))
    const fields = { position }
    if ((item.parentKey || null) !== (parentKey || null)) fields.parentKey = parentKey || null
    ops.push({ op: 'item.update', key, fields })
    commit(ops, 'Перемещение')
    if (parentKey) state.view.collapsed.delete(parentKey)
  }

  function setStatus(key, status, completedDate = null) {
    const item = state.items[key]
    if (!item || item.type !== 'task') return
    const fields = { status }
    if (status === 'done' && item.status !== 'done') {
      const done = completedDate || state.today
      fields.completedDate = done
      fields.offset = diffDays(item.start, done) - diffDays(item.start, item.end)
      fields.progress = 100
    }
    if (status !== 'done' && item.status === 'done') {
      fields.completedDate = null
      fields.offset = 0
    }
    updateItem(key, fields, 'Статус')
  }

  // ---------- пометки, исполнители, план ----------
  function createNote(data) {
    const key = randomKey('n')
    commit([{ op: 'note.create', note: { key, color: 'orange', showDate: false, rowKey: null, ...data } }], 'Пометка')
    return key
  }
  function updateNote(key, fields) {
    commit([{ op: 'note.update', key, fields }], 'Правка пометки')
  }
  function deleteNote(key) {
    commit([{ op: 'note.delete', key }], 'Удаление пометки')
  }
  function createAssignee(data) {
    const key = randomKey('a')
    const last = assigneeList.value[assigneeList.value.length - 1]
    commit([{ op: 'assignee.create', assignee: { key, color: '#2563eb', role: '', position: (last?.position || 0) + 1, ...data } }], 'Новый исполнитель')
    return key
  }
  function updateAssignee(key, fields) {
    commit([{ op: 'assignee.update', key, fields }], 'Правка исполнителя')
  }
  function deleteAssignee(key) {
    const ops = itemList.value.filter((i) => i.assigneeKey === key)
      .map((i) => ({ op: 'item.update', key: i.key, fields: { assigneeKey: null } }))
    ops.push({ op: 'assignee.delete', key })
    commit(ops, 'Удаление исполнителя')
  }
  function updatePlan(fields, label = 'Настройки плана') {
    commit([{ op: 'plan.update', fields }], label)
  }

  // ---------- выбор и сворачивание ----------
  function select(key, { openPanel = false } = {}) {
    state.view.selectedKey = key
    if (openPanel) state.view.panelKey = key
    else if (state.view.panelKey && key) state.view.panelKey = key
  }
  function toggleCollapse(key) {
    if (state.view.collapsed.has(key)) state.view.collapsed.delete(key)
    else state.view.collapsed.add(key)
    saveView()
  }
  function collapseAll(collapse) {
    if (collapse) {
      const keys = itemList.value.filter((i) => index.value.children.get(i.key)?.length).map((i) => i.key)
      state.view.collapsed = new Set(keys)
    } else {
      state.view.collapsed = new Set()
    }
    saveView()
  }
  function revealItem(key) {
    let parent = parentOf(index.value, state.items[key])
    while (parent) {
      state.view.collapsed.delete(parent.key)
      parent = parentOf(index.value, parent)
    }
  }

  return reactive({
    state, settings, range, canEdit, itemList, index, calendar, visibleRows, filtersActive, assigneeList, noteList,
    todayMarker, pendingCount, categories, source,
    load, start, stop, flushNow, poll, saveView,
    commit, undo, redo, updateItem, updateItems, createItem, createItems, duplicateItem, deleteItems, moveItem, setStatus,
    createNote, updateNote, deleteNote, createAssignee, updateAssignee, deleteAssignee, updatePlan,
    select, toggleCollapse, collapseAll, revealItem, cascadeOps,
  })
}

function unique(value, index, list) {
  return list.indexOf(value) === index
}
