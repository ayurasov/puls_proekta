/** Форматирование дат, чисел и склонений для интерфейса (локаль ru-RU). */

const DATE = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
const DATE_SHORT = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' })
const DATETIME = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
const TIME = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' })

/** 'YYYY-MM-DD' → Date в местной полуночи (без сдвига часового пояса). */
export function parseDay(value) {
  if (!value) return null
  if (value instanceof Date) return value
  const [y, m, d] = String(value).slice(0, 10).split('-').map(Number)
  return new Date(y, m - 1, d)
}

export function parseTimestamp(value) {
  if (!value) return null
  return value instanceof Date ? value : new Date(value)
}

export function formatDate(value) {
  if (!value) return '—'
  const date = String(value).length <= 10 ? parseDay(value) : parseTimestamp(value)
  return date ? DATE.format(date) : '—'
}

export function formatDayShort(value) {
  const date = parseDay(value)
  return date ? DATE_SHORT.format(date).replace('.', '') : '—'
}

export function formatDateTime(value) {
  const date = parseTimestamp(value)
  return date ? DATETIME.format(date) : '—'
}

export function formatTime(value) {
  const date = parseTimestamp(value)
  return date ? TIME.format(date) : ''
}

/** «только что», «5 мин назад», «вчера в 14:10», иначе дата и время. */
export function formatRelative(value, now = new Date()) {
  const date = parseTimestamp(value)
  if (!date) return ''
  const seconds = Math.round((now - date) / 1000)
  if (seconds < 45) return 'только что'
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} мин назад`
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  const diff = Math.round((today - day) / 86400000)
  if (diff === 0) return `сегодня в ${TIME.format(date)}`
  if (diff === 1) return `вчера в ${TIME.format(date)}`
  return DATETIME.format(date)
}

export function pluralize(n, one, few, many) {
  const abs = Math.abs(n) % 100
  const last = abs % 10
  if (abs > 10 && abs < 20) return many
  if (last > 1 && last < 5) return few
  if (last === 1) return one
  return many
}
