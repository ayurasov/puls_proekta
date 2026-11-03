import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Сервер подменяется: проверяется очередь отправки редактора, а не HTTP
const server = vi.hoisted(() => ({ handler: null, posts: [] }))
vi.mock('../../api/client', async () => {
  class ApiError extends Error {
    constructor(message, status, payload) { super(message); this.status = status; this.code = payload?.error || null }
  }
  class AuthRequiredError extends ApiError {}
  class NetworkError extends ApiError {}
  return {
    ApiError, AuthRequiredError, NetworkError,
    api: {
      get: (url, params) => server.handler('GET', url, params),
      post: (url, body) => { server.posts.push(body); return server.handler('POST', url, body) },
      delete: () => Promise.resolve({ ok: true }),
    },
  }
})

// Среда тестов — node: редактору нужны только подписки на события окна и документа
const listeners = { addEventListener() {}, removeEventListener() {} }
globalThis.window = { ...listeners }
globalThis.document = { ...listeners, visibilityState: 'visible', createElement: () => ({}) }

const { createPlanEditor, hasUnsavedEdits } = await import('../editor')
const { linksFromItems } = await import('../domain/dependencies')
const { addDays } = await import('../domain/dates')
const { ApiError, AuthRequiredError } = await import('../../api/client')

const task = (key, name, rev = 1) => ({
  key, type: 'task', name, parentKey: null, position: 1024, status: 'new', start: '2026-11-02', end: '2026-11-06',
  endMode: 'date', durationType: 'calendar', progress: 0, offset: 0, color: 'blue', updatedRev: rev,
})
// Связи плана (0.7.0) — отдельным списком; у строк из сценариев до 0.7.0 — по полю dependencyKey
const doc = (items, revision = 1, links = linksFromItems(items)) => ({
  plan: { id: 1, key: 'p1', title: 'План', revision, access: 'edit', settings: {}, isArchived: false },
  items: items.map(({ dependencyKey: _key, dependencyType: _type, dependencyLag: _lag, ...item }) => item), links, notes: [], assignees: [],
  users: {},
})
const ui = { toast: vi.fn(), error: vi.fn(), success: vi.fn() }

async function openEditor(items, links = undefined) {
  let revision = 1
  server.handler = async (method, url) => {
    if (method === 'GET' && url === '/calendar') return []
    if (method === 'GET' && url === '/plans/1') return doc(items, revision, links)
    if (method === 'POST') { revision += 1; return { revision, items: [], links: [], notes: [], assignees: [] } }
    if (method === 'GET') return { revision, unchanged: true, presence: [] }
    throw new Error(`unexpected ${method} ${url}`)
  }
  const editor = createPlanEditor({ planId: 1, ui })
  await editor.load()
  return editor
}

async function settle(ms = 200) {
  await vi.advanceTimersByTimeAsync(ms)
}

