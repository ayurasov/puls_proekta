import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { api, BASE, closeBrowser, login, newPage, shot } from '../helpers.mjs'

/**
 * «По Делу» (ADR-0009): администратор подключает «По Делу» в «Интеграциях», проверяет подключение,
 * получает план загрузки структуры и сотрудников, решает коллизию и применяет. «По Делу» — заглушка на
 * этом же компьютере, как todo-planner 1.4: CSRF-токен в сессии, вход с X-CSRF-Token и Referer, новая
 * cookie сессии после входа, выгрузки структуры (JSON) и сотрудников (ZIP с фото).
 */
const PORT = 5098
const PODELU = `http://127.0.0.1:${PORT}`
const PASSWORD = 'Пароль-сервиса-1'
const IDS = {
  company: '7d1f2b8e-5c3a-4e61-9b0f-2a9c1e4d5f60',
  dev: '0b5e7c2a-91d4-4f3b-8e6a-3c2d1f0e9a81',
  sales: 'c4a9e1f7-2b3d-4c5e-8f60-71a2b3c4d5e6',
  svc: '1a2b3c4d-0000-4000-8000-000000000001',
  sergeev: '1a2b3c4d-0000-4000-8000-000000000002',
  kozlova: '1a2b3c4d-0000-4000-8000-000000000003',
}
// Фото 1×1 PNG
const PHOTO = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64')

const account = { id: IDS.svc, name: 'Сервис Пульса', login: 'svc-puls', email: 'svc@almi-partner.ru', globalRole: 'admin', capabilities: [], isActive: true }
const structure = {
  format: 'podelu.org-structure', version: 1, exportedAt: '2026-10-30T10:00:00Z', app: '1.4.0',
  settings: { org_terminology: { head: 'Начальник' } },
  departments: [
    { key: IDS.company, name: 'АО «Вектор»', parent: null, kind: 'company', sortOrder: 0, externalId: null, heads: [] },
    { key: IDS.dev, name: 'Разработка', parent: IDS.company, kind: 'department', sortOrder: 1, externalId: null, heads: [{ email: 'sergeev.av@almi-partner.ru', primary: true }] },
    { key: IDS.sales, name: 'Продажи', parent: IDS.company, kind: 'department', sortOrder: 2, externalId: null, heads: [{ email: 'kozlova.ii@almi-partner.ru', primary: true }] },
  ],
}
const employee = (fields) => ({
  position: '', manager: '', reportsToNone: false, heads: [], functionalManagers: [], capabilities: [], timezone: 'Europe/Moscow',
  isActive: true, visibilityScope: '', authSource: 'local', externalId: '', ...fields,
})
const employees = {
  format: 'podelu.employees', version: 1, exportedAt: '2026-10-30T10:00:00Z', app: '1.4.0',
  employees: [
    employee({ email: 'sergeev.av@almi-partner.ru', login: 'sergeev.av', name: 'Сергеев Алексей Васильевич', position: 'Архитектор', department: ['АО «Вектор»', 'Разработка'], role: 'user' }),
    employee({ email: 'kozlova.ii@almi-partner.ru', login: 'kozlova.ii', name: 'Козлова Ирина Игоревна', position: 'Директор по продажам', department: ['АО «Вектор»', 'Продажи'], heads: [{ department: ['АО «Вектор»', 'Продажи'], primary: true }], role: 'admin', avatar: 'avatars/2.png' }),
  ],
}
const people = [
  { id: IDS.svc, email: account.email, login: account.login, isSystem: false },
  { id: IDS.sergeev, email: 'sergeev.av@almi-partner.ru', login: 'sergeev.av', isSystem: false },
  { id: IDS.kozlova, email: 'kozlova.ii@almi-partner.ru', login: 'kozlova.ii', isSystem: false },
]

// ---------- ZIP без сжатия (как выгрузка «По Делу»: фото не сжимаются)
function crc32(buffer) {
  let crc = 0xFFFFFFFF
  for (const byte of buffer) {
    let c = (crc ^ byte) & 0xFF
    for (let k = 0; k < 8; k += 1) c = c & 1 ? (c >>> 1) ^ 0xEDB88320 : c >>> 1
    crc = (crc >>> 8) ^ c
  }
  return (crc ^ 0xFFFFFFFF) >>> 0
}
function zip(files) {
  const parts = []
  const central = []
  let offset = 0
  for (const [name, data] of files) {
    const path = Buffer.from(name)
    const crc = crc32(data)
    const local = Buffer.alloc(30)
    local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(20, 4); local.writeUInt16LE(0x0800, 6); local.writeUInt16LE(0x21, 12)
    local.writeUInt32LE(crc, 14); local.writeUInt32LE(data.length, 18); local.writeUInt32LE(data.length, 22); local.writeUInt16LE(path.length, 26)
    parts.push(local, path, data)
    const entry = Buffer.alloc(46)
    entry.writeUInt32LE(0x02014b50, 0); entry.writeUInt16LE(20, 4); entry.writeUInt16LE(20, 6); entry.writeUInt16LE(0x0800, 8); entry.writeUInt16LE(0x21, 14)
    entry.writeUInt32LE(crc, 16); entry.writeUInt32LE(data.length, 20); entry.writeUInt32LE(data.length, 24); entry.writeUInt16LE(path.length, 28); entry.writeUInt32LE(offset, 42)
    central.push(entry, path)
    offset += 30 + path.length + data.length
  }
  const directory = Buffer.concat(central)
  const end = Buffer.alloc(22)
  end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(files.length, 8); end.writeUInt16LE(files.length, 10); end.writeUInt32LE(directory.length, 12); end.writeUInt32LE(offset, 16)
  return Buffer.concat([...parts, directory, end])
}

