/**
 * План из MS Project согласован со связями «Пульса» (ADR-0012, раздел 3: «даты в файле — главные»).
 *
 * Документы fixtures/msproject-*.json пишет сервер (backend/tests/test_msproject.py ::
 * test_план_согласован_со_связями_пульса): строки и связи документа загрузки после подбора лагов
 * (backend/app/services/msproject/schedule.py) и производственный календарь, по которому лаги подобраны.
 * Здесь их проверяет тот же расчёт, что в плане: computeDependencyShifts по всем строкам и связям с календарём
 * плана — как баннер «Сроки N строк не согласованы со связями» (GanttWorkspace) и «Выровнять по связям»
 * (editor.cascadeOps с all: true, при включённом автосдвиге). Ни одна строка не должна сдвинуться.
 */
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { computeDependencyShifts } from '../dependencies'
import { createCalendar } from '../calendar'

const dir = join(__dirname, 'fixtures')
const files = readdirSync(dir).filter((name) => /^msproject-.*\.json$/.test(name)).sort()

describe('план из MS Project и автосдвиг по связям', () => {
  it('документы с сервера на месте', () => {
    expect(files).toEqual(['msproject-6day.json', 'msproject-crm-working.json',
      'msproject-crm.json', 'msproject-generated-baseline.json', 'msproject-generated.json'])
  })

  for (const name of files) {
    it(`${name}: автосдвиг не двигает ни одной строки`, () => {
      const doc = JSON.parse(readFileSync(join(dir, name), 'utf8'))
      const calendar = createCalendar({ holidays: doc.calendar.holidays, workdays: doc.calendar.workdays, useRuCalendar: true })
      expect(doc.links.length).toBeGreaterThan(0)
      const shifts = computeDependencyShifts(doc.items, { links: doc.links, calendar })
      expect(Object.fromEntries(shifts)).toEqual({})
    })
  }

  it('проверка не пустая: лаг «как в файле» (SF 0) сдвинул бы строку', () => {
    const doc = JSON.parse(readFileSync(join(dir, 'msproject-crm.json'), 'utf8'))
    const calendar = createCalendar({ holidays: doc.calendar.holidays, workdays: doc.calendar.workdays, useRuCalendar: true })
    const links = doc.links.map((link) => (link.key === 'l20-21' ? { ...link, lag: 0 } : link))
    expect([...computeDependencyShifts(doc.items, { links, calendar }).keys()]).toEqual(['i21'])
  })
})
