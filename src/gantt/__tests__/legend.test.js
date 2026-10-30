import { describe, expect, it } from 'vitest'
import { legendEntries, NO_CATEGORY } from '../chartModel'

const task = (key, category, color = 'blue', extra = {}) => ({ key, type: 'task', name: `Задача ${key}`, category, color, ...extra })
const milestone = (key, name, color) => ({ key, type: 'milestone', name, color, lineColor: color })

describe('легенда', () => {
  it('задачи без категории — одной записью «Без категории», категории — в порядке настроек', () => {
    const entries = legendEntries([
      task('a', 'Разработка', 'blue'), task('b', null, 'green'), task('c', null, 'green'), task('d', null, 'red'),
      task('e', 'Аналитика', 'purple'), task('f', 'Разработка', 'orange'),
    ], { legendMode: 'all' }, ['Аналитика', 'Разработка'])
    expect(entries.map((e) => e.label)).toEqual(['Аналитика', 'Разработка', NO_CATEGORY])
    expect(entries[1].color).toBe('#3b82f6') // цвет первой задачи категории
    expect(entries[2].color).toBe('#22c55e') // самый частый цвет задач без категории
  })

  it('без категорий в плане задачи в легенду не попадают (раньше — запись на каждую задачу)', () => {
    const entries = legendEntries([task('a', null), task('b', null), task('c', null)], { legendMode: 'all' })
    expect(entries).toEqual([])
  })

  it('вехи — по цвету, а не цветом последней вехи', () => {
    const one = legendEntries([milestone('m1', 'Старт', 'teal'), milestone('m2', 'Финиш', 'teal')], { legendMode: 'all' })
    expect(one).toEqual([{ label: 'Вехи', color: '#14b8a6', kind: 'milestone', title: 'Старт, Финиш' }])
    const two = legendEntries([milestone('m1', 'Старт', 'teal'), milestone('m2', 'Финиш', 'red'), milestone('m3', 'Приёмка', 'red')], { legendMode: 'all' })
    expect(two.map((e) => [e.label, e.color])).toEqual([['Старт', '#14b8a6'], ['Вехи: Финиш и ещё 1', '#ef4444']])
  })
})
