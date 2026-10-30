import { addDays, addMonths, diffDays, endOfMonth, isWeekend, maxDay, minDay, monthFull, monthShort, startOfMonth, startOfWeek, WEEKDAYS_SHORT } from './dates'

/** Пикселей на день при масштабе 100% (как в 1.0.x — сохранённый масштаб выглядит так же). */
export const BASE_PX = { day: 26, week: 16, month: 22, quarter: 20 }
export const ZOOM_STEPS = [0.05, 0.08, 0.1, 0.15, 0.2, 0.25, 0.33, 0.5, 0.67, 0.75, 1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6]
export const SCALES = [
  { key: 'day', label: 'Дни' },
  { key: 'week', label: 'Недели' },
  { key: 'month', label: 'Месяцы' },
  { key: 'quarter', label: 'Кварталы' },
]

export function pxPerDay(scale, zoom) {
  return Math.max(2, Math.round((BASE_PX[scale] || 22) * (zoom || 1)))
}

export function nextZoom(zoom, direction) {
  if (direction > 0) return ZOOM_STEPS.find((z) => z > zoom + 1e-9) ?? ZOOM_STEPS[ZOOM_STEPS.length - 1]
  return [...ZOOM_STEPS].reverse().find((z) => z < zoom - 1e-9) ?? ZOOM_STEPS[0]
}

/** Масштаб, при котором весь период помещается в ширину. */
export function fitZoom(range, scale, width) {
  const days = diffDays(range.start, range.end) + 1
  const zoom = width / days / (BASE_PX[scale] || 22)
  return Math.max(0.02, Math.min(6, Math.floor(zoom * 1000) / 1000))
}

export function totalDays(range) {
  return Math.max(1, diffDays(range.start, range.end) + 1)
}

export function dayX(range, px, day) {
  return diffDays(range.start, day) * px
}

export function xToDay(range, px, x) {
  return addDays(range.start, Math.floor(x / px))
}

/**
 * Заголовок шкалы: верхний ряд (месяцы или годы) и нижний (дни, недели, месяцы, кварталы).
 * Подписи нижнего ряда разрежаются, если ячейки слишком узкие.
 */
export function buildHeader(range, scale, px, calendar, today) {
  const start = range.start
  const end = range.end
  const top = []
  const bottom = []
  const clip = (from, to) => {
    const a = maxDay(from, start)
    const b = minDay(to, end)
    if (a > b) return null
    return { x: dayX(range, px, a), w: (diffDays(a, b) + 1) * px }
  }

  if (scale === 'day' || scale === 'week') {
    let month = startOfMonth(start)
    while (month <= end) {
      const box = clip(month, endOfMonth(month))
      const [y, m] = month.split('-').map(Number)
      if (box) top.push({ ...box, label: box.w > 90 ? `${monthFull(m - 1)} ${y}` : `${monthShort(m - 1)} ${String(y).slice(2)}` })
      month = addMonths(month, 1)
    }
  } else {
    let year = Number(start.slice(0, 4))
    while (`${year}-01-01` <= end) {
      const box = clip(`${year}-01-01`, `${year}-12-31`)
      if (box) top.push({ ...box, label: String(year) })
      year += 1
    }
  }

  if (scale === 'day') {
    const mode = px >= 22 ? 'full' : px >= 15 ? 'number' : 'week'
    let day = start
    while (day <= end) {
      const x = dayX(range, px, day)
      const wd = new Date(Date.UTC(...ymd(day))).getUTCDay()
      const cell = {
        x, w: px, day,
        weekend: isWeekend(day),
        holiday: calendar ? calendar.isHoliday(day) : false,
        workingWeekend: calendar ? calendar.isWorkingWeekend(day) : false,
        today: day === today,
      }
      if (mode === 'full') { cell.label = day.slice(8); cell.sub = WEEKDAYS_SHORT[wd] }
      else if (mode === 'number') cell.label = String(Number(day.slice(8)))
      else if (wd === 1) cell.label = String(Number(day.slice(8)))
      bottom.push(cell)
      day = addDays(day, 1)
    }
  } else if (scale === 'week') {
    let week = startOfWeek(start)
    while (week <= end) {
      const box = clip(week, addDays(week, 6))
      if (box) bottom.push({ ...box, label: box.w >= 34 ? `${Number(week.slice(8))}.${week.slice(5, 7)}` : '' , day: week })
      week = addDays(week, 7)
    }
  } else if (scale === 'month') {
    let month = startOfMonth(start)
    while (month <= end) {
      const box = clip(month, endOfMonth(month))
      const m = Number(month.slice(5, 7))
      if (box) bottom.push({ ...box, label: box.w >= 60 ? monthFull(m - 1) : box.w >= 22 ? monthShort(m - 1) : '', day: month })
      month = addMonths(month, 1)
    }
  } else {
    let quarter = `${start.slice(0, 4)}-${String(Math.floor((Number(start.slice(5, 7)) - 1) / 3) * 3 + 1).padStart(2, '0')}-01`
    while (quarter <= end) {
      const qEnd = addDays(addMonths(quarter, 3), -1)
      const box = clip(quarter, qEnd)
      const q = Math.floor((Number(quarter.slice(5, 7)) - 1) / 3) + 1
      if (box) bottom.push({ ...box, label: box.w >= 28 ? `${q} кв.` : `${q}`, day: quarter })
      quarter = addMonths(quarter, 3)
    }
  }
  return { top, bottom }
}

function ymd(value) {
  const [y, m, d] = value.split('-').map(Number)
  return [y, m - 1, d]
}

/** Вертикальные линии сетки: границы дней (дневная шкала) или ячеек нижнего ряда. */
export function gridLines(header) {
  return header.bottom.map((cell) => cell.x)
}

/**
 * Подписи вех и пометок над шкалой без наложения: подпись занимает по ширине примерно
 * длину текста; если на уровне уже есть пересекающаяся подпись — уровнем ниже.
 * Возвращает [{ ...label, level }] и число уровней.
 */
export function layoutMilestoneLabels(labels, _minGap = 80, charWidth = 5.6) {
  const sorted = [...labels].sort((a, b) => a.x - b.x)
  const levelEnds = []
  const out = sorted.map((label) => {
    const width = Math.min(260, (label.text || '').length * charWidth + 14)
    const left = label.kind === 'note' ? label.x - 6 : label.x - width / 2
    const right = left + width + (label.kind === 'note' ? 10 : 0)
    let level = 0
    while (level < levelEnds.length && left < levelEnds[level] + 6) level += 1
    levelEnds[level] = right
    return { ...label, level, width }
  })
  return { labels: out, levels: Math.max(1, levelEnds.length) }
}

/** Геометрия полосы задачи в пикселях шкалы. */
export function taskBar(task, range, px) {
  const x = dayX(range, px, task.start)
  const w = Math.max(10, (diffDays(task.start, task.end) + 1) * px)
  const offset = Number(task.offset || 0)
  return { x, w, offset, tailW: Math.abs(offset) * px }
}