describe('очередь отправки редактора', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    server.posts = []
    ui.toast.mockClear(); ui.error.mockClear()
  })
  afterEach(() => vi.useRealTimers())

  it('закрытый план (переход на другую вкладку) досылает неотправленные правки', async () => {
    const editor = await openEditor([task('a', 'A')])
    editor.updateItem('a', { name: 'A2' }, 'Переименование')
    editor.stop() // раньше, чем истекли 120 мс до отправки
    expect(hasUnsavedEdits()).toBe(true)
    await settle()
    expect(server.posts).toHaveLength(1)
    expect(server.posts[0].ops[0]).toMatchObject({ op: 'item.update', key: 'a', fields: { name: 'A2' } })
    expect(hasUnsavedEdits()).toBe(false)
  })

  it('отклонённая правка не уносит остальные и их «Отменить»', async () => {
    const editor = await openEditor([task('a', 'A'), task('b', 'B'), task('c', 'C')])
    const base = server.handler
    server.handler = async (method, url, body) => {
      if (method === 'POST' && body.ops.some((op) => op.key === 'b')) throw new ApiError('Строка удалена другим сотрудником', 409, { error: 'gone' })
      return base(method, url, body)
    }
    editor.updateItem('a', { name: 'A2' }, 'Правка A')
    editor.updateItem('b', { name: 'B2' }, 'Правка B')
    editor.updateItem('c', { name: 'C2' }, 'Правка C')
    await settle(1000)
    const sent = server.posts.map((p) => p.ops.map((op) => op.key).join(','))
    expect(sent).toEqual(['a,b,c', 'a', 'b', 'c']) // пакет отклонён → по одной
    expect(ui.error).toHaveBeenCalledTimes(1)
    expect(ui.error.mock.calls[0][0]).toContain('Правка B')
    expect(editor.state.undo.map((e) => e.label)).toEqual(['Правка A', 'Правка C'])
  })

  it('ответ опроса, пришедший позже собственного сохранения, не откатывает строку', async () => {
    const editor = await openEditor([task('a', 'OLD', 1)])
    editor.state.items.a = { ...editor.state.items.a, name: 'NEW', updatedRev: 3 }
    server.handler = async () => ({ revision: 2, items: [task('a', 'OLD', 2)], notes: [], assignees: [], presence: [], events: [] })
    editor.state.plan.revision = 1
    await editor.poll()
    expect(editor.state.items.a.name).toBe('NEW')
  })

  it('«Отменить» из уведомления отменяет именно удаление, а не последнюю правку', async () => {
    const editor = await openEditor([task('a', 'A'), task('b', 'B')])
    const removal = editor.deleteItems(['a'], { label: 'Удаление A' })
    editor.updateItem('b', { name: 'B2' }, 'Правка B')
    editor.undo(removal)
    expect(editor.state.items.a).toBeTruthy()
    expect(editor.state.items.b.name).toBe('B2')
    expect(editor.state.undo.map((e) => e.label)).toEqual(['Правка B'])
  })

  it('истёкшая сессия: правки ждут и уходят сами после входа', async () => {
    const editor = await openEditor([task('a', 'A')])
    const base = server.handler
    let loggedIn = false
    server.handler = async (method, url, body) => {
      if (method === 'POST' && !loggedIn) throw new AuthRequiredError('Нужно войти', 401)
      return base(method, url, body)
    }
    editor.updateItem('a', { name: 'A2' }, 'Переименование')
    await settle(200)
    expect(editor.state.authLost).toBe(true)
    expect(editor.pendingCount).toBe(1)
    loggedIn = true
    await settle(3000)
    expect(editor.pendingCount).toBe(0)
    expect(editor.state.authLost).toBe(false)
  })

  it('«Отменить» в режиме просмотра не теряет запись истории', async () => {
    const editor = await openEditor([task('a', 'A')])
    editor.updateItem('a', { name: 'A2' }, 'Переименование')
    editor.state.access = 'view'
    editor.undo()
    expect(editor.state.undo).toHaveLength(1)
  })

  it('переименование не сдвигает несогласованные задачи в других местах плана', async () => {
    const editor = await openEditor([
      task('a', 'A'), { ...task('b', 'B'), start: '2026-12-01', end: '2026-12-02', dependencyKey: 'a' }, task('c', 'C'),
    ])
    editor.updateItem('c', { name: 'C2' }, 'Переименование')
    expect(editor.state.items.b.start).toBe('2026-12-01')
    await settle()
    expect(server.posts[0].ops).toEqual([{ op: 'item.update', key: 'c', fields: { name: 'C2' } }])
  })

  it('перенос предшественника сдвигает зависящую задачу', async () => {
    const editor = await openEditor([task('a', 'A'), { ...task('b', 'B'), start: '2026-11-07', end: '2026-11-11', dependencyKey: 'a' }])
    editor.updateItem('a', { start: '2026-11-09', end: '2026-11-13' }, 'Сдвиг')
    expect(editor.state.items.b.start).toBe('2026-11-14')
  })
})

describe('ширина колонки названий', () => {
  it('по умолчанию зависит от ширины экрана', async () => {
    const { defaultGridWidth, fitGridWidth } = await import('../editor')
    expect(defaultGridWidth(390)).toBe(160)
    expect(defaultGridWidth(1366)).toBe(301)
    expect(defaultGridWidth(1920)).toBe(380)
    // сохранённая широкая колонка на телефоне не занимает больше половины экрана
    expect(fitGridWidth(420, 390)).toBe(176)
    expect(fitGridWidth(420, 1920)).toBe(420)
  })
})

