<script setup>
import { computed, inject, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import AppIcon from '../../components/common/AppIcon.vue'
import PopupMenu from '../../components/common/PopupMenu.vue'
import { createChartContext, dependencyPaths, legendEntries, rowModel } from '../chartModel'
import { addDays, diffDays, formatDay, formatDayNoYear } from '../domain/dates'
import { dayX, fitZoom, nextZoom } from '../domain/layout'
import { STATUSES, STATUS_BY_KEY, resolveColor } from '../domain/colors'
import { descendantKeys, parentOf } from '../domain/tree'
import { resolveEnd } from '../domain/calendar'

const editor = inject('gantt')
const actions = inject('ganttActions')
const state = editor.state
const view = state.view

const scroller = ref(null)
const scrollTop = ref(0)
const scrollLeft = ref(0)
const viewport = reactive({ width: 800, height: 600 })
const renaming = ref(null)
const renameValue = ref('')
const quickName = ref('')
const menu = ref(null)
const tooltip = ref(null)
const drag = ref(null)
const preview = ref(null)
const rowDrag = ref(null)
const resizing = ref(false)

const COLUMN_WIDTHS = { start: 74, end: 74, duration: 54, assignee: 120, progress: 52 }
const columns = computed(() => Object.entries(COLUMN_WIDTHS).filter(([key]) => view.columns[key]).map(([key, width]) => ({ key, width })))
const MIN_NAME_WIDTH = 140
const nameWidth = computed(() => Math.max(MIN_NAME_WIDTH, view.gridWidth))
const gridWidth = computed(() => nameWidth.value + columns.value.reduce((s, c) => s + c.width, 0))
const rowHeight = computed(() => editor.settings.rowHeight || 44)
const display = computed(() => editor.settings.display)

const ctx = computed(() => createChartContext({
  range: editor.range, scale: view.scale, zoom: view.zoom, rowHeight: rowHeight.value, display: display.value,
  calendar: editor.calendar, today: editor.todayMarker, index: editor.index, notes: editor.noteList, assignees: editor.assigneeList,
}))
const rows = computed(() => editor.visibleRows)
const extraRows = computed(() => (editor.canEdit && !editor.filtersActive ? 1 : 0))
const bodyHeight = computed(() => Math.max((rows.value.length + extraRows.value) * rowHeight.value + 40, viewport.height - ctx.value.headerHeight))
const windowRange = computed(() => {
  const rh = rowHeight.value
  const start = Math.max(0, Math.floor(scrollTop.value / rh) - 6)
  const end = Math.min(rows.value.length, Math.ceil((scrollTop.value + viewport.height) / rh) + 6)
  return { start, end }
})
const windowRows = computed(() => {
  const { start, end } = windowRange.value
  return rows.value.slice(start, end).map((row, i) => ({ row, index: start + i }))
})
const models = computed(() => windowRows.value.map(({ row, index }) => rowModel(row, index, ctx.value, preview.value)))
const arrows = computed(() => {
  if (!display.value.showDependencies) return []
  // Связи считаются по всем видимым строкам, рисуются только пересекающие окно
  const all = rows.value.map((row, i) => (i >= windowRange.value.start - 60 && i < windowRange.value.end + 60 ? rowModel(row, i, ctx.value, preview.value) : null)).filter(Boolean)
  return dependencyPaths(all, ctx.value)
})
const legend = computed(() => (display.value.legendEnabled ? legendEntries(editor.itemList, display.value, editor.settings.categories || []) : []))
// Легенда — в одну строку: что не помещается, открывается кнопкой «+N»
const legendMenu = ref(null)
let legendCanvas = null
const legendFit = computed(() => {
  const entries = legend.value
  if (!entries.length) return { shown: [], hidden: [] }
  legendCanvas = legendCanvas || document.createElement('canvas')
  const g = legendCanvas.getContext('2d')
  g.font = `11.5px ${getComputedStyle(document.body).fontFamily}`
  const available = viewport.width - 28
  let used = 0
  let count = 0
  for (const [i, entry] of entries.entries()) {
    const w = Math.min(220, g.measureText(entry.label).width) + 22 + (i ? 14 : 0)
    const reserve = i < entries.length - 1 ? 52 : 0
    if (used + w + reserve > available && count) break
    used += w
    count += 1
  }
  return { shown: entries.slice(0, count), hidden: entries.slice(count) }
})
const selectedKey = computed(() => view.selectedKey)
const todayColor = computed(() => resolveColor(editor.settings.today?.color || 'red', 'red').color)
const assigneeName = (key) => editor.assigneeList.find((a) => a.key === key)?.name || ''

// ---------- прокрутка и размеры ----------
let raf = 0
function onScroll() {
  if (raf) return
  raf = requestAnimationFrame(() => {
    raf = 0
    if (!scroller.value) return
    scrollTop.value = scroller.value.scrollTop
    scrollLeft.value = scroller.value.scrollLeft
    if (tooltip.value) tooltip.value = null
  })
}
let observer = null
onMounted(() => {
  observer = new ResizeObserver(() => {
    if (!scroller.value) return
    viewport.width = scroller.value.clientWidth
    viewport.height = scroller.value.clientHeight
  })
  if (scroller.value) observer.observe(scroller.value)
})
let stopResize = null
onBeforeUnmount(() => {
  observer?.disconnect()
  stopResize?.()
  cancelAnimationFrame(raf)
  window.removeEventListener('pointermove', onRowDragMove)
  window.removeEventListener('pointerup', onRowDragEnd)
})

function scrollToToday({ initial = false } = {}) {
  nextTick(() => {
    const el = scroller.value
    if (!el) return
    const today = editor.todayMarker
    if (today < editor.range.start || today > editor.range.end) {
      if (!initial) el.scrollLeft = today < editor.range.start ? 0 : el.scrollWidth
      return
    }
    const x = dayX(editor.range, ctx.value.px, today)
    el.scrollLeft = Math.max(0, x - (el.clientWidth - gridWidth.value) * 0.3)
  })
}
function fitToWidth() {
  const width = (scroller.value?.clientWidth || 800) - gridWidth.value - 24
  view.zoom = fitZoom(editor.range, view.scale, width)
  editor.saveView()
  nextTick(() => { if (scroller.value) scroller.value.scrollLeft = 0 })
}
function scrollToRow(key) {
  const index = rows.value.findIndex((r) => r.item.key === key)
  const el = scroller.value
  if (index < 0 || !el) return
  const top = index * rowHeight.value
  const visibleTop = el.scrollTop
  const visibleBottom = el.scrollTop + el.clientHeight - ctx.value.headerHeight - rowHeight.value
  if (top < visibleTop) el.scrollTop = top
  else if (top > visibleBottom) el.scrollTop = top - (el.clientHeight - ctx.value.headerHeight) + rowHeight.value * 2
}
function scrollToItemDate(item) {
  const el = scroller.value
  if (!el || !item) return
  const day = item.type === 'milestone' ? item.date : item.start
  if (!day) return
  const x = dayX(editor.range, ctx.value.px, day)
  if (x < el.scrollLeft || x > el.scrollLeft + el.clientWidth - gridWidth.value - 60) el.scrollLeft = Math.max(0, x - 80)
}

function onWheel(event) {
  if (!(event.ctrlKey || event.metaKey)) return
  event.preventDefault()
  const el = scroller.value
  const rect = el.getBoundingClientRect()
  const xInTimeline = event.clientX - rect.left - gridWidth.value + el.scrollLeft
  const dayAtCursor = xInTimeline / ctx.value.px
  view.zoom = nextZoom(view.zoom, event.deltaY < 0 ? 1 : -1)
  editor.saveView()
  nextTick(() => {
    el.scrollLeft = Math.max(0, dayAtCursor * ctx.value.px - (event.clientX - rect.left - gridWidth.value))
  })
}

// ---------- ширина колонки ----------
function startResize(event) {
  event.preventDefault()
  const startX = event.clientX
  const startWidth = nameWidth.value
  resizing.value = true
  const move = (e) => { view.gridWidth = Math.max(MIN_NAME_WIDTH, Math.min(900, startWidth + e.clientX - startX)) }
  const up = () => {
    resizing.value = false
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
    stopResize = null
    editor.saveView()
  }
  stopResize = up
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}

/** Двойной щелчок по краю колонки — ширина по самому длинному названию среди показанных строк. */
let measureCanvas = null
function autoFitNameColumn() {
  measureCanvas = measureCanvas || document.createElement('canvas')
  const g = measureCanvas.getContext('2d')
  g.font = `700 12.5px ${getComputedStyle(document.body).fontFamily}`
  let widest = 0
  for (const row of rows.value) widest = Math.max(widest, g.measureText(row.item.name || '').width + row.depth * 16)
  // ручка, стрелка, значок, промежутки и кнопка карточки
  view.gridWidth = Math.round(Math.max(160, Math.min(640, widest + 112)))
  editor.saveView()
}

// ---------- строки слева ----------
function metaLine(item) {
  const parts = []
  if (item.type === 'task') {
    const st = STATUS_BY_KEY[item.status || 'new']
    parts.push({ text: st.label, color: st.color })
    if (item.assigneeKey) parts.push({ text: assigneeName(item.assigneeKey) })
    if (item.category) parts.push({ text: item.category })
    if (item.offset) parts.push({ text: `сдвиг ${item.offset > 0 ? '+' : ''}${item.offset} д`, color: item.offset > 0 ? '#e5484d' : '#2fa96b' })
    if (item.dependencyLag) parts.push({ text: `лаг ${item.dependencyLag > 0 ? '+' : ''}${item.dependencyLag} д` })
    if (item.commentCount) parts.push({ text: `💬 ${item.commentCount}` })
  } else if (item.type === 'group') {
    const model = rows.value.find((r) => r.item.key === item.key)
    if (model?.childCount) parts.push({ text: `${model.childCount} ${model.childCount === 1 ? 'элемент' : 'эл.'}` })
  } else if (item.type === 'milestone') {
    parts.push({ text: formatDay(item.date) })
    if (item.assigneeKey) parts.push({ text: assigneeName(item.assigneeKey) })
  }
  return parts
}

function columnValue(item, column) {
  if (column === 'start') return item.type === 'task' ? formatDay(item.start) : item.type === 'milestone' ? formatDay(item.date) : ''
  if (column === 'end') return item.type === 'task' ? formatDay(item.end) : ''
  if (column === 'duration') return item.type === 'task' ? `${diffDays(item.start, item.end) + 1} д` : ''
  if (column === 'assignee') return item.assigneeKey ? assigneeName(item.assigneeKey) : ''
  if (column === 'progress') return item.type === 'task' ? `${item.progress || 0}%` : ''
  return ''
}

function onRowClick(item, event) {
  if (event.detail > 1) return
  editor.select(item.key)
}
function openPanel(item) {
  editor.select(item.key, { openPanel: true })
}

async function startRename(key) {
  const item = state.items[key]
  if (!item || !editor.canEdit) return
  editor.revealItem(key)
  scrollToRow(key)
  renaming.value = key
  renameValue.value = item.name
  await nextTick()
  const input = scroller.value?.querySelector('.rename-input')
  input?.focus()
  input?.select()
}
function finishRename(save = true) {
  const key = renaming.value
  renaming.value = null
  const name = renameValue.value.trim()
  if (!save || !key || !name || state.items[key]?.name === name) return
  editor.updateItem(key, { name }, 'Переименование')
}

function quickAdd() {
  const name = quickName.value.trim()
  if (!name) return
  const selected = view.selectedKey && state.items[view.selectedKey]
  let options = {}
  if (selected?.type === 'group') options = { parentKey: selected.key }
  else if (selected) options = { afterKey: selected.key }
  const key = editor.createItem('task', { name }, options)
  editor.revealItem(key)
  editor.select(key)
  quickName.value = ''
  nextTick(() => scrollToRow(key))
}

// ---------- перетаскивание строк ----------
function onHandleDown(event, item) {
  if (!editor.canEdit || event.button !== 0) return
  event.preventDefault()
  rowDrag.value = { key: item.key, startY: event.clientY, active: false, target: null }
  window.addEventListener('pointermove', onRowDragMove)
  window.addEventListener('pointerup', onRowDragEnd)
}
function onRowDragMove(event) {
  const d = rowDrag.value
  if (!d) return
  if (!d.active && Math.abs(event.clientY - d.startY) < 4) return
  d.active = true
  const el = scroller.value
  const rect = el.getBoundingClientRect()
  const y = event.clientY - rect.top - ctx.value.headerHeight + el.scrollTop
  if (event.clientY < rect.top + ctx.value.headerHeight + 20) el.scrollTop -= 12
  if (event.clientY > rect.bottom - 30) el.scrollTop += 12
  const index = Math.floor(y / rowHeight.value)
  const row = rows.value[Math.max(0, Math.min(rows.value.length - 1, index))]
  if (!row) return
  const within = (y - index * rowHeight.value) / rowHeight.value
  let zone = within < 0.3 ? 'before' : within > 0.7 ? 'after' : 'into'
  if (zone === 'into' && row.item.type === 'milestone') zone = within < 0.5 ? 'before' : 'after'
  if (index >= rows.value.length) zone = 'after'
  const invalid = row.item.key === d.key || descendantKeys(editor.index, d.key).has(row.item.key)
  d.target = invalid ? null : { key: row.item.key, zone }
}
function onRowDragEnd() {
  window.removeEventListener('pointermove', onRowDragMove)
  window.removeEventListener('pointerup', onRowDragEnd)
  const d = rowDrag.value
  rowDrag.value = null
  if (!d?.active || !d.target) return
  const target = state.items[d.target.key]
  if (!target) return
  if (d.target.zone === 'into') {
    editor.moveItem(d.key, { parentKey: target.key, beforeKey: null })
    return
  }
  const parent = parentOf(editor.index, target)
  const siblings = (editor.index.children.get(parent ? parent.key : null) || []).filter((s) => s.key !== d.key)
  const i = siblings.findIndex((s) => s.key === target.key)
  const before = d.target.zone === 'before' ? target.key : siblings[i + 1]?.key || null
  editor.moveItem(d.key, { parentKey: parent ? parent.key : null, beforeKey: before })
}

// ---------- перетаскивание полос ----------
function shiftKeysForGroup(key) {
  return [...descendantKeys(editor.index, key)].filter((k) => ['task', 'milestone'].includes(state.items[k]?.type))
}

function onBarDown(event) {
  if (event.button !== 0) return
  const target = event.target.closest('[data-key]')
  if (!target) {
    const item = rowAtClientY(event.clientY)
    editor.select(item ? item.key : null)
    return
  }
  const key = target.dataset.key
  const kind = target.dataset.kind
  const handle = event.target.closest('[data-handle]')?.dataset.handle || 'move'
  const item = state.items[key] || editor.source.items[key]
  if (!item) return
  event.preventDefault()
  drag.value = {
    key, kind, handle, startX: event.clientX, moved: false,
    copy: kind === 'task' && handle === 'move' && (event.ctrlKey || event.metaKey || event.altKey),
    origin: { start: item.start, end: item.end, date: item.date },
    group: kind === 'group' ? shiftKeysForGroup(key) : null,
  }
  event.currentTarget.setPointerCapture?.(event.pointerId)
}

function clampToParent(item, start, end, keepDuration) {
  const parent = item.parentKey ? state.items[item.parentKey] : null
  if (!parent || parent.type !== 'task') return { start, end }
  if (start < parent.start) {
    const shift = diffDays(start, parent.start)
    start = parent.start
    if (keepDuration) end = addDays(end, shift)
  }
  if (end > parent.end) {
    // Перенос упёрся в окончание родителя: длина сохраняется, задача прижимается к концу
    if (keepDuration) start = maxDay(parent.start, addDays(start, -diffDays(parent.end, end)))
    end = parent.end
  }
  if (end < start) end = start
  return { start, end }
}
const maxDay = (a, b) => (a > b ? a : b)

function onBarMove(event) {
  const d = drag.value
  if (!d) {
    updateTooltip(event)
    return
  }
  const dx = event.clientX - d.startX
  if (!d.moved && Math.abs(dx) < 3) return
  d.moved = true
  tooltip.value = null
  if (!editor.canEdit) return
  const delta = Math.round(dx / ctx.value.px)
  const item = state.items[d.key]
  if (!item) return
  if (d.kind === 'task') {
    let start = d.origin.start
    let end = d.origin.end
    if (d.handle === 'move') {
      start = addDays(start, delta)
      // Длительность в рабочих днях сохраняется: окончание считается по календарю от нового начала
      end = item.endMode === 'duration' && item.durationType === 'working' && item.duration
        ? resolveEnd(editor.calendar, start, 'duration', end, item.duration, 'working')
        : addDays(end, delta)
    }
    if (d.handle === 'left') { start = addDays(start, delta); if (start > end) start = end }
    if (d.handle === 'right') { end = addDays(end, delta); if (end < start) end = start }
    const clamped = d.copy ? { start, end } : clampToParent(item, start, end, d.handle === 'move')
    preview.value = d.copy ? { __copyShift: delta } : { [d.key]: clamped }
    d.delta = delta
    d.result = clamped
  } else if (d.kind === 'milestone') {
    preview.value = { [d.key]: { date: addDays(d.origin.date, delta) } }
    d.delta = delta
  } else if (d.kind === 'group') {
    const next = {}
    for (const k of d.group) {
      const it = state.items[k]
      if (it.type === 'task') next[k] = { start: addDays(it.start, delta), end: addDays(it.end, delta) }
      else next[k] = { date: addDays(it.date, delta) }
    }
    preview.value = next
    d.delta = delta
  }
}

function onBarUp(event) {
  const d = drag.value
  drag.value = null
  const finalPreview = preview.value
  preview.value = null
  if (!d) return
  event.currentTarget.releasePointerCapture?.(event.pointerId)
  if (!d.moved) {
    editor.select(d.key, { openPanel: true })
    return
  }
  if (!editor.canEdit || !d.delta) return
  const item = state.items[d.key]
  if (d.kind === 'task') {
    if (d.copy) {
      const copy = editor.duplicateItem(d.key, { shiftDays: d.delta })
      if (copy) editor.select(copy)
      return
    }
    const { start, end } = d.result
    const fields = { start, end }
    if (d.handle !== 'move') {
      // Длительность меняется только при растягивании за край; перенос её сохраняет
      fields.duration = item.durationType === 'working' ? editor.calendar.countWorkingDays(start, end) : diffDays(start, end) + 1
    }
    editor.updateItem(d.key, fields, d.handle === 'move' ? `Сдвиг «${item.name}»` : `Сроки «${item.name}»`)
  } else if (d.kind === 'milestone') {
    editor.updateItem(d.key, finalPreview[d.key], `Сдвиг вехи «${item.name}»`)
  } else if (d.kind === 'group') {
    editor.updateItems(Object.entries(finalPreview).map(([key, fields]) => ({ key, fields })), `Сдвиг группы «${item.name}» на ${d.delta > 0 ? '+' : ''}${d.delta} д`)
  }
}

// ---------- подсказка ----------
function updateTooltip(event) {
  const target = event.target.closest('[data-tip]')
  if (!target) { tooltip.value = null; return }
  const key = target.dataset.tip
  const kind = target.dataset.tipKind || 'item'
  const x = Math.min(event.clientX + 14, window.innerWidth - 340)
  const y = event.clientY + 16 > window.innerHeight - 180 ? event.clientY - 170 : event.clientY + 16
  tooltip.value = { key, kind, x, y }
}
const tooltipData = computed(() => {
  const t = tooltip.value
  if (!t) return null
  if (t.kind === 'note') {
    const note = editor.source.notes[t.key]
    return note ? { title: note.text, rows: [['Дата', formatDay(note.date)]] } : null
  }
  const item = editor.source.items[t.key]
  if (!item) return null
  const fields = display.value.tooltipFields || []
  const rowsOut = []
  if (item.type === 'task') {
    if (fields.includes('status')) rowsOut.push(['Статус', STATUS_BY_KEY[item.status || 'new'].label, STATUS_BY_KEY[item.status || 'new'].color])
    if (fields.includes('dates')) {
      rowsOut.push(['Срок', `${formatDay(item.start)} — ${formatDay(item.end)} (${diffDays(item.start, item.end) + 1} д)`])
      if (item.offset) rowsOut.push(['Сдвиг', `${item.offset > 0 ? '+' : ''}${item.offset} д, до ${formatDay(addDays(item.end, item.offset))}`])
      if (item.completedDate) rowsOut.push(['Завершена', formatDay(item.completedDate)])
    }
    if (fields.includes('progress') && item.progress) rowsOut.push(['Прогресс', `${item.progress}%`])
    if (fields.includes('dependency') && item.dependencyKey && editor.source.items[item.dependencyKey]) {
      rowsOut.push(['Зависит от', editor.source.items[item.dependencyKey].name])
    }
    if (fields.includes('assignee') && item.assigneeKey) rowsOut.push(['Ответственный', assigneeName(item.assigneeKey)])
    if (item.category) rowsOut.push(['Категория', item.category])
  } else if (item.type === 'milestone') {
    rowsOut.push(['Дата', formatDay(item.date)])
    if (item.comment) rowsOut.push(['Комментарий', item.comment])
    if (item.assigneeKey) rowsOut.push(['Ответственный', assigneeName(item.assigneeKey)])
  } else {
    const model = models.value.find((m) => m.key === item.key)
    if (model?.group) {
      rowsOut.push(['Сроки', model.group.label])
      rowsOut.push(['Задач', `${model.group.count}, завершено ${model.group.done}`])
      if (model.group.offset) rowsOut.push(['Сдвиг', `${model.group.offset > 0 ? '+' : ''}${model.group.offset} д`])
    }
  }
  if (fields.includes('lastChange') && item.updatedAt) {
    const who = item.updatedById && state.users[String(item.updatedById)]?.name
    rowsOut.push(['Изменено', new Date(item.updatedAt).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }) + (who ? ` · ${who}` : '')])
  }
  return { title: item.name, type: item.type, rows: rowsOut }
})

