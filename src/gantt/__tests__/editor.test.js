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
const { ApiError, AuthRequiredError } = await import('../../api/client')

const task = (key, name, rev = 1) => ({
  key, type: 'task', name, parentKey: null, position: 1024, status: 'new', start: '2026-11-02', end: '2026-11-06',
  endMode: 'date', durationType: 'calendar', progress: 0, offset: 0, dependencyType: 'FS', dependencyLag: 0,
  color: 'blue', updatedRev: rev,
})
const doc = (items, revision = 1) => ({
  plan: { id: 1, key: 'p1', title: 'План', revision, access: 'edit', settings: {}, isArchived: false },
  items, notes: [], assignees: [], users: {},
})
const ui = { toast: vi.fn(), error: vi.fn(), success: vi.fn() }

async function openEditor(items) {
  let revision = 1
  server.handler = async (method, url) => {
    if (method === 'GET' && url === '/calendar') return []
    if (method === 'GET' && url === '/plans/1') return doc(items, revision)
    if (method === 'POST') { revision += 1; return { revision, items: [], notes: [], assignees: [] } }
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
