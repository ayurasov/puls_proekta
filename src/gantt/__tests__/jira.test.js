import { describe, expect, it, vi } from 'vitest'
import { createJira, issueKeyFromUrl, jiraChanges, jiraLinkFromInput } from '../jira'

const BASE = 'https://jira.example.ru'
const task = (fields = {}) => ({
  key: 'i1', type: 'task', name: 'Сборка', status: 'new', start: '2026-11-02', end: '2026-11-06', endMode: 'date',
  durationType: 'calendar', progress: 0, offset: 0, ...fields,
})
const issue = (fields = {}) => ({
  key: 'ABC-1', summary: 'Сборка', status: 'В работе', statusCategory: 'new', start: null, due: '2026-11-06',
  resolved: null, progress: null, ...fields,
})

describe('ссылки на задачи Jira', () => {
  it('ключ из ссылки только на подключённую Jira', () => {
    expect(issueKeyFromUrl(`${BASE}/browse/abc-12`, BASE)).toBe('ABC-12')
    expect(issueKeyFromUrl(`${BASE}/secure/RapidBoard.jspa?selectedIssue=XY-7`, BASE)).toBe('XY-7')
    expect(issueKeyFromUrl('https://other.ru/browse/ABC-12', BASE)).toBeNull()
    expect(issueKeyFromUrl('не ссылка', BASE)).toBeNull()
    expect(issueKeyFromUrl(`${BASE}/jira/browse/A-1`, `${BASE}/jira`)).toBe('A-1')
    expect(issueKeyFromUrl(`${BASE}/browse/A-1`, `${BASE}/jira`)).toBeNull()
  })

  it('ключ в поле превращается в ссылку', () => {
    expect(jiraLinkFromInput(' abc-5 ', `${BASE}/`)).toEqual({ url: `${BASE}/browse/ABC-5` })
    expect(jiraLinkFromInput('ABC-5', null).error).toBeTruthy()
    expect(jiraLinkFromInput('https://x.ru/browse/A-1', BASE)).toEqual({ url: 'https://x.ru/browse/A-1' })
    expect(jiraLinkFromInput('ftp://x', BASE).error).toBeTruthy()
    expect(jiraLinkFromInput('', BASE)).toEqual({ url: null })
  })
})

describe('что перенести из Jira', () => {
  it('совпадающая задача — ничего', () => {
    expect(jiraChanges(task(), issue())).toEqual([])
  })

  it('название, статус, сроки и прогресс', () => {
    const changes = jiraChanges(task(), issue({
      summary: 'Сборка стенда', statusCategory: 'indeterminate', start: '2026-11-03', due: '2026-11-10', progress: 40,
    }))
    expect(changes.map((c) => c.field)).toEqual(['name', 'status', 'dates', 'progress'])
    expect(changes[2].fields).toEqual({ start: '2026-11-03', end: '2026-11-10' })
    expect(changes[3].fields).toEqual({ progress: 40 })
  })

  it('решённая задача: завершение с датой решения, прогресс 100', () => {
    const [status] = jiraChanges(task({ status: 'inwork' }), issue({ statusCategory: 'done', resolved: '2026-11-09' }))
    expect(status.fields).toEqual({ status: 'done', completedDate: '2026-11-09', offset: 3, progress: 100 })
  })

  it('«Заблокирована» не меняется на «В работе»; возврат из «Завершена» сбрасывает завершение', () => {
    expect(jiraChanges(task({ status: 'blocked' }), issue({ statusCategory: 'indeterminate' }))).toEqual([])
    const [status] = jiraChanges(task({ status: 'done', completedDate: '2026-11-06' }), issue({ statusCategory: 'new' }))
    expect(status.fields).toEqual({ status: 'new', completedDate: null, offset: 0 })
  })

  it('срок раньше начала не предлагается; длительность пересчитывается', () => {
    expect(jiraChanges(task(), issue({ due: '2026-10-01' }))).toEqual([])
    const [dates] = jiraChanges(task({ endMode: 'duration', duration: 5 }), issue({ due: '2026-11-08' }))
    expect(dates.fields).toEqual({ start: '2026-11-02', end: '2026-11-08', duration: 7 })
  })

  it('без данных в Jira поле не трогается', () => {
    expect(jiraChanges(task({ progress: 30 }), issue({ due: null, progress: null }))).toEqual([])
    expect(jiraChanges(task(), null)).toEqual([])
  })
})