// ---------- контекстное меню ----------
function dateAtClientX(clientX) {
  const el = scroller.value
  const rect = el.getBoundingClientRect()
  const x = clientX - rect.left - gridWidth.value + el.scrollLeft
  const day = addDays(editor.range.start, Math.floor(x / ctx.value.px))
  return day < editor.range.start ? editor.range.start : day > editor.range.end ? editor.range.end : day
}
function rowAtClientY(clientY) {
  const el = scroller.value
  const rect = el.getBoundingClientRect()
  const y = clientY - rect.top - ctx.value.headerHeight + el.scrollTop
  return rows.value[Math.floor(y / rowHeight.value)]?.item || null
}
function openContextMenu(event, fromGrid = false) {
  event.preventDefault()
  tooltip.value = null
  const item = rowAtClientY(event.clientY)
  const date = fromGrid ? (item?.start || item?.date || editor.todayMarker) : dateAtClientX(event.clientX)
  if (item) editor.select(item.key)
  menu.value = { x: event.clientX, y: event.clientY, item, date }
}
function closeMenu() { menu.value = null }

function menuAction(action) {
  const { item, date } = menu.value
  closeMenu()
  const can = editor.canEdit
  switch (action) {
    case 'open': openPanel(item); break
    case 'rename': startRename(item.key); break
    case 'jira': window.open(item.jiraUrl, '_blank', 'noopener'); break
    case 'note': actions.openNoteEditor({ date, rowKey: item?.key || null }); break
    case 'child': if (can) select(editor.createItem('task', { start: date }, { parentKey: item.key })); break
    case 'next': if (can) {
      const start = addDays(item.end, 1)
      select(editor.createItem('task', { start, end: addDays(start, 4), dependencyKey: item.key, dependencyType: 'FS', name: 'Новая задача' }, { afterKey: item.key }))
    } break
    case 'duplicate': if (can) select(editor.duplicateItem(item.key)); break
    case 'delete': if (can) actions.deleteRows([item.key]); break
    case 'task-here': if (can) select(editor.createItem('task', { start: date }, item ? (item.type === 'group' ? { parentKey: item.key } : { afterKey: item.key }) : {})); break
    case 'group-here': if (can) select(editor.createItem('group', {}, item ? { afterKey: item.key } : {})); break
    case 'milestone-here': if (can) select(editor.createItem('milestone', { date }, item ? (item.type === 'group' ? { parentKey: item.key } : { afterKey: item.key }) : {})); break
    case 'subgroup': if (can) select(editor.createItem('group', {}, { parentKey: item.key })); break
    default:
      if (action.startsWith('status:')) actions.setStatus(item.key, action.slice(7))
  }
}
function select(key) {
  if (!key) return
  editor.revealItem(key)
  editor.select(key, { openPanel: true })
  nextTick(() => scrollToRow(key))
}