describe('сдвиг завершённой задачи', () => {
  beforeEach(() => { vi.useFakeTimers(); server.posts = [] })
  afterEach(() => vi.useRealTimers())

  it('перенос сроков пересчитывает сдвиг от фактической даты завершения', async () => {
    const editor = await openEditor([{ ...task('a', 'A'), status: 'done', completedDate: '2026-11-08', offset: 2, progress: 100 }])
    editor.updateItem('a', { end: '2026-11-10' }, 'Сроки')
    expect(editor.state.items.a.offset).toBe(-2)
    editor.undo()
    expect(editor.state.items.a.offset).toBe(2)
  })

  it('незавершённой задаче сдвиг при переносе сроков не меняется', async () => {
    const editor = await openEditor([{ ...task('a', 'A'), offset: 3 }])
    editor.updateItem('a', { end: '2026-11-10' }, 'Сроки')
    expect(editor.state.items.a.offset).toBe(3)
  })
})

describe('0.4.0: несколько строк одной правкой', () => {
  beforeEach(() => { vi.useFakeTimers(); server.posts = [] })
  afterEach(() => vi.useRealTimers())

  it('новая группа с задачами внутри и строка в существующую группу — одна правка', async () => {
    const editor = await openEditor([{ ...task('g', 'Эпик'), type: 'group' }, { ...task('t', 'Старая'), parentKey: 'g' }])
    const keys = editor.createItems([
      { type: 'group', data: { name: 'Новый эпик' }, children: [{ type: 'task', data: { name: 'A' } }, { type: 'task', data: { name: 'B' } }] },
      { type: 'task', data: { name: 'C' }, parentKey: 'g' },
      { type: 'task', data: { name: 'D' } },
    ], { label: 'Задачи из Jira (4)' })
    expect(keys).toHaveLength(5)
    const [group, a, b, c, d] = keys.map((k) => editor.state.items[k])
    expect([a.parentKey, b.parentKey]).toEqual([group.key, group.key])
    expect(a.position).toBeLessThan(b.position)
    expect(c.parentKey).toBe('g')
    expect(c.position).toBeGreaterThan(editor.state.items.t.position)
    expect(group.parentKey).toBeNull()
    expect(d.parentKey).toBeNull()
    editor.undo()
    expect(keys.every((k) => !editor.state.items[k])).toBe(true)
  })
})

describe('0.5.0: строки из Jira со связями «блокирует»', () => {
  beforeEach(() => { vi.useFakeTimers(); server.posts = [] })
  afterEach(() => vi.useRealTimers())

  it('предшественник по метке из того же списка и автосдвиг — одной правкой', async () => {
    const editor = await openEditor([task('t', 'Старая')])
    const before = editor.state.items.t.end
    const keys = editor.createItems([
      { type: 'task', ref: 'A-1', data: { name: 'Первая', start: '2026-05-01', end: '2026-05-10' } },
      { type: 'task', ref: 'A-2', data: { name: 'Вторая', start: '2026-05-03', end: '2026-05-06' }, predecessors: [{ ref: 'A-1', type: 'FS' }] },
      { type: 'task', data: { name: 'К старой', start: '2026-04-01', end: '2026-04-03' }, predecessors: [{ key: 't' }, { ref: 'A-1', type: 'SS' }] },
      { type: 'task', data: { name: 'К несуществующей' }, predecessors: [{ key: 'нет-такой' }] },
    ], { label: 'Задачи из Jira (4)' })
    const [first, second, third, fourth] = keys.map((k) => editor.state.items[k])
    const pairs = (key) => editor.linkList.filter((l) => l.to === key).map((l) => [l.from, l.type])
    expect(pairs(second.key)).toEqual([[first.key, 'FS']])
    // Автосдвиг: вторая начинается после первой
    expect(second.start > first.end).toBe(true)
    // Два предшественника: начало — по наибольшему из ограничений
    expect(pairs(third.key)).toEqual([['t', 'FS'], [first.key, 'SS']])
    expect(third.start > before).toBe(true)
    expect(pairs(fourth.key)).toEqual([])
    editor.undo()
    expect(keys.every((k) => !editor.state.items[k])).toBe(true)
    expect(editor.linkList).toEqual([])
  })
})