describe('ревью 0.3.0: перенос из Jira', () => {
  it('«Заблокирована» меняется только на «Завершена»', () => {
    expect(jiraChanges(task({ status: 'blocked' }), issue({ statusCategory: 'new' }))).toEqual([])
    expect(jiraChanges(task({ status: 'blocked' }), issue({ statusCategory: 'done', resolved: '2026-11-06' }))[0].fields.status).toBe('done')
  })

  it('сроки задачи со связью: из Jira — только свободная дата', () => {
    const fs = jiraChanges(task({ dependencyKey: 'p', dependencyType: 'FS' }), issue({ start: '2026-11-03', due: '2026-11-09' }), { linked: true })
    expect(fs[0].fields).toEqual({ start: '2026-11-02', end: '2026-11-09' })
    expect(fs[0].note).toBe('начало задаёт связь')
    const ff = jiraChanges(task({ dependencyKey: 'p', dependencyType: 'FF' }), issue({ start: '2026-11-01', due: '2026-11-09' }), { linked: true })
    expect(ff[0].fields).toEqual({ start: '2026-11-01', end: '2026-11-06' })
  })
})

describe('0.4.0: автоперенос из Jira', () => {
  function setup({ auto = ['status'], canEdit = true } = {}) {
    const items = {
      a: task({ key: 'a', jiraUrl: `${BASE}/browse/ABC-1` }),
      b: task({ key: 'b', jiraUrl: `${BASE}/browse/ABC-2`, status: 'inwork' }),
      c: task({ key: 'c' }),
    }
    const editor = {
      settings: { jiraAuto: auto, display: { autoShift: true } }, canEdit, calendar: null,
      itemList: Object.values(items), state: { items, today: '2026-11-04', plan: { id: 1 }, access: 'edit' },
      updateItems: vi.fn(), updatePlan: vi.fn((fields) => { editor.settings.jiraAuto = fields.settings.jiraAuto }),
    }
    const ui = { success: vi.fn(), error: vi.fn() }
    const jira = createJira(editor, ui)
    Object.assign(jira.state, { connected: true, baseUrl: BASE, loaded: true })
    jira.state.issues = {
      'ABC-1': issue({ key: 'ABC-1', statusCategory: 'indeterminate', due: '2026-11-10' }),
      'ABC-2': issue({ key: 'ABC-2', statusCategory: 'indeterminate' }),
    }
    return { editor, jira, ui }
  }

  it('переносит только выбранные поля, одной правкой и один раз', () => {
    const { editor, jira, ui } = setup()
    expect(jira.autoApply()).toBe(1)
    expect(editor.updateItems).toHaveBeenCalledTimes(1)
    const [list, label] = editor.updateItems.mock.calls[0]
    expect(list).toEqual([{ key: 'a', fields: { status: 'inwork' } }])
    expect(label).toBe('Из Jira автоматически (1)')
    expect(ui.success).toHaveBeenCalled()
    // Уже перенесённое не повторяется при следующем обновлении (например, после Ctrl+Z)
    expect(jira.autoApply()).toBe(0)
  })

  it('выключен или нет прав на правку — ничего не меняет', () => {
    expect(setup({ auto: [] }).jira.autoApply()).toBe(0)
    const { editor, jira } = setup({ canEdit: false })
    expect(jira.autoApply()).toBe(0)
    expect(editor.updateItems).not.toHaveBeenCalled()
  })

  it('включение автопереноса — правка настроек плана и сразу перенос', () => {
    const { editor, jira } = setup({ auto: [] })
    jira.setAutoFields(['status', 'dates'])
    expect(editor.updatePlan).toHaveBeenCalledWith({ settings: { jiraAuto: ['status', 'dates'] } }, 'Автоперенос из Jira')
    const fields = editor.updateItems.mock.calls[0][0].find((c) => c.key === 'a').fields
    expect(fields).toMatchObject({ status: 'inwork', end: '2026-11-10' })
  })

  it('после привязки строки — только её задача', () => {
    const { editor, jira } = setup({ auto: ['status'] })
    jira.state.issues['ABC-2'] = issue({ key: 'ABC-2', statusCategory: 'done', resolved: '2026-11-03' })
    expect(jira.autoApply(['ABC-2'])).toBe(1)
    expect(editor.updateItems.mock.calls[0][0].map((c) => c.key)).toEqual(['b'])
  })
})