watch(() => view.panelKey, (key) => {
  if (key) nextTick(() => { scrollToRow(key); scrollToItemDate(state.items[key]) })
})

defineExpose({ scrollToToday, fitToWidth, scrollToRow, startRename })

function dash(style) {
  return style === 'solid' ? null : style === 'dotted' ? '1.5 3' : '5 4'
}
function noteLabel(note) {
  return (note.showDate ? `${formatDayNoYear(note.date)} · ` : '') + note.text
}
</script>

<template>
  <div class="gantt" :class="{ 'is-resizing': resizing, 'is-dragging': drag?.moved || rowDrag?.active }">
    <div ref="scroller" class="scroller scroll-thin" @scroll="onScroll" @wheel="onWheel">
      <div class="canvas" :style="{ width: gridWidth + ctx.width + 'px', height: ctx.headerHeight + bodyHeight + 'px' }">
        <!-- Заголовок -->
        <div class="header" :style="{ height: ctx.headerHeight + 'px', width: gridWidth + ctx.width + 'px' }">
          <div class="corner" :style="{ width: gridWidth + 'px' }">
            <div class="corner-cols" :style="{ height: ctx.headerHeight + 'px' }">
              <div class="col-head col-name" :style="{ width: nameWidth + 'px' }">
                <span>Задача</span>
                <span class="muted small">{{ rows.length }} {{ rows.length === 1 ? 'строка' : 'строк' }}</span>
              </div>
              <div v-for="col in columns" :key="col.key" class="col-head" :style="{ width: col.width + 'px' }">
                {{ { start: 'Начало', end: 'Окончание', duration: 'Длит.', assignee: 'Ответственный', progress: '%' }[col.key] }}
              </div>
            </div>
            <div class="resizer" title="Ширина колонки: перетащите; двойной щелчок — по самому длинному названию" @pointerdown="startResize" @dblclick="autoFitNameColumn" />
          </div>
          <svg class="header-svg" :width="ctx.width" :height="ctx.headerHeight">
            <g class="top-row">
              <g v-for="cell in ctx.header.top" :key="'t' + cell.x">
                <rect :x="cell.x" y="0" :width="cell.w" height="22" class="h-top" />
                <text :x="cell.x + 6" y="15" class="h-top-text">{{ cell.label }}</text>
              </g>
            </g>
            <g class="bottom-row" transform="translate(0, 22)">
              <g v-for="cell in ctx.header.bottom" :key="'b' + cell.x">
                <rect :x="cell.x" y="0" :width="cell.w" :height="ctx.bottomRow" :class="['h-cell', { weekend: cell.weekend && display.showWeekends && !cell.workingWeekend, holiday: cell.holiday && display.showHolidays, today: cell.today }]" />
                <text v-if="cell.label" :x="cell.x + cell.w / 2" :y="cell.sub ? 13 : 16" class="h-text" :class="{ today: cell.today }">{{ cell.label }}</text>
                <text v-if="cell.sub" :x="cell.x + cell.w / 2" y="26" class="h-sub" :class="{ today: cell.today }">{{ cell.sub }}</text>
              </g>
            </g>
            <g v-if="ctx.laneHeight" :transform="`translate(0, ${22 + ctx.bottomRow})`">
              <rect x="0" y="0" :width="ctx.width" :height="ctx.laneHeight" class="lane-bg" />
              <g v-for="label in ctx.lane" :key="label.kind + label.key" class="lane-label" :data-key="label.kind === 'milestone' ? label.key : null"
                 :data-tip="label.key" :data-tip-kind="label.kind === 'note' ? 'note' : 'item'"
                 @click="label.kind === 'milestone' ? openPanel(state.items[label.key]) : actions.openNoteEditor({ noteKey: label.key })"
                 @pointermove="updateTooltip" @pointerleave="tooltip = null">
                <path v-if="label.kind === 'note'" :d="`M${label.x - 4} ${6 + label.level * 15} l4 9 l4 -9 a4 4 0 1 0 -8 0 Z`" :fill="label.color" />
                <text :x="label.x + (label.kind === 'note' ? 7 : 0)" :y="16 + label.level * 15" :fill="label.color" :text-anchor="label.kind === 'note' ? 'start' : 'middle'" class="lane-text">
                  {{ label.kind === 'note' ? noteLabel(label) : label.text }}
                </text>
              </g>
            </g>
            <line v-if="ctx.todayX !== null" :x1="ctx.todayX" :x2="ctx.todayX" :y1="22" :y2="ctx.headerHeight" class="today-head" :stroke="todayColor" />
          </svg>
        </div>

        <!-- Тело -->
        <div class="body" :style="{ height: bodyHeight + 'px' }">
          <div class="grid" :style="{ width: gridWidth + 'px', height: bodyHeight + 'px' }" @contextmenu="openContextMenu($event, true)">
            <div
              v-for="{ row, index } in windowRows" :key="row.item.key" class="grid-row"
              :class="[`type-${row.item.type}`, {
                selected: selectedKey === row.item.key, alt: index % 2 === 1, flash: state.remoteFlash[row.item.key],
                'drop-before': rowDrag?.target?.key === row.item.key && rowDrag.target.zone === 'before',
                'drop-after': rowDrag?.target?.key === row.item.key && rowDrag.target.zone === 'after',
                'drop-into': rowDrag?.target?.key === row.item.key && rowDrag.target.zone === 'into',
                dragging: rowDrag?.active && rowDrag.key === row.item.key,
              }]"
              :style="{ top: index * rowHeight + 'px', height: rowHeight + 'px' }" :data-row="row.item.key"
              @click="onRowClick(row.item, $event)" @dblclick="editor.canEdit ? startRename(row.item.key) : openPanel(row.item)"
            >
              <div class="cell-name" :style="{ width: nameWidth + 'px' }">
                <span v-if="editor.canEdit" class="handle" title="Перетащить" @pointerdown.stop="onHandleDown($event, row.item)"><AppIcon name="grip" :size="14" /></span>
                <span class="indent" :style="{ width: row.depth * 16 + 'px' }" />
                <button v-if="row.hasChildren" class="caret" type="button" :title="view.collapsed.has(row.item.key) && !editor.filtersActive ? 'Развернуть' : 'Свернуть'" @click.stop="editor.toggleCollapse(row.item.key)" @dblclick.stop>
                  <AppIcon :name="view.collapsed.has(row.item.key) && !editor.filtersActive ? 'chevronRight' : 'chevronDown'" :size="13" />
                </button>
                <span v-else class="caret-space" />
                <span class="mark">
                  <AppIcon v-if="row.item.type === 'group'" name="folder" :size="14" />
                  <AppIcon v-else-if="row.item.type === 'milestone'" name="diamond" :size="13" />
                  <span v-else class="status-dot" :style="{ background: STATUS_BY_KEY[row.item.status || 'new'].color }" />
                </span>
                <div class="name-block">
                  <input
                    v-if="renaming === row.item.key" v-model="renameValue" class="rename-input input input-sm" maxlength="5000"
                    @click.stop @dblclick.stop @keydown.enter.prevent="finishRename()" @keydown.esc.prevent="finishRename(false)" @blur="finishRename()"
                  />
                  <template v-else>
                    <div class="name" :title="row.item.name">{{ row.item.name }}</div>
                    <div v-if="rowHeight >= 36 && metaLine(row.item).length" class="meta">
                      <template v-for="(part, i) in metaLine(row.item)" :key="i">
                        <span v-if="i" class="sep">·</span><span :style="part.color ? { color: part.color } : null">{{ part.text }}</span>
                      </template>
                    </div>
                  </template>
                </div>
                <button class="open-btn" type="button" title="Открыть карточку" @click.stop="openPanel(row.item)"><AppIcon name="detail" :size="14" /></button>
              </div>
              <div v-for="col in columns" :key="col.key" class="cell" :style="{ width: col.width + 'px' }">{{ columnValue(row.item, col.key) }}</div>
            </div>
            <form v-if="extraRows" class="grid-row quick-add" :style="{ top: rows.length * rowHeight + 'px', height: rowHeight + 'px' }" @submit.prevent="quickAdd">
              <div class="cell-name" :style="{ width: nameWidth + 'px' }">
                <AppIcon name="plus" :size="14" class="muted" />
                <input v-model="quickName" class="quick-input" placeholder="Новая задача — введите название и Enter" maxlength="5000" data-testid="quick-add" />
              </div>
            </form>
            <div v-if="!rows.length" class="grid-empty muted">
              {{ editor.filtersActive ? 'Ничего не найдено — измените фильтры' : editor.canEdit ? 'План пуст. Добавьте первую задачу.' : 'План пуст' }}
            </div>
          </div>

          <svg
            class="body-svg" :width="ctx.width" :height="bodyHeight"
            @pointerdown="onBarDown" @pointermove="onBarMove" @pointerup="onBarUp" @pointerleave="!drag && (tooltip = null)"
            @contextmenu="openContextMenu($event)"
          >
            <defs>
              <pattern id="hatch-late" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="3" height="6" fill="rgba(255,255,255,.35)" /></pattern>
              <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L8 4 L0 8 z" fill="#94a3b8" /></marker>
              <marker id="arrow-active" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L8 4 L0 8 z" fill="#4f7cff" /></marker>
            </defs>
            <g class="stripes">
              <rect v-for="{ index } in windowRows" v-show="index % 2 === 1" :key="'s' + index" x="0" :y="index * rowHeight" :width="ctx.width" :height="rowHeight" class="stripe" />
              <rect v-for="m in models" v-show="m.key === selectedKey" :key="'sel' + m.key" x="0" :y="m.y" :width="ctx.width" :height="rowHeight" class="row-selected" />
            </g>
            <g class="shades">
              <rect v-for="s in ctx.shades" :key="'w' + s.x" :x="s.x" y="0" :width="s.w" :height="bodyHeight" :class="s.kind" />
            </g>
            <path class="grid-lines" :d="ctx.gridLines.map((x) => `M${x + 0.5} 0V${bodyHeight}`).join('')" />
            <line v-if="ctx.todayX !== null" :x1="ctx.todayX" :x2="ctx.todayX" y1="0" :y2="bodyHeight" class="today-line"
                  :stroke="todayColor" :stroke-dasharray="dash(editor.settings.today?.style)" />

            <g class="rows">
              <g v-for="m in models" :key="m.key" :class="{ flash: state.remoteFlash[m.key] }">
                <g v-if="m.bar" class="task" :class="[`st-${m.bar.status}`, { selected: m.key === selectedKey, editable: editor.canEdit }]" :data-key="m.key" data-kind="task" :data-tip="m.key">
                  <rect v-if="m.bar.tail?.kind === 'late'" class="tail late" :x="m.bar.tail.x - 3" :y="m.bar.y + 3" :width="m.bar.tail.w + 3" :height="m.bar.h - 6" rx="4" />
                  <rect class="bar" :x="m.bar.x" :y="m.bar.y" :width="m.bar.w" :height="m.bar.h" rx="6" :fill="m.bar.fill" />
                  <rect v-if="m.bar.progress" class="progress" :x="m.bar.x" :y="m.bar.y" :width="m.bar.w * m.bar.progress / 100" :height="m.bar.h" rx="6" />
                  <rect v-if="m.bar.tail?.kind === 'early'" class="tail early" :x="m.bar.tail.x" :y="m.bar.y" :width="m.bar.tail.w" :height="m.bar.h" rx="6" />
                  <rect v-if="m.bar.tail" class="tail-hatch" :x="m.bar.tail.kind === 'late' ? m.bar.tail.x : m.bar.tail.x" :y="m.bar.tail.kind === 'late' ? m.bar.y + 3 : m.bar.y" :width="m.bar.tail.w" :height="m.bar.tail.kind === 'late' ? m.bar.h - 6 : m.bar.h" fill="url(#hatch-late)" />
                  <text class="bar-text" :x="m.bar.x + m.bar.w / 2" :y="m.bar.y + 16" :fill="m.bar.text">{{ m.bar.label }}</text>
                  <text v-if="m.bar.tailLabel && m.bar.tail?.kind === 'late'" class="tail-text" :x="m.bar.tail.x + m.bar.tail.w + 5" :y="m.bar.y + 16">{{ m.bar.tailLabel }}</text>
                  <path v-if="m.bar.status === 'done' && m.bar.w > 26" class="done-check" :d="`M${m.bar.x + m.bar.w - 15} ${m.bar.y + 12} l3 3 l6 -6`" />
                  <rect v-if="m.bar.status === 'done' || m.bar.status === 'blocked' || m.key === selectedKey" class="outline" :x="m.bar.x + 1" :y="m.bar.y + 1" :width="m.bar.w - 2" :height="m.bar.h - 2" rx="5" />
                  <a v-if="m.bar.jira && m.bar.w > 36" :href="m.bar.jira" target="_blank" rel="noopener noreferrer" class="jira" @pointerdown.stop @click.stop>
                    <title>Открыть в Jira</title>
                    <rect :x="m.bar.x + m.bar.w - 19" :y="m.bar.y + 4" width="15" height="15" rx="4" />
                    <path :d="`M${m.bar.x + m.bar.w - 14} ${m.bar.y + 8} h5 v5 M${m.bar.x + m.bar.w - 9} ${m.bar.y + 8} l-5 5`" />
                  </a>
                  <template v-if="editor.canEdit">
                    <rect class="handle-zone" data-handle="left" :x="m.bar.x - 2" :y="m.bar.y" width="8" :height="m.bar.h" />
                    <rect class="handle-zone" data-handle="right" :x="m.bar.x + m.bar.w - 6" :y="m.bar.y" width="8" :height="m.bar.h" />
                  </template>
                </g>
                <g v-if="m.group" class="group-bar" :class="{ selected: m.key === selectedKey, editable: editor.canEdit }" :data-key="m.key" data-kind="group" :data-tip="m.key">
                  <rect v-if="m.group.tail?.kind === 'late'" class="tail late" :x="m.group.x + m.group.w" :y="m.group.y + 2" :width="m.group.tail.w" :height="m.group.h - 4" rx="2" />
                  <path class="group-shape" :fill="m.group.fill" :d="`M${m.group.x} ${m.group.y} h${m.group.w} v${m.group.h + 5} l-5 -5 h-${m.group.w - 10} l-5 5 Z`" />
                  <text v-if="m.group.w > 70" class="group-text" :x="m.group.x + m.group.w / 2" :y="m.group.y - 4">{{ m.group.label }}</text>
                  <text v-if="m.group.tailLabel && m.group.tail?.kind === 'late'" class="tail-text" :x="m.group.x + m.group.w + m.group.tail.w + 5" :y="m.group.y + 9">{{ m.group.tailLabel }}</text>
                </g>
                <g v-if="m.milestone" class="milestone" :class="{ selected: m.key === selectedKey, editable: editor.canEdit }" :data-key="m.key" data-kind="milestone" :data-tip="m.key">
                  <path :d="`M${m.milestone.x} ${m.milestone.y - 9} l9 9 l-9 9 l-9 -9 Z`" :fill="m.milestone.fill" class="diamond" />
                  <text class="milestone-text" :x="m.milestone.x + 13" :y="m.milestone.y + 4">{{ m.milestone.label }}</text>
                </g>
                <g v-for="note in m.notes" :key="note.key" class="note" :data-tip="note.key" data-tip-kind="note" @click.stop="actions.openNoteEditor({ noteKey: note.key })" @pointerdown.stop>
                  <path :d="`M${note.x - 5} ${note.y + 5} a5 5 0 1 1 10 0 c0 3 -5 9 -5 9 c0 0 -5 -6 -5 -9 Z`" :fill="note.color" />
                  <circle :cx="note.x" :cy="note.y + 5" r="1.8" fill="#fff" />
                  <text :x="note.x + 8" :y="note.y + 9" :fill="note.color" class="note-text">{{ noteLabel(note).length > 40 ? noteLabel(note).slice(0, 39) + '…' : noteLabel(note) }}</text>
                </g>
              </g>
            </g>
            <g class="milestone-lines">
              <line v-for="line in ctx.milestoneLines" :key="'ml' + line.key" :x1="line.x" :x2="line.x" y1="0" :y2="bodyHeight" :stroke="line.color" :stroke-dasharray="dash(line.style)" />
            </g>
            <g class="arrows">
              <path v-for="a in arrows" :key="a.key" :d="a.d" :class="{ active: a.from === selectedKey || a.to === selectedKey }"
                    :marker-end="a.from === selectedKey || a.to === selectedKey ? 'url(#arrow-active)' : 'url(#arrow)'" />
            </g>
            <g v-if="preview?.__copyShift !== undefined && drag?.key" class="copy-ghost">
              <template v-for="m in models" :key="'ghost' + m.key">
                <rect v-if="m.key === drag.key && m.bar" :x="m.bar.x + preview.__copyShift * ctx.px" :y="m.bar.y" :width="m.bar.w" :height="m.bar.h" rx="6" />
              </template>
            </g>
          </svg>
        </div>
      </div>
    </div>
    <div v-if="legend.length" class="legend" :class="{ 'is-top': display.legendPosition === 'top' }" data-testid="legend">
      <span v-for="entry in legendFit.shown" :key="entry.kind + entry.label + entry.color" class="legend-item" :title="entry.title || entry.label">
        <span :class="entry.kind === 'milestone' ? 'legend-diamond' : 'legend-bar'" :style="{ background: entry.color }" /><span class="legend-label">{{ entry.label }}</span>
      </span>
      <button v-if="legendFit.hidden.length" class="legend-more" type="button" :title="`Ещё ${legendFit.hidden.length} в легенде`" @click="legendMenu = legendMenu ? null : $event.currentTarget">
        +{{ legendFit.hidden.length }}
      </button>
    </div>
    <PopupMenu v-if="legendMenu" :anchor="legendMenu" align="end" :min-width="220" @close="legendMenu = null">
      <div class="menu-label">Легенда</div>
      <div v-for="entry in legendFit.hidden" :key="'more' + entry.kind + entry.label + entry.color" class="legend-item legend-menu-item" :title="entry.title || entry.label">
        <span :class="entry.kind === 'milestone' ? 'legend-diamond' : 'legend-bar'" :style="{ background: entry.color }" /><span class="legend-label">{{ entry.label }}</span>
      </div>
    </PopupMenu>

    <Teleport to="body">
      <div v-if="tooltipData && !drag" class="tooltip gantt-tooltip" :style="{ left: tooltip.x + 'px', top: tooltip.y + 'px' }">
        <div class="tt-title">{{ tooltipData.type === 'milestone' ? '◆ ' : '' }}{{ tooltipData.title }}</div>
        <div v-for="row in tooltipData.rows" :key="row[0]" class="tt-row">
          <span class="tt-key">{{ row[0] }}</span><span class="tt-value" :style="row[2] ? { color: row[2], fontWeight: 600 } : null">{{ row[1] }}</span>
        </div>
      </div>
    </Teleport>

    <PopupMenu v-if="menu" :x="menu.x" :y="menu.y" :min-width="230" @close="closeMenu">
      <template v-if="menu.item">
        <div class="menu-label ellipsis">{{ menu.item.name }}</div>
        <button class="menu-item" type="button" @click="menuAction('open')"><AppIcon name="detail" /> Открыть карточку<span class="menu-hint">Enter</span></button>
        <template v-if="editor.canEdit">
          <button class="menu-item" type="button" @click="menuAction('rename')"><AppIcon name="edit" /> Переименовать<span class="menu-hint">F2</span></button>
          <template v-if="menu.item.type === 'task'">
            <div class="menu-separator" />
            <div class="menu-label">Статус</div>
            <button v-for="s in STATUSES" :key="s.key" class="menu-item" type="button" :disabled="menu.item.status === s.key" @click="menuAction('status:' + s.key)">
              <span class="status-dot" :style="{ background: s.color }" /> {{ s.label }}
            </button>
            <div class="menu-separator" />
            <button class="menu-item" type="button" @click="menuAction('child')"><AppIcon name="child" /> Подзадача</button>
            <button class="menu-item" type="button" @click="menuAction('next')"><AppIcon name="arrowRight" /> Следующая задача (связь)</button>
          </template>
          <template v-if="menu.item.type === 'group'">
            <div class="menu-separator" />
            <button class="menu-item" type="button" @click="menuAction('task-here')"><AppIcon name="plus" /> Задача в группу</button>
            <button class="menu-item" type="button" @click="menuAction('subgroup')"><AppIcon name="folder" /> Подгруппа</button>
          </template>
          <button class="menu-item" type="button" @click="menuAction('note')"><AppIcon name="note" /> Пометка {{ formatDayNoYear(menu.date) }}</button>
          <button class="menu-item" type="button" @click="menuAction('duplicate')"><AppIcon name="copy" /> Дублировать<span class="menu-hint">Ctrl+D</span></button>
        </template>
        <button v-if="menu.item.jiraUrl" class="menu-item" type="button" @click="menuAction('jira')"><AppIcon name="external" /> Открыть в Jira</button>
        <template v-if="editor.canEdit">
          <div class="menu-separator" />
          <button class="menu-item is-danger" type="button" @click="menuAction('delete')"><AppIcon name="trash" /> Удалить<span class="menu-hint">Delete</span></button>
        </template>
      </template>
      <template v-if="editor.canEdit">
        <div class="menu-separator" v-if="menu.item" />
        <div class="menu-label">Добавить на {{ formatDayNoYear(menu.date) }}</div>
        <button class="menu-item" type="button" @click="menuAction('task-here')"><AppIcon name="gantt" /> Задачу</button>
        <button class="menu-item" type="button" @click="menuAction('milestone-here')"><AppIcon name="diamond" /> Веху</button>
        <button class="menu-item" type="button" @click="menuAction('group-here')"><AppIcon name="folder" /> Группу</button>
        <button v-if="!menu.item" class="menu-item" type="button" @click="menuAction('note')"><AppIcon name="note" /> Пометку</button>
      </template>
      <div v-if="!editor.canEdit && !menu.item" class="menu-label">Только просмотр</div>
    </PopupMenu>
  </div>