describe('0.4.0: перенос из Jira в цепочке связей', () => {
  beforeEach(() => { vi.useFakeTimers(); server.posts = [] })
  afterEach(() => vi.useRealTimers())

  const chain = () => [
    { ...task('a', 'A'), start: '2026-11-02', end: '2026-11-06' },
    { ...task('b', 'B'), start: '2026-11-09', end: '2026-11-13', dependencyKey: 'a', dependencyType: 'FS' },
    { ...task('c', 'C'), start: '2026-11-16', end: '2026-11-20', dependencyKey: 'b', dependencyType: 'FS' },
  ]

  it('срок последующей задачи из Jira не перезаписывается сдвигом за предшественником', async () => {
    const editor = await openEditor(chain())
    editor.updateItems([
      { key: 'a', fields: { start: '2026-11-02', end: '2026-11-10' } },
      { key: 'b', fields: { start: '2026-11-09', end: '2026-11-20' } },
    ], 'Из Jira', { keepFreeDates: true })
    expect([editor.state.items.b.start, editor.state.items.b.end]).toEqual(['2026-11-11', '2026-11-20'])
    // Следующая по цепочке — сдвигается от нового окончания B, длительность сохраняется
    expect([editor.state.items.c.start, editor.state.items.c.end]).toEqual(['2026-11-21', '2026-11-25'])
  })

  it('обычная правка сдвигает последующую задачу целиком, как раньше', async () => {
    const editor = await openEditor(chain())
    editor.updateItems([{ key: 'a', fields: { end: '2026-11-10' } }, { key: 'b', fields: { end: '2026-11-20' } }], 'Правка')
    expect([editor.state.items.b.start, editor.state.items.b.end]).toEqual(['2026-11-11', '2026-11-22'])
  })

  it('автоперенос не отнимает «Повторить»', async () => {
    const editor = await openEditor(chain())
    editor.updateItem('c', { name: 'C2' }, 'Переименование')
    editor.undo()
    expect(editor.state.redo).toHaveLength(1)
    editor.updateItems([{ key: 'a', fields: { progress: 30 } }], 'Из Jira автоматически (1)', { keepRedo: true })
    expect(editor.state.redo).toHaveLength(1)
    editor.updateItems([{ key: 'a', fields: { progress: 40 } }], 'Правка')
    expect(editor.state.redo).toHaveLength(0)
  })
})

