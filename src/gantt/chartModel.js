/**
 * Модель отрисовки диаграммы: геометрия шкалы, полос, вех, пометок и связей в пикселях.
 * Одна модель — два представления: экран (Vue, с виртуализацией строк) и экспорт
 * (SVG-строка → PNG/PDF), поэтому выгрузка совпадает с экраном.
 */
import { addDays, diffDays, formatDayNoYear } from './domain/dates'
import { buildHeader, dayX, layoutMilestoneLabels, pxPerDay, totalDays } from './domain/layout'
import { BAR_OPACITY, readableText, resolveColor, STATUS_BY_KEY, textColorOnWhite } from './domain/colors'
import { groupSpan, shiftedEnd } from './domain/tree'

export const BAR_HEIGHT = 24
/** Полоса в строке с пометкой: ниже и тоньше, чтобы подпись пометки над ней не наезжала на полосу. */
export const BAR_HEIGHT_WITH_NOTE = 20
export const GROUP_BAR_HEIGHT = 10
export const TOP_ROW = 22

export function createChartContext({ range, scale, zoom, rowHeight, display, calendar, today, index, notes, assignees }) {
  const px = pxPerDay(scale, zoom)
  const width = totalDays(range) * px
  const header = buildHeader(range, scale, px, calendar, today)
  const bottomRow = scale === 'day' && px >= 22 ? 32 : 24
  const items = [...index.byKey.values()]
  const inRange = (day) => day >= range.start && day <= range.end

  const milestoneLines = items
    .filter((i) => i.type === 'milestone' && i.date && inRange(i.date))
    .map((m) => {
      const color = resolveColor(m.lineColor || m.color, 'teal').color
      return { key: m.key, x: dayX(range, px, m.date) + px - 1, color, style: m.lineStyle || 'dashed', text: m.comment || m.name, name: m.name }
    })
  const globalNotes = display.showNotes
    ? (notes || []).filter((n) => !n.rowKey || !index.byKey.has(n.rowKey)).filter((n) => inRange(n.date))
      .map((n) => ({ ...n, x: dayX(range, px, n.date) + Math.floor(px / 2), color: resolveColor(n.color, 'orange').color }))
    : []
  const laneEntries = [
    ...milestoneLines.filter((m) => m.text).map((m) => ({ kind: 'milestone', key: m.key, x: m.x, text: m.text, color: m.color, textColor: textColorOnWhite(m.color) })),
    ...globalNotes.map((n) => ({ kind: 'note', key: n.key, x: n.x, text: n.text, color: n.color, textColor: textColorOnWhite(n.color), date: n.date, showDate: n.showDate })),
  ]
  const lane = laneEntries.length ? layoutMilestoneLabels(laneEntries, 90) : { labels: [], levels: 0 }
  const laneHeight = lane.labels.length ? 8 + lane.levels * 15 : 0
  const headerHeight = TOP_ROW + bottomRow + laneHeight

  const todayX = inRange(today) ? dayX(range, px, today) : null
  const shades = []
  if (scale === 'day' || px >= 3) {
    let day = range.start
    let guard = 0
    while (day <= range.end && guard < 20000) {
      const holiday = display.showHolidays && calendar.isHoliday(day)
      const weekend = display.showWeekends && !calendar.isWorkingWeekend(day) && [0, 6].includes(new Date(Date.UTC(...ymd(day))).getUTCDay())
      if (holiday || weekend) shades.push({ x: dayX(range, px, day), w: px, kind: holiday ? 'holiday' : 'weekend' })
      day = addDays(day, 1)
      guard += 1
    }
  }
  const assigneeNames = Object.fromEntries((assignees || []).map((a) => [a.key, a.name]))
  return {
    range, scale, px, width, header, bottomRow, laneHeight, lane: lane.labels, headerHeight, rowHeight, display,
    today, todayX, milestoneLines, shades, index, notes: notes || [], assigneeNames, calendar,
    gridLines: header.bottom.map((c) => c.x),
  }
}

function ymd(value) {
  const [y, m, d] = value.split('-').map(Number)
  return [y, m - 1, d]
}

export function barLabel(task, display) {
  const dates = `${formatDayNoYear(task.start)}–${formatDayNoYear(task.end)}`
  if (task.barText) return display.showTaskNamesOnBars ? `${task.barText} — ${task.name}` : task.barText
  return display.showTaskNamesOnBars ? `${dates} — ${task.name}` : dates
}

