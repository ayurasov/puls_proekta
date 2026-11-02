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
