import { describe, expect, it } from 'vitest'
import { buildIndex, outlineNumbers } from '../tree'

const row = (key, parentKey, position) => ({ key, parentKey, position, type: 'task', name: key })

describe('номера строк по структуре (WBS)', () => {
  it('1, 1.1, 1.2, 2 — по порядку строк в дереве', () => {
    const index = buildIndex([
      row('b', null, 2), row('a', null, 1), row('a2', 'a', 2), row('a1', 'a', 1), row('a1x', 'a1', 1), row('c', null, 3),
    ])
    expect(Object.fromEntries(outlineNumbers(index))).toEqual({ a: '1', a1: '1.1', a1x: '1.1.1', a2: '1.2', b: '2', c: '3' })
  })

  it('строка с несуществующим родителем — в корне; цикл не зацикливает', () => {
    const numbers = outlineNumbers(buildIndex([row('x', 'нет', 1), row('p', 'q', 1), row('q', 'p', 1), row('r', null, 2)]))
    expect(numbers.get('x')).toBe('1')
    expect(numbers.get('r')).toBe('2')
    expect(numbers.has('p')).toBe(false)
  })
})

describe('номера строк у мастер-копии MS Project', () => {
  it('коды WBS из файла; у строки, добавленной в «Пульсе», номера нет', async () => {
    const { rowNumbers } = await import('../tree')
    const index = buildIndex([
      { ...row('a', null, 1), extra: { msp: { wbs: 'A.1' } } }, { ...row('b', null, 2) },
    ])
    expect(Object.fromEntries(rowNumbers(index, { source: { kind: 'msproject', master: true } }))).toEqual({ a: 'A.1' })
    expect(Object.fromEntries(rowNumbers(index, { source: { kind: 'msproject', master: false } }))).toEqual({ a: '1', b: '2' })
  })
})