describe('0.7.0: связи — отдельные операции', () => {
  beforeEach(() => { vi.useFakeTimers(); server.posts = []; ui.error.mockClear() })
  afterEach(() => vi.useRealTimers())

  const rows = () => [
    { ...task('a', 'A'), start: '2026-11-02', end: '2026-11-06' },
    { ...task('b', 'B'), start: '2026-11-02', end: '2026-11-10' },
    { ...task('c', 'C'), start: '2026-11-02', end: '2026-11-03' },
    { key: 'm', type: 'milestone', name: 'Сдача', date: '2026-12-01', position: 2048, updatedRev: 1 },
  ]

  it('второй предшественник: последующая задача — по наибольшему ограничению; отмена и повтор', async () => {
    const editor = await openEditor(rows(), [{ key: 'l1', from: 'a', to: 'c', type: 'FS', lag: 0, lagKind: 'calendar' }])
    const key = editor.createLink({ from: 'b', to: 'c' }, 'Новая связь')
    expect(editor.linkList.map((l) => [l.from, l.to])).toEqual([['a', 'c'], ['b', 'c']])
    expect([editor.state.items.c.start, editor.state.items.c.end]).toEqual(['2026-11-11', '2026-11-12'])
    await settle()
    expect(server.posts[0].ops).toEqual([
      { op: 'link.create', link: { key, from: 'b', to: 'c', type: 'FS', lag: 0, lagKind: 'calendar' } },
      { op: 'item.update', key: 'c', fields: { start: '2026-11-11', end: '2026-11-12' } },
    ])
    editor.undo()
    expect(editor.state.links[key]).toBeUndefined()
    expect(editor.state.items.c.start).toBe('2026-11-02')
    await settle()
    expect(server.posts[1].ops).toEqual([
      { op: 'item.update', key: 'c', fields: { start: '2026-11-02', end: '2026-11-03' } },
      { op: 'link.delete', key },
    ])
    editor.redo()
    expect(editor.state.links[key].from).toBe('b')
    expect(editor.state.items.c.start).toBe('2026-11-11')
    await settle()
    expect(server.posts[2].ops[0]).toEqual({ op: 'link.restore', key })
  })

  it('правка лага и типа сдвигает последующую; удаление связи — по оставшимся', async () => {
    const editor = await openEditor(rows(), [
      { key: 'l1', from: 'a', to: 'c', type: 'FS', lag: 0, lagKind: 'calendar' },
      { key: 'l2', from: 'b', to: 'c', type: 'FS', lag: 0, lagKind: 'calendar' },
    ])
    editor.updateLink('l1', { lag: 10 })
    expect(editor.state.items.c.start).toBe('2026-11-17')
    // SS от A (пн 02.11) + 10 рабочих дней — через два выходных: 16.11
    editor.updateLink('l1', { type: 'SS', lagKind: 'working' })
    expect(editor.state.links.l1).toMatchObject({ type: 'SS', lag: 10, lagKind: 'working' })
    expect(editor.state.items.c.start).toBe('2026-11-16')
    editor.deleteLink('l1')
    expect(editor.state.items.c.start).toBe('2026-11-11')
    editor.undo()
    expect(editor.state.links.l1).toBeTruthy()
    expect(editor.state.items.c.start).toBe('2026-11-16')
  })

  it('связь, замыкающая цикл, или повтор — не создаётся, сотрудник видит причину', async () => {
    const editor = await openEditor(rows(), [{ key: 'l1', from: 'a', to: 'b', type: 'FS', lag: 0, lagKind: 'calendar' }])
    expect(editor.createLink({ from: 'b', to: 'a' })).toBeNull()
    expect(ui.error).toHaveBeenLastCalledWith(expect.stringMatching(/цикл/))
    expect(editor.createLink({ from: 'a', to: 'b' })).toBeNull()
    expect(ui.error).toHaveBeenLastCalledWith('Связь «A» → «B» уже есть')
    expect(editor.dependentsOf('a')).toEqual(new Set(['b']))
    await settle()
    expect(server.posts).toHaveLength(0)
  })

  it('удаление строки удаляет её связи, отмена возвращает и строку, и связи', async () => {
    const editor = await openEditor(rows(), [
      { key: 'l1', from: 'a', to: 'b', type: 'FS', lag: 0, lagKind: 'calendar' },
      { key: 'l2', from: 'b', to: 'm', type: 'FS', lag: 0, lagKind: 'calendar' },
      { key: 'l3', from: 'a', to: 'c', type: 'FS', lag: 0, lagKind: 'calendar' },
    ])
    editor.deleteItems(['b'])
    expect(Object.keys(editor.state.links)).toEqual(['l3'])
    expect(editor.linksOf('m').predecessors).toEqual([])
    await settle()
    expect(server.posts[0].ops).toEqual([
      { op: 'link.delete', key: 'l1' }, { op: 'link.delete', key: 'l2' }, { op: 'item.delete', key: 'b' },
    ])
    editor.undo()
    expect(Object.keys(editor.state.links).sort()).toEqual(['l1', 'l2', 'l3'])
    expect(editor.state.items.b).toBeTruthy()
    await settle()
    expect(server.posts[1].ops).toEqual([
      { op: 'item.restore', key: 'b' }, { op: 'link.restore', key: 'l2' }, { op: 'link.restore', key: 'l1' },
    ])
  })

  it('«Последующая задача» и веха-последующая; копия строки — без связей', async () => {
    const editor = await openEditor(rows())
    const next = editor.createItem('task', { start: '2026-11-07', end: '2026-11-08', name: 'После A' }, { afterKey: 'a', predecessors: [{ from: 'a', type: 'FS' }] })
    expect(editor.linksOf(next).predecessors.map((l) => l.from)).toEqual(['a'])
    expect(editor.state.items[next].start).toBe('2026-11-07')
    editor.createLink({ from: 'b', to: 'm' })
    expect(editor.state.items.m.date).toBe('2026-11-10')
    const copy = editor.duplicateItem(next)
    expect(editor.linksOf(copy)).toEqual({ predecessors: [], successors: [] })
  })

  it('«Начало не ранее» сдвигает задачу, крайний срок — нет', async () => {
    const editor = await openEditor(rows())
    editor.updateItem('c', { deadline: '2026-11-01' })
    expect(editor.state.items.c.start).toBe('2026-11-02')
    editor.updateItem('c', { constraintType: 'snet', constraintDate: '2026-11-20' }, 'Начало не ранее')
    expect([editor.state.items.c.start, editor.state.items.c.end]).toEqual(['2026-11-20', '2026-11-21'])
    await settle()
    expect(server.posts.at(-1).ops.slice(-2)).toEqual([
      { op: 'item.update', key: 'c', fields: { constraintType: 'snet', constraintDate: '2026-11-20' } },
      { op: 'item.update', key: 'c', fields: { start: '2026-11-20', end: '2026-11-21' } },
    ])
  })

  it('лаг в рабочих днях — по календарю плана', async () => {
    const editor = await openEditor(rows(), [{ key: 'l1', from: 'c', to: 'b', type: 'FS', lag: 0, lagKind: 'working' }])
    // C теперь заканчивается в пятницу 06.11: B начинается в понедельник 09.11, а не в субботу
    editor.updateItem('c', { end: '2026-11-06' })
    expect(editor.state.items.b.start).toBe('2026-11-09')
    editor.updateLink('l1', { lagKind: 'calendar' })
    expect(editor.state.items.b.start).toBe('2026-11-07')
  })
})

