import { describe, expect, it } from 'vitest'
import { createCalendar, resolveEnd } from '../calendar'
import { addDays, diffDays, isValidDay, startOfWeek, todayLocal, weekday } from '../dates'

// Праздники 2026 года — как в 1.0.1
const HOLIDAYS_2026 = ['2026-01-01', '2026-01-02', '2026-01-03', '2026-01-04', '2026-01-05', '2026-01-06', '2026-01-07',
  '2026-01-08', '2026-01-09', '2026-01-10', '2026-01-11', '2026-02-23', '2026-03-09', '2026-05-01', '2026-05-11',
  '2026-06-12', '2026-11-04', '2026-12-31']

describe('dates', () => {
  it('arithmetic is timezone safe', () => {
    expect(addDays('2026-03-28', 2)).toBe('2026-03-30')
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01')
    expect(diffDays('2026-01-01', '2026-12-31')).toBe(364)
    expect(weekday('2026-10-30')).toBe(5)
    expect(startOfWeek('2026-11-01')).toBe('2026-10-26')
    expect(isValidDay('2026-02-30')).toBe(false)
  })
  it('today is local, not UTC (bug of 1.0.x between 00:00 and 03:00 MSK)', () => {
    const justAfterMidnight = new Date(2026, 9, 30, 0, 30)
    expect(todayLocal(justAfterMidnight)).toBe('2026-10-30')
  })
})

describe('calendar', () => {
  const ru = createCalendar({ holidays: HOLIDAYS_2026, useRuCalendar: true })
  it('working days honour RF holidays', () => {
    expect(ru.isWorkingDay('2026-01-09')).toBe(false)
    expect(ru.isWorkingDay('2026-01-12')).toBe(true)
    // 28, 29, 30 декабря, (31 — праздник), 1 января 2027 (дней 2027 года в этом наборе нет), 4 января
    expect(ru.addWorkingDays('2026-12-28', 5)).toBe('2027-01-04')
    expect(ru.addWorkingDays('2026-01-01', 1)).toBe('2026-01-12')
    expect(ru.countWorkingDays('2026-01-01', '2026-01-16')).toBe(5)
  })
  it('working saturday counts when RF calendar is on', () => {
    const cal = createCalendar({ holidays: ['2027-02-22', '2027-02-23'], workdays: ['2027-02-20'] })
    expect(cal.isWorkingDay('2027-02-20')).toBe(true)
    expect(cal.addWorkingDays('2027-02-19', 2)).toBe('2027-02-20')
    expect(cal.addWorkingDays('2027-02-19', 3)).toBe('2027-02-24')
  })
  it('without RF calendar only weekends are days off', () => {
    const plain = createCalendar({ holidays: HOLIDAYS_2026, useRuCalendar: false })
    expect(plain.isWorkingDay('2026-01-09')).toBe(true)
    expect(plain.isWorkingDay('2026-01-10')).toBe(false)
  })
  it('resolves end by duration', () => {
    expect(resolveEnd(ru, '2026-05-04', 'duration', null, 5, 'calendar')).toBe('2026-05-08')
    expect(resolveEnd(ru, '2026-05-04', 'duration', null, 5, 'working')).toBe('2026-05-08')
    expect(resolveEnd(ru, '2026-05-07', 'duration', null, 5, 'working')).toBe('2026-05-14')
    expect(resolveEnd(ru, '2026-05-07', 'date', '2026-05-09', 5, 'working')).toBe('2026-05-09')
  })
})