/** Примерная обрезка текста по ширине (без измерения шрифта): ~6,9 px на символ полужирного 11,5 px. */
export const BAR_CHAR_WIDTH = 6.9
export function fitText(text, width, charWidth = BAR_CHAR_WIDTH) {
  const max = Math.floor((width - 10) / charWidth)
  if (max <= 1) return ''
  return text.length > max ? text.slice(0, Math.max(1, max - 1)) + '…' : text
}

/** Геометрия строки i (y — верх строки в теле диаграммы). */
export function rowModel(row, i, ctx, preview = null) {
  const item = preview?.[row.item.key] ? { ...row.item, ...preview[row.item.key] } : row.item
  const y = i * ctx.rowHeight
  const mid = y + ctx.rowHeight / 2
  const model = { key: item.key, type: item.type, y, mid, item }
  const { range, px, display } = ctx
  if (display.showNotes) {
    model.notes = ctx.notes
      .filter((n) => n.rowKey === item.key && n.date >= range.start && n.date <= range.end)
      .map((n) => {
        const color = resolveColor(n.color, 'orange').color
        return { ...n, x: dayX(range, px, n.date) + Math.floor(px / 2), y: y + 2, color, textColor: textColorOnWhite(color) }
      })
  } else {
    model.notes = []
  }
  // Пометка на строке: подпись — в верхней части строки, полоса — ниже (иначе текст ложится на полосу)
  const roomForNote = model.notes.length > 0 && ctx.rowHeight >= 40
  if (item.type === 'task' && item.start && item.end) {
    const x = dayX(range, px, item.start)
    const w = Math.max(10, (diffDays(item.start, item.end) + 1) * px)
    const color = resolveColor(item.color, 'blue')
    const offset = Number(item.offset || 0)
    const label = barLabel(item, display)
    const h = roomForNote ? BAR_HEIGHT_WITH_NOTE : BAR_HEIGHT
    model.bar = {
      x, w, y: roomForNote ? y + ctx.rowHeight - h - 6 : mid - h / 2, h, fill: color.color,
      text: readableText(color.color, BAR_OPACITY[item.status] || 1),
      label: fitText(label, w), fullLabel: label,
      progress: display.showProgress ? Math.max(0, Math.min(100, Number(item.progress || 0))) : 0,
      status: item.status || 'new', offset,
      tail: offset > 0 ? { x: x + w, w: offset * px, kind: 'late' } : offset < 0 ? { x: x + w - Math.min(w, -offset * px), w: Math.min(w, -offset * px), kind: 'early' } : null,
      tailLabel: offset && display.showOffsetLabels ? `сдвиг ${offset > 0 ? '+' : ''}${offset} д (до ${formatDayNoYear(shiftedEnd(item))})` : '',
      jira: item.jiraUrl || null,
      endX: x + w + (offset > 0 ? offset * px : 0),
    }
  }
  if (item.type === 'group') {
    const span = groupSpan(ctx.index, item.key)
    if (span) {
      const s = preview ? spanWithPreview(ctx.index, item.key, preview) : span
      const x = dayX(range, px, s.start)
      const w = Math.max(10, (diffDays(s.start, s.end) + 1) * px)
      const color = resolveColor(item.color, 'slate')
      model.group = {
        x, w, y: mid - GROUP_BAR_HEIGHT / 2, h: GROUP_BAR_HEIGHT, fill: color.color,
        label: `${formatDayNoYear(s.start)}–${formatDayNoYear(s.end)}`, offset: s.offset,
        tail: s.offset > 0 ? { x: x + w, w: s.offset * px, kind: 'late' } : s.offset < 0 ? { x: x + w - Math.min(w, -s.offset * px), w: Math.min(w, -s.offset * px), kind: 'early' } : null,
        tailLabel: s.offset && display.showOffsetLabels ? `сдвиг ${s.offset > 0 ? '+' : ''}${s.offset} д` : '',
        count: s.count, done: s.done,
      }
    }
  }
  if (item.type === 'milestone' && item.date) {
    const color = resolveColor(item.lineColor || item.color, 'teal')
    model.milestone = { x: dayX(range, px, item.date) + px - 1, y: roomForNote ? mid + 4 : mid, size: 7, fill: color.color, label: item.name }
  }
  return model
}

