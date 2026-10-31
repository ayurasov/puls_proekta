import { describe, expect, it, vi } from 'vitest'

const get = vi.fn()
vi.mock('../../api/client', () => ({ api: { get: (...args) => get(...args) } }))
const { createPodeluTasks, groupByItem, isOverdue, resetPodeluLinks, sortTasks } = await import('../podeluTasks')

/** 0.5.0: задачи «По Делу», которые ссылаются на проект и строки его планов (ADR-0010). */
const t = (id, fields = {}) => ({ id, title: `Задача ${id}`, done: false, due: null, itemKey: null, planId: null, ...fields })

describe('порядок и группы', () => {
  it('сначала открытые по сроку, без срока — в конце, затем выполненные', () => {
    const list = [t('a', { done: true, due: '2026-10-01' }), t('b'), t('c', { due: '2026-11-05' }), t('d', { due: '2026-10-31' })]
    expect(sortTasks(list).map((x) => x.id)).toEqual(['d', 'c', 'b', 'a'])
  })

  it('по строкам плана: проект целиком — первым, название строки — из плана', () => {
    const groups = groupByItem([
      t('a', { itemKey: 'k1', planId: 1, itemName: 'Старое название' }),
      t('b'),
      t('c', { itemKey: 'k2', planId: 1, itemName: 'Монтаж' }),
      t('d', { itemKey: 'k1', planId: 1 }),
    ], { k1: { name: 'Сборка' } })
    expect(groups.map((g) => [g.itemKey, g.itemName, g.tasks.map((x) => x.id)])).toEqual([
      [null, null, ['b']], ['k1', 'Сборка', ['a', 'd']], ['k2', 'Монтаж', ['c']],
    ])
  })

  it('просрочена только открытая задача со сроком в прошлом', () => {
    expect(isOverdue(t('a', { due: '2026-10-30' }), '2026-10-31')).toBe(true)
    expect(isOverdue(t('a', { due: '2026-10-30', done: true }), '2026-10-31')).toBe(false)
    expect(isOverdue(t('a'), '2026-10-31')).toBe(false)
  })
})

describe('загрузка', () => {
  it('выключено — ни одного запроса задач; включено — задачи и число открытых', async () => {
    resetPodeluLinks()
    get.mockReset()
    get.mockResolvedValueOnce({ enabled: false, baseUrl: null })
    const off = createPodeluTasks(7)
    await off.load()
    expect(off.state.enabled).toBe(false)
    expect(get).toHaveBeenCalledTimes(1)

    resetPodeluLinks()
    get.mockReset()
    get.mockResolvedValueOnce({ enabled: true, baseUrl: 'https://podelu.ru' })
    get.mockResolvedValueOnce({ enabled: true, tasks: [t('a'), t('b', { done: true }), t('c', { itemKey: 'k1', planId: 3 })], error: null })
    const on = createPodeluTasks(7)
    await on.load()
    expect(get).toHaveBeenLastCalledWith('/projects/7/podelu-tasks')
    expect(on.openCount).toBe(2)
    expect(on.forItem(3, 'k1').map((x) => x.id)).toEqual(['c'])
    expect(on.forItem(4, 'k1')).toEqual([])
  })

  it('ошибка «По Делу» не ломает проект', async () => {
    resetPodeluLinks()
    get.mockReset()
    get.mockResolvedValueOnce({ enabled: true })
    get.mockRejectedValueOnce(new Error('сеть'))
    const tasks = createPodeluTasks(1)
    await tasks.load()
    expect(tasks.state.error).toBe('сеть')
    expect(tasks.state.loaded).toBe(true)
  })
})