// ---------- заглушка «По Делу»
const stub = { sessions: new Map(), log: [], logins: 0, counter: 0 }
let server
// Сессии сервисной учётной записи, из которых «Пульс» не вышел
const openSessions = () => [...stub.sessions.values()].filter((s) => s.user).length
function startPodelu() {
  server = createServer((req, res) => {
    let raw = ''
    req.on('data', (chunk) => { raw += chunk })
    req.on('end', () => {
      const url = new URL(req.url, PODELU)
      const sid = /todo_planner_session=([^;]+)/.exec(req.headers.cookie || '')?.[1]
      const session = sid ? stub.sessions.get(sid) : null
      stub.log.push({ method: req.method, path: url.pathname, referer: req.headers.referer, csrf: req.headers['x-csrf-token'], session: Boolean(session) })
      const send = (status, data, headers = {}) => {
        const body = Buffer.isBuffer(data) ? data : Buffer.from(JSON.stringify(data))
        res.writeHead(status, { 'Content-Type': Buffer.isBuffer(data) ? 'application/zip' : 'application/json', ...headers })
        res.end(body)
      }
      const newSession = (data) => {
        const id = `s${(stub.counter += 1)}`
        stub.sessions.set(id, data)
        return { 'Set-Cookie': `todo_planner_session=${id}; HttpOnly; Path=/; SameSite=Lax` }
      }
      if (url.pathname === '/api/auth/csrf-token') {
        const token = `csrf-${stub.counter + 1}`
        if (session) { session.csrf = token; return send(200, { csrfToken: token }) }
        return send(200, { csrfToken: token }, newSession({ csrf: token, user: null }))
      }
      if (req.method === 'POST') {
        // Как «По Делу» в production: токен из своей сессии и Referer того же сайта
        if (!session || req.headers['x-csrf-token'] !== session.csrf) return send(400, { error: 'csrf_error', message: 'The CSRF token is missing.' })
        if (!(req.headers.referer || '').startsWith(`${PODELU}/`)) return send(400, { error: 'csrf_error', message: 'The referrer header is missing.' })
      }
      if (url.pathname === '/api/auth/login') {
        const body = JSON.parse(raw || '{}')
        if (body.login !== account.login || body.password !== PASSWORD) return send(401, { error: 'invalid_credentials', message: 'Неверный login или пароль' })
        stub.logins += 1
        stub.sessions.delete(sid) // новая сессия после входа — с тем же CSRF-токеном
        return send(200, { user: account }, newSession({ csrf: session.csrf, user: account }))
      }
      if (url.pathname === '/api/auth/logout') {
        stub.sessions.delete(sid)
        return send(200, { message: 'logged_out' })
      }
      if (!session?.user) return send(401, { error: 'auth_required', message: 'Требуется авторизация' })
      if (url.pathname === '/api/auth/me') return send(200, account)
      if (url.pathname === '/api/org/export') return send(200, structure)
      if (url.pathname === '/api/users/export') {
        if (url.searchParams.get('format') !== 'zip') return send(200, employees)
        return send(200, zip([['employees.json', Buffer.from(JSON.stringify(employees))], ['avatars/2.png', PHOTO]]))
      }
      if (url.pathname === '/api/users/admin/all') return send(200, people)
      return send(404, { error: 'not_found' })
    })
  })
  return new Promise((resolve) => server.listen(PORT, '127.0.0.1', resolve))
}

let page

before(async () => {
  await startPodelu()
  page = await newPage({ width: 1500, height: 950 })
  await login(page)
})
after(async () => {
  await api(page, 'DELETE', '/settings/podelu')
  await closeBrowser()
  server.close()
})