function spanWithPreview(index, key, preview) {
  const patched = { byKey: new Map(index.byKey), children: index.children }
  for (const [k, fields] of Object.entries(preview)) {
    if (patched.byKey.has(k)) patched.byKey.set(k, { ...patched.byKey.get(k), ...fields })
  }
  return groupSpan(patched, key) || groupSpan(index, key)
}

/**
 * Связи «предшественник → задача» как ломаные со стрелкой. rowsByKey: key → модель строки
 * (только видимые строки; связи с невидимыми строками не рисуются).
 */
export function dependencyPaths(models, ctx) {
  const byKey = new Map(models.map((m) => [m.key, m]))
  const paths = []
  for (const m of models) {
    const item = m.item
    if (item.type !== 'task' || !item.dependencyKey || !m.bar) continue
    const pred = byKey.get(item.dependencyKey)
    if (!pred) continue
    const type = item.dependencyType || 'FS'
    let x1
    let y1
    if (pred.bar) {
      x1 = type === 'SS' || type === 'SF' ? pred.bar.x : pred.bar.x + pred.bar.w + (pred.bar.offset > 0 ? pred.bar.tail.w : 0)
      y1 = pred.mid
    } else if (pred.milestone) {
      x1 = pred.milestone.x
      y1 = pred.mid
    } else continue
    const toStart = type === 'FS' || type === 'SS'
    const x2 = toStart ? m.bar.x : m.bar.x + m.bar.w
    const y2 = m.mid
    const stub = 8
    let d
    if (toStart) {
      if (x2 - x1 >= stub * 2) {
        const xm = x1 + stub
        d = `M${x1} ${y1} H${xm} V${y2} H${x2 - 1}`
      } else {
        const yMid = y1 + (y2 > y1 ? 1 : -1) * ctx.rowHeight / 2
        d = `M${x1} ${y1} H${x1 + stub} V${yMid} H${x2 - stub} V${y2} H${x2 - 1}`
      }
    } else {
      const xr = Math.max(x1, x2) + stub
      d = `M${x1} ${y1} H${xr} V${y2} H${x2 + 1}`
    }
    paths.push({ key: `${pred.key}->${m.key}`, from: pred.key, to: m.key, d, toStart, x2, y2 })
  }
  return paths
}

export const NO_CATEGORY = 'Без категории'

/**
 * Легенда: цвета категорий задач и вех. Задачи — по категории (цвет первой задачи категории,
 * порядок — как в настройках плана); задачи без категории — одной записью «Без категории» и только
 * если категории в плане есть (в 1.0.x каждая задача без категории давала свою запись). Вехи —
 * по цвету линии: одна запись на цвет, подпись — название вехи или «Вехи: …».
 */
export function legendEntries(items, display, categoryOrder = []) {
  const categories = new Map()
  const uncategorised = new Map()
  const milestones = new Map()
  for (const item of items) {
    if (display.legendMode === 'main' && item.parentKey) continue
    if (item.type === 'task') {
      const color = resolveColor(item.color, 'blue').color
      if (item.category) {
        if (!categories.has(item.category)) categories.set(item.category, color)
      } else {
        uncategorised.set(color, (uncategorised.get(color) || 0) + 1)
      }
    }
    if (item.type === 'milestone') {
      const color = resolveColor(item.lineColor || item.color, 'teal').color
      if (!milestones.has(color)) milestones.set(color, [])
      milestones.get(color).push(item.name)
    }
  }
  const order = (name) => {
    const i = categoryOrder.indexOf(name)
    return i < 0 ? Number.MAX_SAFE_INTEGER : i
  }
  const entries = [...categories].sort((a, b) => order(a[0]) - order(b[0]))
    .map(([label, color]) => ({ label, color, kind: 'task' }))
  if (entries.length && uncategorised.size) {
    const [color] = [...uncategorised].sort((a, b) => b[1] - a[1])[0]
    entries.push({ label: NO_CATEGORY, color, kind: 'task' })
  }
  for (const [color, names] of milestones) {
    const label = milestones.size === 1 ? (names.length === 1 ? 'Веха' : 'Вехи')
      : names.length === 1 ? names[0] : `Вехи: ${names[0]} и ещё ${names.length - 1}`
    entries.push({ label, color, kind: 'milestone', title: names.join(', ') })
  }
  return entries
}

export function statusMeta(key) {
  return STATUS_BY_KEY[key] || STATUS_BY_KEY.new
}
