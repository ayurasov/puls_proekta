import { describe, expect, it } from 'vitest'
import { buildImportRows, currentSprint, foreignJiraHost, issueDates, issueKeyFromLinks, milestoneFromVersion, taskFromIssue } from '../jira'

/** 0.5.0: «Задачи из Jira» — строки по эпикам и спринтам, связи «блокирует», вехи релизов, другие адреса Jira. */
const BASE = 'https://jira.example.ru'
const OLD = 'https://jira.old.ru'
const TODAY = '2026-10-31'
const issue = (key, fields = {}) => ({
  key, url: `${BASE}/browse/${key}`, summary: `Задача ${key}`, status: 'Открыта', statusCategory: 'new', start: null,
  due: null, resolved: null, progress: null, epic: null, isEpic: false, sprints: [], links: { blockedBy: [], blocks: [] }, ...fields,
})
const sprint = (id, start, end) => ({ id, name: `Спринт ${id}`, state: 'active', start, end })

describe('другие адреса этой Jira', () => {
  it('ссылка на старый адрес распознаётся, на чужую Jira — показывает её хост', () => {
    expect(issueKeyFromLinks(`${OLD}/browse/abc-1`, BASE, [OLD])).toBe('ABC-1')
    expect(issueKeyFromLinks(`${OLD}/browse/ABC-1`, BASE, [])).toBeNull()
    expect(foreignJiraHost(`${OLD}/browse/ABC-1`, BASE, [])).toBe('jira.old.ru')
    expect(foreignJiraHost('https://jira.other.ru:8443/secure/RapidBoard.jspa?selectedIssue=X-2', BASE)).toBe('jira.other.ru:8443')
    expect(foreignJiraHost(`${OLD}/browse/ABC-1`, BASE, [OLD])).toBeNull()
    expect(foreignJiraHost('https://wiki.example.ru/page', BASE)).toBeNull()
    expect(foreignJiraHost('не ссылка', BASE)).toBeNull()
  })
})

describe('сроки строки из задачи Jira', () => {
  it('срок, затем спринт, затем дата решения, затем сегодня', () => {
    expect(issueDates(issue('A-1', { due: '2026-11-20' }), TODAY)).toEqual({ start: '2026-11-16', end: '2026-11-20' })
    expect(issueDates(issue('A-1', { start: '2026-11-01', due: '2026-11-20' }), TODAY)).toEqual({ start: '2026-11-01', end: '2026-11-20' })
    expect(issueDates(issue('A-1', { sprints: [sprint(5, '2026-10-05', '2026-10-19'), sprint(6, '2026-10-19', '2026-11-02')] }), TODAY))
      .toEqual({ start: '2026-10-19', end: '2026-11-02' })
    expect(issueDates(issue('A-1', { statusCategory: 'done', resolved: '2026-10-10' }), TODAY)).toEqual({ start: '2026-10-06', end: '2026-10-10' })
    expect(issueDates(issue('A-1'), TODAY)).toEqual({ start: TODAY, end: '2026-11-04' })
    // Срок раньше начала спринта: начало — за пять дней до срока
    expect(issueDates(issue('A-1', { due: '2026-10-15', sprints: [sprint(6, '2026-10-19', '2026-11-02')] }), TODAY)).toEqual({ start: '2026-10-11', end: '2026-10-15' })
  })

  it('завершённая задача — прогресс 100 и дата завершения', () => {
    const row = taskFromIssue(issue('A-1', { statusCategory: 'done', due: '2026-10-20', resolved: '2026-10-22' }), TODAY)
    expect(row).toMatchObject({ type: 'task', ref: 'A-1', data: { status: 'done', progress: 100, completedDate: '2026-10-22', offset: 2, jiraUrl: `${BASE}/browse/A-1` } })
    expect(taskFromIssue(issue('A-2', { statusCategory: 'indeterminate', progress: 40, due: '2026-11-01' }), TODAY).data).toMatchObject({ status: 'inwork', progress: 40 })
    expect(currentSprint(issue('A-3'))).toBeNull()
  })
})

describe('строки для добавления', () => {
  const issues = [
    issue('A-1', { due: '2026-11-10', sprints: [sprint(6, '2026-10-19', '2026-11-02')] }),
    issue('A-2', { due: '2026-11-20', epic: 'A-10', sprints: [sprint(7, '2026-11-02', '2026-11-16')], links: { blockedBy: ['A-1', 'A-9'], blocks: [] } }),
    issue('A-3', { due: '2026-11-25', epic: 'A-10', links: { blockedBy: ['X-5'], blocks: [] } }),
    issue('A-10', { isEpic: true, summary: 'Личный кабинет' }),
  ]

  it('без групп — задачи подряд, связи «блокирует» среди добавляемых и строк плана', () => {
    const rows = buildImportRows(issues.slice(0, 3), { links: true, linkedRows: new Map([['X-5', { key: 'i-old', end: '2026-11-01' }]]), today: TODAY })
    expect(rows.map((r) => r.ref)).toEqual(['A-1', 'A-2', 'A-3'])
    expect(rows[1].data).toMatchObject({ dependencyRef: 'A-1', dependencyType: 'FS' })
    expect(rows[2].data).toMatchObject({ dependencyKey: 'i-old', dependencyType: 'FS' })
    expect(rows[0].data.dependencyRef).toBeUndefined()
    // Без флажка связей — без предшественников
    expect(buildImportRows(issues.slice(0, 3), { today: TODAY }).every((r) => !r.data.dependencyRef && !r.data.dependencyKey)).toBe(true)
  })

  it('по эпикам: эпик — группа (существующая используется повторно), выбранный эпик — только группа', () => {
    const rows = buildImportRows(issues, { group: 'epics', epics: { 'A-10': { summary: 'Личный кабинет', url: `${BASE}/browse/A-10` } }, today: TODAY })
    expect(rows.map((r) => r.type)).toEqual(['task', 'group'])
    expect(rows[1]).toMatchObject({ type: 'group', data: { name: 'Личный кабинет', jiraUrl: `${BASE}/browse/A-10` } })
    expect(rows[1].children.map((c) => c.ref)).toEqual(['A-2', 'A-3'])
    const reused = buildImportRows(issues, { group: 'epics', epicGroups: new Map([['A-10', 'g-epic']]), today: TODAY })
    expect(reused.map((r) => [r.ref, r.parentKey])).toEqual([['A-1', undefined], ['A-2', 'g-epic'], ['A-3', 'g-epic']])
  })

  it('по спринтам: группа на спринт, существующая группа с тем же названием используется', () => {
    const rows = buildImportRows(issues.slice(0, 3), { group: 'sprints', today: TODAY })
    expect(rows.map((r) => r.type === 'group' ? `группа ${r.data.name}: ${r.children.map((c) => c.ref).join(',')}` : r.ref))
      .toEqual(['группа Спринт 6: A-1', 'группа Спринт 7: A-2', 'A-3'])
    const reused = buildImportRows(issues.slice(0, 1), { group: 'sprints', groupsByName: new Map([['Спринт 6', 'g6']]), today: TODAY })
    expect(reused).toEqual([expect.objectContaining({ ref: 'A-1', parentKey: 'g6' })])
  })

  it('релиз — веха на дату выпуска со ссылкой на релиз', () => {
    expect(milestoneFromVersion({ name: 'Релиз 2', release: '2026-12-15', url: `${BASE}/projects/A/versions/100` }))
      .toEqual({ type: 'milestone', data: { name: 'Релиз 2', date: '2026-12-15', jiraUrl: `${BASE}/projects/A/versions/100` } })
  })
})
