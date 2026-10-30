import { describe, expect, it } from 'vitest'
import { applyBatch, mergeSettings, touchedKeys } from '../ops'
import { insertPosition, positionBetween } from '../positions'
import { buildIndex, groupSpan } from '../tree'

const state = () => ({
  plan: { title: 'План', settings: { scale: 'day', display: { showNotes: true, legendEnabled: false } } },
  items: { t1: { key: 't1', type: 'task', name: 'A', start: '2026-05-01', end: '2026-05-05', position: 1024 } },
  notes: {},
  assignees: {},
})

describe('local ops and undo', () => {
  it('update → inverse restores old values', () => {
    const s = state()
    const inverse = applyBatch(s, [{ op: 'item.update', key: 't1', fields: { name: 'B', end: '2026-05-09' } }])
    expect(s.items.t1.name).toBe('B')
    applyBatch(s, inverse)
    expect(s.items.t1).toMatchObject({ name: 'A', end: '2026-05-05' })
  })
  it('create/delete/restore round trip through the graveyard', () => {
    const s = state()
    const undoCreate = applyBatch(s, [{ op: 'item.create', item: { key: 'g1', type: 'group', name: 'G', position: 2048 } }])
    expect(Object.keys(s.items)).toEqual(['t1', 'g1'])
    const redo = applyBatch(s, undoCreate)
    expect(s.items.g1).toBeUndefined()
    expect(redo).toEqual([{ op: 'item.restore', key: 'g1' }])
    applyBatch(s, redo)
    expect(s.items.g1.name).toBe('G')
  })
  it('plan settings merge deeply and revert', () => {
    const s = state()
    const inverse = applyBatch(s, [{ op: 'plan.update', fields: { settings: { display: { showNotes: false } } } }])
    expect(s.plan.settings.display).toEqual({ showNotes: false, legendEnabled: false })
    applyBatch(s, inverse)
    expect(s.plan.settings.display.showNotes).toBe(true)
    expect(mergeSettings({ a: { b: 1 } }, { a: { c: 2 } })).toEqual({ a: { b: 1, c: 2 } })
  })
  it('touched keys', () => {
    expect([...touchedKeys([{ op: 'item.update', key: 'x' }, { op: 'note.create', note: { key: 'n' } }, { op: 'plan.update' }])])
      .toEqual(['item:x', 'note:n', 'plan'])
  })
})

describe('positions', () => {
  it('inserts between neighbours and renumbers when exhausted', () => {
    expect(positionBetween(1024, 2048)).toBe(1536)
    expect(positionBetween(null, 1024)).toBe(0)
    const siblings = [{ key: 'a', position: 1 }, { key: 'b', position: 1 + 1e-9 }]
    const { position, renumber } = insertPosition(siblings, 1)
    expect(renumber).toEqual([{ key: 'a', position: 1024 }, { key: 'b', position: 3072 }])
    expect(position).toBe(2048)
  })
})

describe('group span', () => {
  it('covers nested tasks recursively with shift', () => {
    const items = [
      { key: 'g', type: 'group', name: 'G', position: 1 },
      { key: 'g2', type: 'group', name: 'G2', parentKey: 'g', position: 2 },
      { key: 'a', type: 'task', parentKey: 'g2', start: '2026-05-01', end: '2026-05-05', offset: 3, position: 3, status: 'done' },
      { key: 'b', type: 'task', parentKey: 'g', start: '2026-04-20', end: '2026-04-25', position: 4 },
    ]
    const span = groupSpan(buildIndex(items), 'g')
    expect(span).toMatchObject({ start: '2026-04-20', end: '2026-05-05', shiftedEnd: '2026-05-08', offset: 3, count: 2, done: 1 })
  })
})