</template>

<style scoped>
.gantt { display: flex; flex-direction: column; min-height: 0; background: var(--color-surface); }
.gantt.is-resizing, .gantt.is-dragging { user-select: none; }
.scroller { flex: 1; overflow: auto; position: relative; }
.canvas { position: relative; }
.header { position: sticky; top: 0; z-index: 6; display: flex; background: var(--color-surface-2); }
.corner { position: sticky; left: 0; z-index: 7; background: var(--color-surface-2); border-right: 1px solid var(--color-border); border-bottom: 1px solid var(--color-border-strong); flex-shrink: 0; }
.corner-cols { display: flex; align-items: flex-end; }
.col-head { padding: 0 10px 8px; font-size: 11px; font-weight: 700; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: 0.04em; white-space: nowrap; overflow: hidden; }
.col-name { display: flex; justify-content: space-between; gap: 8px; }
.col-name .small { text-transform: none; letter-spacing: 0; font-weight: 500; }
.resizer { position: absolute; top: 0; right: -4px; width: 8px; height: 100%; cursor: col-resize; z-index: 8; }
.resizer:hover, .is-resizing .resizer { background: rgba(79, 124, 255, 0.25); }
.header-svg { display: block; border-bottom: 1px solid var(--color-border-strong); flex-shrink: 0; }
.h-top { fill: var(--color-surface-2); stroke: var(--color-border); stroke-width: 0.5; }
.h-top-text { font-size: 11px; font-weight: 700; fill: var(--color-text-secondary); }
.h-cell { fill: var(--color-surface-2); stroke: #eceef4; stroke-width: 0.5; }
.h-cell.weekend { fill: #eef0f4; }
.h-cell.holiday { fill: #fde8e8; }
.h-cell.today { fill: var(--color-primary-soft); }
.h-text { font-size: 10.5px; font-weight: 650; fill: var(--color-text-secondary); text-anchor: middle; }
.h-sub { font-size: 9px; fill: var(--color-text-muted); text-anchor: middle; }
.h-text.today, .h-sub.today { fill: var(--color-primary-dark); }
.lane-bg { fill: #fbfcfe; }
.lane-label { cursor: pointer; }
.lane-text { font-size: 10px; font-weight: 700; paint-order: stroke; stroke: #fbfcfe; stroke-width: 3px; }
.today-head { stroke-width: 2; }
.body { position: relative; display: flex; }
.grid { position: sticky; left: 0; z-index: 4; background: var(--color-surface); border-right: 1px solid var(--color-border); flex-shrink: 0; }
.grid-row { position: absolute; left: 0; right: 0; display: flex; align-items: center; border-bottom: 1px solid #f0f2f6; background: var(--color-surface); cursor: default; }
.grid-row.alt { background: #fcfcfe; }
.grid-row:hover { background: #f6f8ff; }
.grid-row.selected { background: var(--color-primary-soft); }
.grid-row.flash { animation: flash 2.4s ease; }
.grid-row.dragging { opacity: 0.45; }
.grid-row.drop-before { box-shadow: inset 0 2px 0 var(--color-primary); }
.grid-row.drop-after { box-shadow: inset 0 -2px 0 var(--color-primary); }
.grid-row.drop-into { background: #e3eaff; box-shadow: inset 0 0 0 2px var(--color-primary); }
@keyframes flash { 0% { background: #fff4cf; } 100% { background: inherit; } }
.cell-name { display: flex; align-items: center; gap: 4px; padding: 0 6px 0 4px; height: 100%; min-width: 0; flex-shrink: 0; }
.handle { color: #c0c6d4; cursor: grab; display: flex; padding: 2px; border-radius: 4px; opacity: 0; flex-shrink: 0; }
.grid-row:hover .handle { opacity: 1; }
.handle:hover { color: var(--color-text-muted); background: #eef1f7; }
.indent { flex-shrink: 0; }
.caret { width: 18px; height: 18px; border: 0; background: transparent; color: var(--color-text-muted); display: flex; align-items: center; justify-content: center; border-radius: 4px; flex-shrink: 0; padding: 0; }
.caret:hover { background: #e6eaf3; color: var(--color-text); }
.caret-space { width: 18px; flex-shrink: 0; }
.mark { width: 16px; display: flex; align-items: center; justify-content: center; color: var(--color-text-muted); flex-shrink: 0; }
.type-milestone .mark { color: #0f9b8e; }
.name-block { flex: 1; min-width: 0; display: flex; flex-direction: column; justify-content: center; }
.name { font-size: 12.5px; line-height: 1.3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.type-group .name { font-weight: 700; }
.meta { font-size: 10.5px; color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; line-height: 1.35; }
.meta .sep { margin: 0 4px; opacity: 0.6; }
.open-btn { width: 24px; height: 24px; border: 1px solid var(--color-border); border-radius: 6px; background: var(--color-surface); color: var(--color-text-muted); display: flex; align-items: center; justify-content: center; opacity: 0; flex-shrink: 0; padding: 0; }
.grid-row:hover .open-btn, .grid-row.selected .open-btn { opacity: 1; }
.open-btn:hover { background: var(--color-primary); border-color: var(--color-primary); color: #fff; }
.cell { font-size: 12px; color: var(--color-text-secondary); padding: 0 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; border-left: 1px solid #f0f2f6; height: 100%; display: flex; align-items: center; }
.rename-input { width: 100%; }
.quick-add { background: transparent; border-bottom: 0; }
.quick-add .cell-name { padding-left: 30px; gap: 8px; }
.quick-input { flex: 1; border: 0; background: transparent; font-size: 12.5px; padding: 6px 4px; outline: none; color: var(--color-text); }
.quick-input:focus { background: var(--color-surface-2); border-radius: 6px; }
.grid-empty { position: absolute; top: 60px; left: 0; right: 0; text-align: center; font-size: 13px; }
.body-svg { display: block; flex-shrink: 0; }
.stripe { fill: #fcfcfe; }
.row-selected { fill: rgba(79, 124, 255, 0.07); }
.weekend { fill: rgba(120, 128, 150, 0.07); }
.holiday { fill: rgba(239, 68, 68, 0.08); }
.grid-lines { stroke: #f0f2f6; stroke-width: 1; }
.today-line { stroke-width: 2; pointer-events: none; }
.task { cursor: pointer; }
.task.editable { cursor: grab; }
.gantt.is-dragging .task.editable { cursor: grabbing; }
.bar { transition: filter 0.12s; }
.task:hover .bar { filter: brightness(1.06) drop-shadow(0 2px 4px rgba(20, 25, 40, 0.18)); }
.progress { fill: rgba(0, 0, 0, 0.22); pointer-events: none; }
.bar-text { font-size: 10px; font-weight: 700; text-anchor: middle; pointer-events: none; }
.tail.late { fill: #ef4444; opacity: 0.6; }
.tail.early { fill: #22c55e; opacity: 0.7; }
.tail-hatch { pointer-events: none; }
.tail-text { font-size: 9.5px; font-weight: 700; fill: #c53030; pointer-events: none; }
.st-done .bar { opacity: 0.78; }
.st-done .outline { fill: none; stroke: #22c55e; stroke-width: 2.5; }
.st-blocked .bar { opacity: 0.7; }
.st-blocked .outline { fill: none; stroke: #e5484d; stroke-width: 2; stroke-dasharray: 4 3; }
.task.selected .outline { fill: none; stroke: #1c2331; stroke-width: 2; stroke-dasharray: none; }
.done-check { fill: none; stroke: #fff; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; pointer-events: none; }
.handle-zone { fill: transparent; cursor: ew-resize; }
.jira rect { fill: rgba(255, 255, 255, 0.92); }
.jira path { fill: none; stroke: #0052cc; stroke-width: 1.6; stroke-linecap: round; }
.jira:hover rect { fill: #fff; }
.group-bar { cursor: pointer; }
.group-bar.editable { cursor: grab; }
.group-shape { opacity: 0.55; }
.group-bar:hover .group-shape, .group-bar.selected .group-shape { opacity: 0.85; }
.group-text { font-size: 9.5px; font-weight: 700; fill: var(--color-text-muted); text-anchor: middle; pointer-events: none; }
.milestone { cursor: pointer; }
.milestone.editable { cursor: grab; }
.diamond { stroke: #fff; stroke-width: 1.5; }
.milestone.selected .diamond { stroke: #1c2331; stroke-width: 2; }
.milestone-text { font-size: 10.5px; font-weight: 600; fill: var(--color-text-secondary); pointer-events: none; }
.milestone-lines line { stroke-width: 2; pointer-events: none; opacity: 0.9; }
.note { cursor: pointer; }
.note-text { font-size: 9.5px; font-weight: 650; paint-order: stroke; stroke: #fff; stroke-width: 3px; }
.arrows path { fill: none; stroke: #a7b0c2; stroke-width: 1.3; pointer-events: none; }
.arrows path.active { stroke: var(--color-primary); stroke-width: 1.8; }
.copy-ghost rect { fill: rgba(79, 124, 255, 0.25); stroke: var(--color-primary); stroke-dasharray: 4 3; pointer-events: none; }
.flash .bar { animation: flash-bar 2.4s ease; }
@keyframes flash-bar { 0%, 30% { filter: drop-shadow(0 0 6px #facc15); } 100% { filter: none; } }
/* Сенсорный экран: ручки перетаскивания не занимают места, кнопка карточки видна всегда */
@media (hover: none) {
  .handle { display: none; }
  .open-btn { display: none; }
  .grid-row.selected .open-btn { display: flex; opacity: 1; width: 32px; height: 32px; }
}
.legend { display: flex; flex-wrap: nowrap; align-items: center; gap: 14px; padding: 5px 14px; border-top: 1px solid var(--color-border); font-size: 11.5px; color: var(--color-text-secondary); flex-shrink: 0; overflow: hidden; min-height: 28px; }
.legend.is-top { order: -1; border-top: 0; border-bottom: 1px solid var(--color-border); }
.legend-item { display: inline-flex; align-items: center; gap: 6px; min-width: 0; flex-shrink: 0; }
.legend-label { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.legend-bar { width: 16px; height: 8px; border-radius: 2px; flex-shrink: 0; }
.legend-diamond { width: 8px; height: 8px; transform: rotate(45deg); flex-shrink: 0; }
.legend-more { border: 1px solid var(--color-border); background: var(--color-surface); border-radius: 999px; padding: 1px 8px; font-size: 11px; font-weight: 600; color: var(--color-text-secondary); flex-shrink: 0; }
.legend-more:hover { border-color: var(--color-border-strong); color: var(--color-text); }
.legend-menu-item { display: flex; padding: 6px 10px; font-size: 12.5px; }
</style>

<style>
.gantt-tooltip { min-width: 220px; }
.gantt-tooltip .tt-title { font-weight: 700; font-size: 13px; margin-bottom: 6px; }
.gantt-tooltip .tt-row { display: flex; gap: 10px; padding: 2px 0; font-size: 12px; }
.gantt-tooltip .tt-key { color: var(--color-text-muted); min-width: 92px; }
.gantt-tooltip .tt-value { color: var(--color-text); }
</style>
