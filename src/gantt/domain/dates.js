/**
 * Календарные дни в формате 'YYYY-MM-DD'. Вся арифметика — через номер дня (UTC),
 * поэтому переход на летнее/зимнее время и часовой пояс не сдвигают даты.
 */

const DAY_MS = 86400000

export function dayNumber(value) {
  const [y, m, d] = String(value).slice(0, 10).split('-').map(Number)
  return Math.round(Date.UTC(y, m - 1, d) / DAY_MS)
}

export function fromDayNumber(n) {
  const date = new Date(n * DAY_MS)
  const y = date.getUTCFullYear()
  const m = String(date.getUTCMonth() + 1).padStart(2, '0')
  const d = String(date.getUTCDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

export function addDays(value, n) {
  return fromDayNumber(dayNumber(value) + n)
}

/** Сколько дней от a до b (b − a). */
export function diffDays(a, b) {
  return dayNumber(b) - dayNumber(a)
}

/** 0 — воскресенье … 6 — суббота. */
export function weekday(value) {
  return new Date(dayNumber(value) * DAY_MS).getUTCDay()
}

export function isWeekend(value) {
  const wd = weekday(value)
  return wd === 0 || wd === 6
}

/**
 * Сегодня по местному времени. В 1.0.x «сегодня» бралось из UTC (toISOString) — с 00:00
 * до 03:00 по Москве линия «сегодня» стояла на вчерашнем дне.
 */
export function todayLocal(now = new Date()) {
  const y = now.getFullYear()
  const m = String(now.getMonth() + 1).padStart(2, '0')
  const d = String(now.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

export function isValidDay(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  return fromDayNumber(dayNumber(value)) === value
}

export function minDay(a, b) {
  if (!a) return b
  if (!b) return a
  return a < b ? a : b
}

export function maxDay(a, b) {
  if (!a) return b
  if (!b) return a
  return a > b ? a : b
}

export function clampDay(value, min, max) {
  if (min && value < min) return min
  if (max && value > max) return max
  return value
}

export function startOfMonth(value) {
  return String(value).slice(0, 8) + '01'
}

export function endOfMonth(value) {
  const [y, m] = String(value).split('-').map(Number)
  const next = m === 12 ? `${y + 1}-01-01` : `${y}-${String(m + 1).padStart(2, '0')}-01`
  return addDays(next, -1)
}

export function addMonths(value, n) {
  const [y, m] = String(value).split('-').map(Number)
  const total = y * 12 + (m - 1) + n
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, '0')}-01`
}

/** Понедельник недели, в которую попадает день. */
export function startOfWeek(value) {
  const wd = weekday(value)
  return addDays(value, -((wd + 6) % 7))
}

const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
const MONTHS_GEN = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
const MONTHS_FULL = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']
export const WEEKDAYS_SHORT = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб']

export function monthShort(monthIndex) {
  return MONTHS_SHORT[monthIndex]
}

export function monthFull(monthIndex) {
  return MONTHS_FULL[monthIndex]
}

/** «5 мая» */
export function dayMonth(value) {
  const [, m, d] = String(value).split('-').map(Number)
  return `${d} ${MONTHS_GEN[m - 1]}`
}

/** «05.05.2026» */
export function formatDay(value) {
  if (!value) return '—'
  const [y, m, d] = String(value).split('-')
  return `${d}.${m}.${y}`
}

/** «05.05» */
export function formatDayNoYear(value) {
  if (!value) return '—'
  const [, m, d] = String(value).split('-')
  return `${d}.${m}`
}
