import { describe, expect, it } from 'vitest'
import { issueKeyFromUrl, jiraChanges, jiraLinkFromInput } from '../jira'

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
