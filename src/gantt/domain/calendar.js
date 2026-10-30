import { addDays, isWeekend } from './dates'

/**
 * Производственный календарь: выходные, праздники и рабочие субботы.
 * Праздники и переносы приходят с сервера (таблица calendar_days, 2025–2027 и правки
 * администратора). Если в настройках плана выключен «Календарь РФ», рабочими считаются
 * все дни, кроме субботы и воскресенья (как в 1.0.x).
 */
export function createCalendar({ holidays = [], workdays = [], useRuCalendar = true } = {}) {
  const holidaySet = holidays instanceof Set ? holidays : new Set(holidays)
  const workdaySet = workdays instanceof Set ? workdays : new Set(workdays)

  function isHoliday(day) {
    return holidaySet.has(day)
  }

  function isWorkingWeekend(day) {
    return workdaySet.has(day)
  }

  function isWorkingDay(day) {
    if (!useRuCalendar) return !isWeekend(day)
    if (holidaySet.has(day)) return false
    if (workdaySet.has(day)) return true
    return !isWeekend(day)
  }

  /**
   * Окончание задачи длиной n рабочих дней от start (start входит в срок, если он рабочий).
   * Совпадает с addWorkingDays 1.0.x.
   */
  function addWorkingDays(start, n) {
    let remaining = Math.max(1, Math.round(Number(n) || 1))
    let day = start
    if (isWorkingDay(day)) remaining -= 1
    let guard = 0
    while (remaining > 0 && guard < 20000) {
      day = addDays(day, 1)
      if (isWorkingDay(day)) remaining -= 1
      guard += 1
    }
    return day
  }

  /** Число рабочих дней в отрезке [start, end], не меньше 1. */
  function countWorkingDays(start, end) {
    let count = 0
    let day = start
    let guard = 0
    while (day <= end && guard < 20000) {
      if (isWorkingDay(day)) count += 1
      day = addDays(day, 1)
      guard += 1
    }
    return Math.max(1, count)
  }

  return { isHoliday, isWorkingWeekend, isWorkingDay, addWorkingDays, countWorkingDays, useRuCalendar }
}

export function addCalendarDays(start, n) {
  return addDays(start, Math.max(0, Math.round(Number(n) || 1) - 1))
}

/** Окончание задачи по режиму: дата или длительность (календарные/рабочие дни). */
export function resolveEnd(calendar, start, mode, end, duration, durationType) {
  if (mode !== 'duration') return end
  return durationType === 'working' ? calendar.addWorkingDays(start, duration) : addCalendarDays(start, duration)
}

/** Длительность задачи в днях её типа. */
export function taskDuration(calendar, task) {
  if (!task.start || !task.end) return 1
  if (task.durationType === 'working') return calendar.countWorkingDays(task.start, task.end)
  return Math.max(1, (Date.UTC(...ymd(task.end)) - Date.UTC(...ymd(task.start))) / 86400000 + 1)
}

function ymd(value) {
  const [y, m, d] = value.split('-').map(Number)
  return [y, m - 1, d]
}