test('администратор подключает «По Делу» в «Интеграциях» и проверяет подключение', async () => {
  await page.goto(`${BASE}/admin/integrations`)
  await page.fill('[data-testid=podelu-url]', PODELU)
  await page.fill('[data-testid=podelu-login]', 'svc-puls')
  await page.fill('[data-testid=podelu-password]', 'не тот пароль')
  await page.click('[data-testid=podelu-test]')
  await page.locator('[data-testid=podelu-status]', { hasText: 'неверный логин или пароль' }).waitFor()
  // Ответ проверки с неверным паролем (502) браузер пишет в консоль — это ожидаемо
  assert.ok(page.errors.every((e) => /status of 502/.test(e)))
  page.errors.length = 0
  await page.fill('[data-testid=podelu-password]', PASSWORD)
  await page.click('[data-testid=podelu-test]')
  await page.locator('[data-testid=podelu-status]', { hasText: 'Соединение установлено: Сервис Пульса (svc-puls) · права на выгрузку структуры и сотрудников есть' }).waitFor()
  await page.click('[data-testid=podelu-save]')
  await page.locator('[data-testid=podelu-status]', { hasText: 'Подключение сохранено' }).waitFor()
  await shot(page, '10-podelu-settings')

  const saved = await api(page, 'GET', '/settings/podelu')
  assert.deepEqual(saved.body, { baseUrl: PODELU, login: 'svc-puls', acceptInvalidCerts: false, hasPassword: true })
  assert.ok(!JSON.stringify(saved.body).includes(PASSWORD))
  // Вход — с CSRF-токеном своей сессии и Referer «По Делу»; после проверки — выход
  const logins = stub.log.filter((r) => r.path === '/api/auth/login')
  assert.ok(logins.every((r) => r.csrf && r.referer === `${PODELU}/`))
  assert.equal(openSessions(), 0)
  assert.deepEqual((await api(page, 'GET', '/podelu')).body, { connected: true, baseUrl: PODELU })
})

test('загрузка структуры и сотрудников из «По Делу»: план, коллизия, применение', async () => {
  const users = (await api(page, 'GET', '/users')).body
  const sergeev = users.find((u) => u.login === 'sergeev.av')
  // Независимо от других наборов: должность в «Пульсе» отличается, подразделения нет
  await api(page, 'PATCH', `/users/${sergeev.id}`, { position: 'Инженер', departmentId: null })

  await page.click('[data-testid=podelu-sync-link]')
  await page.waitForURL(/\/admin\/org-transfer\?tab=podelu/)
  await page.click('[data-testid=podelu-preview]')
  const structurePlan = page.locator('[data-testid=structure-plan]')
  await structurePlan.waitFor()
  assert.match(await structurePlan.textContent(), /Подразделений в «По Делу»: 3/)
  assert.match(await structurePlan.textContent(), /Новых: 3/)
  const plan = page.locator('[data-testid=employees-plan]')
  assert.match(await plan.textContent(), /Новых: 1/)
  assert.match(await plan.textContent(), /Коллизий: 1/)
  assert.match(await plan.textContent(), /Фото: 1/)
  assert.match(await page.textContent('[data-testid=podelu-source]'), /Сервис Пульса · «По Делу» 1\.4\.0/)
  const kozlova = page.locator('[data-record="kozlova.ii@almi-partner.ru"]')
  assert.match(await kozlova.textContent(), /роль «Администратор» из файла новому сотруднику не назначается/)

  const loginsBefore = stub.logins
  const row = page.locator('[data-record="sergeev.av@almi-partner.ru"] tr[data-field=position]')
  assert.match(await row.textContent(), /Инженер.*Архитектор/)
  await row.locator('[data-choice=incoming]').click()
  await page.waitForFunction(() => document.querySelector('[data-record="sergeev.av@almi-partner.ru"] tr[data-field=position] td.chosen:nth-child(3)'))
  await page.waitForFunction(() => !document.querySelector('[data-testid=podelu-apply]')?.disabled)
  assert.equal(stub.logins, loginsBefore) // решение пересчитывает план без входа в «По Делу»
  await shot(page, '10-podelu-plan')
  await page.click('[data-testid=podelu-apply]')
  await page.locator('[data-testid=podelu-applied]').waitFor()
  assert.match(await page.textContent('[data-testid=podelu-applied]'), /подразделений новых 3, изменено 0;\s+сотрудников новых 1, обновлено 1, фото 1/)
  assert.equal(stub.logins, loginsBefore + 1) // применение получает данные заново
  assert.equal(openSessions(), 0) // после каждой выгрузки — выход из «По Делу»

  // Подразделения сохранили UUID «По Делу», сотрудники связаны со своими карточками там
  const tree = (await api(page, 'GET', '/org/tree')).body
  const sales = tree.departments.find((d) => d.name === 'Продажи')
  assert.equal(sales.id, IDS.sales)
  assert.equal(sales.podeluId, IDS.sales)
  const after = (await api(page, 'GET', '/users')).body
  const updated = after.find((u) => u.login === 'sergeev.av')
  assert.equal(updated.position, 'Архитектор')
  assert.equal(updated.departmentId, IDS.dev)
  assert.equal(updated.podeluId, IDS.sergeev)
  const created = after.find((u) => u.login === 'kozlova.ii')
  assert.equal(created.role, 'viewer') // права администратора из «По Делу» не выдаются
  assert.equal(created.isActive, false)
  assert.equal(created.departmentId, IDS.sales)
  assert.equal(created.podeluId, IDS.kozlova)
  assert.ok(created.avatarUrl)
  assert.deepEqual(page.errors, [])
})