describe('ревью 0.7.0: автосдвиг больших планов и связи', () => {
  beforeEach(() => { vi.useFakeTimers(); server.posts = []; ui.error.mockClear() })
  afterEach(() => vi.useRealTimers())

  const N = 5000
  // Цепочка FS из N однодневных задач, согласованная со связями; reverse — строки в обратном порядке
  const chain = ({ reverse = false } = {}) => {
    const items = Array.from({ length: N }, (_, k) => ({ ...task(`t${k}`, `T${k}`), start: addDays('2026-01-01', k), end: addDays('2026-01-01', k), position: reverse ? N - k : k }))
    const links = Array.from({ length: N - 1 }, (_, k) => ({ key: `l${k}`, from: `t${k}`, to: `t${k + 1}`, type: 'FS', lag: 0, lagKind: 'calendar' }))
    return [reverse ? items.reverse() : items, links]
  }

  it('правка с автосдвигом 5000 строк уходит частями до 1500 операций, по порядку; отмена — одна и тоже частями', async () => {
    const editor = await openEditor(...chain())
    await settle()
    server.posts = []
    editor.updateItem('t0', { end: '2026-01-02' }, 'Срок первой задачи')
    expect(editor.state.items[`t${N - 1}`].start).toBe(addDays('2026-01-01', N))
    await settle(1000)
    expect(server.posts.map((p) => p.ops.length)).toEqual([1500, 1500, 1500, 500])
    const sent = server.posts.flatMap((p) => p.ops)
    expect(sent[0]).toEqual({ op: 'item.update', key: 't0', fields: { end: '2026-01-02' } })
    expect(sent.at(-1).key).toBe(`t${N - 1}`)
    expect(editor.state.undo.map((e) => e.label)).toEqual(['Срок первой задачи'])
    server.posts = []
    editor.undo()
    expect(editor.state.items.t1.start).toBe('2026-01-02')
    await settle(1000)
    expect(server.posts.map((p) => p.ops.length)).toEqual([1500, 1500, 1500, 500])
    expect(editor.pendingCount).toBe(0)
  })

  it('часть большой правки отклонена — остальные части не уходят, отмена снимается, план перечитывается', async () => {
    const editor = await openEditor(...chain())
    const base = server.handler
    let posts = 0
    server.handler = async (method, url, body) => {
      if (method === 'POST' && ++posts === 2) throw new ApiError('Строка удалена другим сотрудником', 409, { error: 'gone' })
      return base(method, url, body)
    }
    editor.updateItem('t0', { end: '2026-01-02' }, 'Срок первой задачи')
    await settle(1000)
    expect(server.posts.map((p) => p.ops.length)).toEqual([1500, 1500])
    expect(ui.error).toHaveBeenCalledTimes(1)
    expect(editor.state.undo).toEqual([])
    expect(editor.pendingCount).toBe(0)
  })

  it('«Выровнять по связям» на тысячах строк — тоже частями', async () => {
    const [items, links] = chain()
    const editor = await openEditor(items.map((i) => ({ ...i, start: '2026-01-01', end: '2026-01-01' })), links)
    const ops = editor.cascadeOps(new Map(), { all: true })
    expect(ops).toHaveLength(N - 1)
    editor.commit(ops, 'Выравнивание по связям')
    await settle(1000)
    expect(server.posts.map((p) => p.ops.length)).toEqual([1500, 1500, 1500, 499])
  })

  it('автосдвиг цепочки в обратном порядке строк — один обход, без зависания', async () => {
    const editor = await openEditor(...chain({ reverse: true }))
    const started = Date.now()
    const ops = editor.cascadeOps(new Map([['t0', { end: '2026-01-02' }]]))
    expect(ops).toHaveLength(N - 1)
    expect(Date.now() - started).toBeLessThan(3000)
  })

  it('смена статуса без смены дат не переставляет несогласованные последующие', async () => {
    const editor = await openEditor([
      { ...task('a', 'A'), start: '2026-11-02', end: '2026-11-06' },
      { ...task('b', 'B'), start: '2026-12-01', end: '2026-12-02' }, // давно не согласована (например, из MS Project)
    ], [{ key: 'l1', from: 'a', to: 'b', type: 'FS', lag: 0, lagKind: 'calendar' }])
    editor.setStatus('a', 'inwork')
    expect(editor.state.items.b.start).toBe('2026-12-01')
    // Завершение в плановый срок окончание для последующих не меняет
    editor.setStatus('a', 'done', '2026-11-06')
    expect(editor.state.items.b.start).toBe('2026-12-01')
    // Завершение позже срока — меняет: последующая встаёт после фактической даты
    editor.setStatus('a', 'inwork')
    editor.setStatus('a', 'done', '2026-11-10')
    expect(editor.state.items.b.start).toBe('2026-11-11')
  })

  it('удаление предшественника: последующая встаёт по оставшимся связям, отмена возвращает всё', async () => {
    const editor = await openEditor([
      { ...task('a', 'A'), start: '2026-11-02', end: '2026-11-06' },
      { ...task('b', 'B'), start: '2026-11-02', end: '2026-11-12' },
      { ...task('c', 'C'), start: '2026-11-13', end: '2026-11-14' },
    ], [
      { key: 'l1', from: 'a', to: 'c', type: 'FS', lag: 0, lagKind: 'calendar' },
      { key: 'l2', from: 'b', to: 'c', type: 'FS', lag: 0, lagKind: 'calendar' },
    ])
    editor.deleteItems(['b'])
    expect([editor.state.items.c.start, editor.state.items.c.end]).toEqual(['2026-11-07', '2026-11-08'])
    editor.undo()
    expect(editor.state.items.c.start).toBe('2026-11-13')
    expect(Object.keys(editor.state.links).sort()).toEqual(['l1', 'l2'])
  })

  it('копия строки — без идентификаторов MS Project', async () => {
    const editor = await openEditor([{ ...task('a', 'A'), extra: { msp: { uid: 7, guid: 'g' }, legacyGroupId: 'x' } },
      { ...task('b', 'B'), extra: { msp: { uid: 8 } } }])
    const copyA = editor.duplicateItem('a')
    const copyB = editor.duplicateItem('b')
    expect(editor.state.items[copyA].extra).toEqual({ legacyGroupId: 'x' })
    expect(editor.state.items[copyB].extra).toBeUndefined()
    expect(editor.state.items.a.extra.msp.uid).toBe(7)
  })

  it('связь с родительской или вложенной задачей не создаётся', async () => {
    const editor = await openEditor([task('p', 'Родитель'), { ...task('c', 'Вложенная'), parentKey: 'p' }])
    expect(editor.createLink({ from: 'p', to: 'c' })).toBeNull()
    expect(editor.createLink({ from: 'c', to: 'p' })).toBeNull()
    expect(ui.error).toHaveBeenLastCalledWith('Нельзя связать «Вложенная» и «Родитель»: одна строка вложена в другую')
  })
})

describe('0.7.1: поиск по номеру строки', () => {
  beforeEach(() => { vi.useFakeTimers(); server.posts = [] })
  afterEach(() => vi.useRealTimers())

  it('номер ищется только при включённой колонке «Номер»', async () => {
    const items = [task('a', 'Анализ'), { ...task('b', 'Сборка'), position: 2048 }, { ...task('c', 'Тест 2'), position: 3072 }]
    const editor = await openEditor(items)
    const names = () => editor.visibleRows.map((r) => r.item.name)
    editor.state.view.search = '2'
    expect(names()).toEqual(['Тест 2'])
    editor.state.view.columns.wbs = true
    expect(names()).toEqual(['Сборка', 'Тест 2'])
  })
})
