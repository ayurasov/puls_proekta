import { describe, expect, it, vi } from 'vitest'
import { buildIndex, flattenTree } from '../domain/tree'

vi.mock('../../api/client', () => ({ api: {}, saveBlob: vi.fn() }))
const { buildCsv } = await import('../export')

function editorWith(items, settings = {}) {
  const map = Object.fromEntries(items.map((i) => [i.key, i]))
  const index = buildIndex(items)
  return {
    state: { items: map, view: { columns: {} } }, settings, index, assigneeList: [], linkList: [],
    visibleRows: flattenTree(index, { expandAll: true }),
  }
}

const rows = [
  { key: 'g', type: 'group', name: 'Этап', position: 1, parentKey: null },
  { key: 'a', type: 'task', name: 'Анализ', position: 1, parentKey: 'g', start: '2026-11-02', end: '2026-11-06', extra: { msp: { wbs: 'A.1' } } },
  { key: 'm', type: 'milestone', name: 'Сдача', position: 2, parentKey: null, date: '2026-11-09' },
]

describe('0.7.1: номер строки в CSV (ADR-0012, 5.5)', () => {
  it('«№» — порядок выгрузки, «Номер» — по структуре плана', () => {
    const lines = buildCsv(editorWith(rows)).slice(1).split('\r\n')
    expect(lines[0].split(';').slice(0, 3)).toEqual(['№', 'Номер', 'Уровень'])
    expect(lines.slice(1).map((l) => l.split(';').slice(0, 2).join(';'))).toEqual(['1;1', '2;1.1', '3;2'])
  })

  it('у мастер-копии MS Project — коды WBS из файла, строки «Пульса» без номера', () => {
    const settings = { source: { kind: 'msproject', master: true } }
    const lines = buildCsv(editorWith(rows, settings)).slice(1).split('\r\n')
    expect(lines.slice(1).map((l) => l.split(';')[1])).toEqual(['', 'A.1', ''])
  })
})
