import { describe, expect, it } from 'vitest'
import {
  directionMatches, isMyProject, matchesProjectFilters, normalizeProjectFilters, projectFiltersActive, projectProgress,
  projectState, sortProjects,
} from '../projectFilters'

const project = (id, title, stats = null, extra = {}) => ({ id, title, stats, access: 'view', createdById: 1, directionId: null, ...extra })
const stats = (total, done, overdue, progress, end = null) => ({ total, done, overdue, progress, end })

describe('состояние и прогресс проекта', () => {
  it('просрочен, завершён, в срок, без задач', () => {
    expect(projectState(project(1, 'А', stats(4, 1, 2, 40)))).toBe('overdue')
    expect(projectState(project(1, 'А', stats(4, 4, 0, 100)))).toBe('done')
    expect(projectState(project(1, 'А', stats(2, 1, 0, 100)))).toBe('done') // средний прогресс 100 %
    expect(projectState(project(1, 'А', stats(4, 1, 0, 60)))).toBe('ontrack')
    expect(projectState(project(1, 'А', stats(0, 0, 0, 0)))).toBe('empty')
    expect(projectState(project(1, 'А'))).toBe('empty')
  })

  it('прогресс — как на карточке; без поля progress — по завершённым', () => {
    expect(projectProgress(project(1, 'А', { total: 4, done: 1 }))).toBe(25)
    expect(projectProgress(project(1, 'А', stats(4, 1, 0, 70)))).toBe(70)
    expect(projectProgress(project(1, 'А'))).toBe(0)
  })
})

describe('фильтры главной', () => {
  it('«Мои» — автор или право редактирования', () => {
    const me = { id: 7 }
    expect(isMyProject(project(1, 'А', null, { createdById: 7 }), me)).toBe(true)
    expect(isMyProject(project(1, 'А', null, { access: 'edit' }), me)).toBe(true)
    // Администратор правит всё — для него «Мои» только созданные им
    const admin = { ...me, role: 'admin' }
    expect(isMyProject(project(1, 'А', null, { access: 'manage' }), admin)).toBe(false)
    expect(isMyProject(project(1, 'А', null, { access: 'manage', createdById: me.id }), admin)).toBe(true)
    expect(isMyProject(project(1, 'А', null, { access: 'manage' }), me)).toBe(true)
    expect(isMyProject(project(1, 'А'), me)).toBe(false)
    expect(isMyProject(project(1, 'А', null, { createdById: 7 }), null)).toBe(false)
  })

  it('состояние и «Мои» вместе', () => {
    const p = project(1, 'А', stats(4, 1, 2, 40), { access: 'edit' })
    expect(matchesProjectFilters(p, { state: 'overdue', mine: true }, { id: 3 })).toBe(true)
    expect(matchesProjectFilters(p, { state: 'done', mine: false }, { id: 3 })).toBe(false)
    expect(matchesProjectFilters({ ...p, access: 'view' }, { state: '', mine: true }, { id: 3 })).toBe(false)
  })

  it('направление: все, конкретное, без направления', () => {
    expect(directionMatches(5, '')).toBe(true)
    expect(directionMatches(5, '5')).toBe(true)
    expect(directionMatches(5, '6')).toBe(false)
    expect(directionMatches(null, 'none')).toBe(true)
    expect(directionMatches(5, 'none')).toBe(false)
  })

  it('сохранённый выбор очищается от неизвестных значений', () => {
    expect(normalizeProjectFilters({ direction: '12', state: 'overdue', mine: true, sort: 'end' }))
      .toEqual({ direction: '12', state: 'overdue', mine: true, sort: 'end' })
    expect(normalizeProjectFilters({ direction: 'x', state: 'late', mine: 'yes', sort: 'random' }))
      .toEqual({ direction: '', state: '', mine: false, sort: 'order' })
    expect(normalizeProjectFilters(null)).toEqual({ direction: '', state: '', mine: false, sort: 'order' })
    expect(projectFiltersActive({ direction: '', state: '', mine: false, sort: 'title' })).toBe(false)
    expect(projectFiltersActive({ direction: 'none', state: '', mine: false, sort: 'order' })).toBe(true)
  })
})

describe('сортировка', () => {
  const list = [
    project(1, 'Проект 10', stats(2, 0, 0, 80, '2026-12-01')),
    project(2, 'проект 2', stats(2, 0, 0, 20, '2026-11-01')),
    project(3, 'Альфа'),
    project(4, 'Бета', stats(3, 0, 0, 20, null)),
  ]
  const ids = (sorted) => sorted.map((p) => p.id)
  it('по порядку — как есть', () => expect(ids(sortProjects(list, 'order'))).toEqual([1, 2, 3, 4]))
  it('по названию — без учёта регистра, числа по значению', () => expect(ids(sortProjects(list, 'title'))).toEqual([3, 4, 2, 1]))
  it('по прогрессу — сначала отстающие, без задач — в конце', () => expect(ids(sortProjects(list, 'progress'))).toEqual([4, 2, 1, 3]))
  it('по сроку окончания — раньше выше, без сроков — в конце', () => expect(ids(sortProjects(list, 'end'))).toEqual([2, 1, 3, 4]))
})
