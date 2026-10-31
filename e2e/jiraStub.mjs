/**
 * Заглушка Jira для e2e, снимков экранов и разработки — ведёт себя как настоящая Jira там, где «Пульс» на это
 * опирается:
 *
 * - Server/DC (flavor 'server'): REST API v2, POST и GET /search (validateQuery — только логическое значение,
 *   строку Server отклоняет с 400), Epic Link — customfield_10008, спринт — customfield_10010 строкой
 *   «com.atlassian.greenhopper.service.sprint.Sprint@…[id=…,name=…]», как отдаёт Jira Server;
 * - Cloud (flavor 'cloud'): REST API v3, старый /search удалён (410 Gone), поиск — /search/jql с nextPageToken,
 *   эпик — родитель задачи, спринт — массив объектов;
 * - вход: Basic (логин и пароль) или Bearer (PAT); без входа или с неверными данными — 401;
 * - /myself, /serverInfo (с baseUrl, который Jira считает своим), /mypermissions, /project, /project/search,
 *   /project/KEY/versions, /project/KEY/statuses, /field, /issue/KEY, Agile: /board, /board/ID/sprint;
 * - JQL — подмножество, которое строит «Пульс»: key in (…), project = / in, issuetype in, statusCategory = / != /
 *   in, status in, text ~ / summary ~, fixVersion = , sprint = / in openSprints(), "Epic Link" = , parent = ,
 *   assignee is EMPTY / = currentUser(), resolution = Unresolved, updated >= -Nd, ORDER BY (без учёта), AND.
 *   Непонятное условие — 400 с текстом, как у Jira.
 *
 *   const jira = await startJiraStub({ port: 5099, flavor: 'server' })
 *   jira.url, jira.requests (журнал запросов), jira.issues (задачи можно менять в тесте), await jira.close()
 */
import { createServer } from 'node:http'

const status = (name, key) => ({ name, statusCategory: { key } })
const TYPES = { task: { name: 'Задача', hierarchyLevel: 0 }, story: { name: 'История', hierarchyLevel: 0 }, bug: { name: 'Ошибка', hierarchyLevel: 0 }, epic: { name: 'Epic', hierarchyLevel: 1 } }

/** Демонстрационные данные: проект PULS (эпик, версии, спринты, связи «блокирует») и проект OPS. */
export function demoJiraData() {
  const projects = [
    { id: '10000', key: 'PULS', name: 'Портал поставщиков' },
    { id: '10001', key: 'OPS', name: 'Эксплуатация' },
    // Проект без права просмотра у сервисной учётной записи — его задач нет в поиске
    { id: '10002', key: 'SEC', name: 'Безопасность', hidden: true },
  ]
  const versions = {
    PULS: [
      { id: '20001', name: 'Релиз 1.0', released: true, releaseDate: '2026-06-30', startDate: '2026-05-01' },
      { id: '20002', name: 'Релиз 2.0', released: false, releaseDate: '2026-12-15', startDate: '2026-09-01', description: 'Личный кабинет и ЭДО' },
      { id: '20003', name: 'Релиз 3.0', released: false },
    ],
    OPS: [],
  }
  const sprints = {
    1: [
      { id: 5, name: 'Спринт 5', state: 'closed', startDate: '2026-10-05T09:00:00.000+03:00', endDate: '2026-10-19T18:00:00.000+03:00' },
      { id: 6, name: 'Спринт 6', state: 'active', startDate: '2026-10-19T09:00:00.000+03:00', endDate: '2026-11-02T18:00:00.000+03:00' },
      { id: 7, name: 'Спринт 7', state: 'future', startDate: '2026-11-02T09:00:00.000+03:00', endDate: '2026-11-16T18:00:00.000+03:00' },
    ],
  }
  const boards = [{ id: 1, name: 'Доска PULS', type: 'scrum', location: { projectKey: 'PULS' } }]
  const issues = {
    'PULS-1': { summary: 'Согласование ТЗ', issuetype: TYPES.task, status: status('В работе', 'indeterminate'), assignee: { displayName: 'Иванов И.' }, duedate: '2026-06-12', timespent: 7200, timeestimate: 7200, fixVersions: ['20001'], sprint: 5, updated: '2026-10-30T10:00:00.000+0300' },
    'PULS-2': { summary: 'Прототип интерфейса', issuetype: TYPES.story, status: status('Открыта', 'new'), duedate: '2026-06-20', fixVersions: ['20002'], sprint: 6, links: { blockedBy: ['PULS-1'] } },
    'PULS-3': { summary: 'Приёмка заказчиком', issuetype: TYPES.task, status: status('Готово', 'done'), duedate: '2026-06-05', resolutiondate: '2026-06-04T11:00:00.000+0300', fixVersions: ['20001'] },
    'PULS-10': { summary: 'Личный кабинет', issuetype: TYPES.epic, status: status('В работе', 'indeterminate') },
    'PULS-11': { summary: 'Вход по СМС', issuetype: TYPES.story, status: status('Открыта', 'new'), duedate: '2026-07-10', epic: 'PULS-10', fixVersions: ['20002'], sprint: 6, priority: { name: 'Высокий' } },
    'PULS-12': { summary: 'Профиль поставщика', issuetype: TYPES.story, status: status('Открыта', 'new'), duedate: '2026-07-17', epic: 'PULS-10', fixVersions: ['20002'], sprint: 7, links: { blockedBy: ['PULS-11'] } },
    'PULS-13': { summary: 'Ошибка входа через Safari', issuetype: TYPES.bug, status: status('В работе', 'indeterminate'), assignee: { displayName: 'Петрова А.' }, duedate: '2026-11-05', epic: 'PULS-10', sprint: 6 },
    'OPS-1': { summary: 'Резервное копирование базы', issuetype: TYPES.task, status: status('Открыта', 'new'), duedate: '2026-11-20' },
    'SEC-1': { summary: 'Аудит доступа', issuetype: TYPES.task, status: status('Открыта', 'new'), duedate: '2026-11-25' },
  }
  return { projects, versions, sprints, boards, issues }
}

function sprintString(s) {
  return `com.atlassian.greenhopper.service.sprint.Sprint@6f00${s.id}[id=${s.id},rapidViewId=1,state=${s.state.toUpperCase()},name=${s.name},startDate=${s.startDate || '<null>'},endDate=${s.endDate || '<null>'},completeDate=<null>,sequence=${s.id},goal=]`
}

export async function startJiraStub({
  port = 5099, flavor = 'server', login = 'svc-puls', password = 'пароль-сервиса', token = 'pat-token',
  data = demoJiraData(), reportedBaseUrl = null, anonymousSearch = false,
} = {}) {
  const cloud = flavor === 'cloud'
  const api = cloud ? '/rest/api/3/' : '/rest/api/2/'
  const url = `http://127.0.0.1:${port}`
  const requests = []
  const allSprints = () => Object.values(data.sprints).flat()

  function visibleProject(key) {
    const project = data.projects.find((p) => p.key === key)
    return project && !project.hidden ? project : null
  }
  function issueFields(key) {
    const raw = data.issues[key]
    const project = key.split('-')[0]
    const fields = {
      summary: raw.summary, status: raw.status, issuetype: raw.issuetype || TYPES.task, assignee: raw.assignee || null,
      priority: raw.priority || { name: 'Средний' }, duedate: raw.duedate || null, resolutiondate: raw.resolutiondate || null,
      created: '2026-05-01T10:00:00.000+0300', updated: raw.updated || '2026-10-29T10:00:00.000+0300',
      timeoriginalestimate: raw.timeoriginalestimate ?? null, timespent: raw.timespent ?? null, timeestimate: raw.timeestimate ?? null,
      progress: { progress: raw.timespent || 0, total: (raw.timespent || 0) + (raw.timeestimate || 0) },
      project: { key: project, name: data.projects.find((p) => p.key === project)?.name },
      fixVersions: (raw.fixVersions || []).map((id) => {
        const v = (data.versions[project] || []).find((x) => x.id === id)
        return v ? { id: v.id, name: v.name, released: v.released, releaseDate: v.releaseDate } : null
      }).filter(Boolean),
      issuelinks: [
        ...(raw.links?.blockedBy || []).map((k) => ({ type: { name: 'Blocks', inward: 'is blocked by', outward: 'blocks' }, inwardIssue: { key: k } })),
        ...Object.entries(data.issues).filter(([, other]) => other.links?.blockedBy?.includes(key))
          .map(([k]) => ({ type: { name: 'Blocks', inward: 'is blocked by', outward: 'blocks' }, outwardIssue: { key: k } })),
      ],
      customfield_10015: raw.start || null,
    }
    const sprint = raw.sprint ? allSprints().find((s) => s.id === raw.sprint) : null
    if (cloud) {
      fields.customfield_10020 = sprint ? [{ id: sprint.id, name: sprint.name, state: sprint.state, startDate: sprint.startDate, endDate: sprint.endDate, boardId: 1 }] : null
      if (raw.epic) fields.parent = { key: raw.epic, fields: { summary: data.issues[raw.epic]?.summary, issuetype: TYPES.epic } }
    } else {
      fields.customfield_10010 = sprint ? [sprintString(sprint)] : null
      fields.customfield_10008 = raw.epic || null
    }
    return fields
  }

  /** Подмножество JQL → функция-фильтр; ошибка — { error }. */
  function compile(jql) {
    const [where] = jql.split(/\s+ORDER\s+BY\s+/i)
    const clauses = where.trim() ? where.split(/\s+AND\s+/i) : []
    const tests = []
    const list = (text) => text.replace(/^\(|\)$/g, '').split(',').map((s) => s.trim().replace(/^"|"$/g, '').replace(/\\"/g, '"')).filter(Boolean)
    for (const raw of clauses) {
      const c = raw.trim().replace(/^\((.*)\)$/, '$1')
      let m
      if ((m = /^(?:key|issuekey)\s+in\s+(\(.*\))$/i.exec(c))) { const keys = list(m[1]).map((k) => k.toUpperCase()); tests.push((k) => keys.includes(k)); continue }
      if ((m = /^project\s*=\s*"?([A-Z][A-Z0-9_]*)"?$/i.exec(c))) { const p = m[1].toUpperCase(); tests.push((k) => k.startsWith(`${p}-`)); continue }
      if ((m = /^project\s+in\s+(\(.*\))$/i.exec(c))) { const ps = list(m[1]).map((p) => p.toUpperCase()); tests.push((k) => ps.includes(k.split('-')[0])); continue }
      if ((m = /^issuetype\s+in\s+(\(.*\))$/i.exec(c))) { const ts = list(m[1]).map((t) => t.toLowerCase()); tests.push((k, f) => ts.includes(f.issuetype.name.toLowerCase())); continue }
      if ((m = /^issuetype\s*(!?=)\s*"?([^"]+)"?$/i.exec(c))) { const t = m[2].toLowerCase(); const neg = m[1] === '!='; tests.push((k, f) => (f.issuetype.name.toLowerCase() === t) !== neg); continue }
      if ((m = /^statusCategory\s*(!?=)\s*"?([^"]+?)"?$/i.exec(c))) {
        const names = { 'to do': 'new', new: 'new', 'in progress': 'indeterminate', indeterminate: 'indeterminate', done: 'done', 2: 'new', 4: 'indeterminate', 3: 'done' }
        const cat = names[m[2].toLowerCase()]
        if (!cat) return { error: `The value '${m[2]}' does not exist for the field 'statusCategory'.` }
        const neg = m[1] === '!='
        tests.push((k, f) => (f.status.statusCategory.key === cat) !== neg); continue
      }
      if ((m = /^statusCategory\s+in\s+(\(.*\))$/i.exec(c))) {
        const names = { 'to do': 'new', 'in progress': 'indeterminate', done: 'done', 2: 'new', 4: 'indeterminate', 3: 'done' }
        const cats = list(m[1]).map((n) => names[n.toLowerCase()])
        tests.push((k, f) => cats.includes(f.status.statusCategory.key)); continue
      }
      if ((m = /^status\s+in\s+(\(.*\))$/i.exec(c))) { const ss = list(m[1]).map((s) => s.toLowerCase()); tests.push((k, f) => ss.includes(f.status.name.toLowerCase())); continue }
      if ((m = /^(text|summary)\s*~\s*"(.*)"$/i.exec(c))) {
        const words = m[2].replace(/\\"/g, '"').replace(/\*/g, '').toLowerCase().split(/\s+/).filter(Boolean)
        tests.push((k, f) => words.every((w) => f.summary.toLowerCase().includes(w) || k.toLowerCase() === w)); continue
      }
      if ((m = /^fixVersion\s*=\s*"?([^"]+)"?$/i.exec(c))) { const v = m[1]; tests.push((k, f) => f.fixVersions.some((x) => x.id === v || x.name === v)); continue }
      if (/^sprint\s+in\s+openSprints\(\)$/i.test(c)) { tests.push((k) => ['active', 'future'].includes(allSprints().find((s) => s.id === data.issues[k].sprint)?.state)); continue }
      if ((m = /^sprint\s*=\s*"?(\d+)"?$/i.exec(c))) { const id = Number(m[1]); tests.push((k) => data.issues[k].sprint === id); continue }
      if ((m = /^(?:"Epic Link"|cf\[10008\]|parent|parentEpic)\s*=\s*"?([A-Z][A-Z0-9_]*-\d+)"?$/i.exec(c))) {
        if (!cloud && /^parent\b/i.test(c)) return { error: "Field 'parent' does not exist or you do not have permission to view it." }
        if (cloud && /^"Epic Link"/i.test(c)) return { error: "Field 'Epic Link' does not exist or you do not have permission to view it." }
        const e = m[1].toUpperCase(); tests.push((k) => data.issues[k].epic === e || k === e && /^parentEpic/i.test(c)); continue
      }
      if (/^assignee\s+is\s+EMPTY$/i.test(c)) { tests.push((k, f) => !f.assignee); continue }
      if (/^assignee\s*=\s*currentUser\(\)$/i.test(c)) { tests.push(() => false); continue }
      if (/^resolution\s*=\s*Unresolved$/i.test(c)) { tests.push((k, f) => f.status.statusCategory.key !== 'done'); continue }
      if ((m = /^updated\s*>=\s*-(\d+)d$/i.exec(c))) { tests.push(() => true); continue }
      return { error: `Error in the JQL Query: Expecting operator but got '${c.slice(0, 40)}'.` }
    }
    return { test: (key) => { const f = issueFields(key); return tests.every((t) => t(key, f)) } }
  }

  function pick(fields, wanted) {
    if (!wanted || !wanted.length || wanted.includes('*all')) return fields
    return Object.fromEntries(wanted.filter((n) => n in fields).map((n) => [n, fields[n]]))
  }
  function authorized(req) {
    const header = req.headers.authorization || ''
    if (header.startsWith('Bearer ')) return !cloud && header.slice(7) === token
    if (header.startsWith('Basic ')) {
      const [user, ...rest] = Buffer.from(header.slice(6), 'base64').toString('utf8').split(':')
      return user === login && rest.join(':') === password
    }
    return false
  }

  const server = createServer((req, res) => {
    let body = ''
    req.on('data', (chunk) => { body += chunk })
    req.on('end', () => {
      const parsed = new URL(req.url, url)
      const path = parsed.pathname
      requests.push({ method: req.method, path, query: Object.fromEntries(parsed.searchParams), body: body ? safeJson(body) : null })
      const send = (code, payload, headers = {}) => { res.writeHead(code, { 'Content-Type': 'application/json;charset=UTF-8', ...headers }); res.end(JSON.stringify(payload)) }
      const reportedBase = reportedBaseUrl || url
      if (path === `${api}serverInfo`) return send(200, { baseUrl: reportedBase, version: cloud ? '1001.0.0-SNAPSHOT' : '9.12.2', deploymentType: cloud ? 'Cloud' : 'DataCenter', serverTitle: 'Jira' })
      const ok = authorized(req)
      if (!ok && !(anonymousSearch && path.includes('search'))) {
        return send(401, { errorMessages: ['You are not authenticated. Authentication required to perform this operation.'] }, { 'X-Seraph-LoginReason': 'AUTHENTICATED_FAILED' })
      }
      if (path === `${api}myself`) return send(200, { name: login, key: login, displayName: 'Сервисная учётная запись', emailAddress: `${login}@company.ru`, active: true })
      if (path === `${api}mypermissions`) {
        const visible = data.projects.filter((p) => !p.hidden).length
        return send(200, { permissions: { BROWSE_PROJECTS: { key: 'BROWSE_PROJECTS', havePermission: visible > 0 } } })
      }
      if (path === `${api}project`) return send(200, data.projects.filter((p) => !p.hidden).map(({ id, key, name }) => ({ id, key, name, projectTypeKey: 'software' })))
      if (path === `${api}project/search`) {
        const values = data.projects.filter((p) => !p.hidden).map(({ id, key, name }) => ({ id, key, name }))
        return send(200, { values, total: values.length, isLast: true, startAt: 0, maxResults: 50 })
      }
      let m
      if ((m = new RegExp(`^${api}project/([A-Z0-9_]+)/versions$`).exec(path))) {
        if (!visibleProject(m[1])) return send(404, { errorMessages: [`No project could be found with key '${m[1]}'.`] })
        return send(200, (data.versions[m[1]] || []).map((v) => ({ ...v, self: `${url}${api}version/${v.id}`, archived: false, projectId: 1 })))
      }
      if ((m = new RegExp(`^${api}project/([A-Z0-9_]+)/statuses$`).exec(path))) {
        if (!visibleProject(m[1])) return send(404, { errorMessages: [`No project could be found with key '${m[1]}'.`] })
        const statuses = [status('Открыта', 'new'), status('В работе', 'indeterminate'), status('Готово', 'done')]
        return send(200, Object.values(TYPES).map((t) => ({ name: t.name, subtask: false, statuses })))
      }
      if (path === `${api}field`) {
        return send(200, [
          { id: 'duedate', name: 'Срок', schema: { type: 'date', system: 'duedate' } },
          { id: 'resolutiondate', name: 'Дата решения', schema: { type: 'datetime' } },
          { id: 'customfield_10015', name: 'Start date', custom: true, schema: { type: 'date', custom: 'com.atlassian.jira.plugin.system.customfieldtypes:datepicker' } },
          ...(cloud
            ? [{ id: 'customfield_10020', name: 'Sprint', custom: true, schema: { type: 'array', items: 'json', custom: 'com.pyxis.greenhopper.jira:gh-sprint' } }]
            : [
                { id: 'customfield_10008', name: 'Epic Link', custom: true, schema: { type: 'any', custom: 'com.pyxis.greenhopper.jira:gh-epic-link' } },
                { id: 'customfield_10010', name: 'Sprint', custom: true, schema: { type: 'array', items: 'string', custom: 'com.pyxis.greenhopper.jira:gh-sprint' } },
              ]),
        ])
      }
      if (cloud && path === '/rest/api/3/search') {
        return send(410, { errorMessages: ['The requested API has been removed. Please migrate to the /rest/api/3/search/jql API.'] })
      }
      const isSearch = cloud ? path === '/rest/api/3/search/jql' : path === '/rest/api/2/search'
      if (isSearch) {
        const params = req.method === 'POST' ? safeJson(body) || {} : {
          jql: parsed.searchParams.get('jql') || '', startAt: Number(parsed.searchParams.get('startAt') || 0),
          maxResults: Number(parsed.searchParams.get('maxResults') || 50), nextPageToken: parsed.searchParams.get('nextPageToken'),
          fields: (parsed.searchParams.get('fields') || '').split(',').filter(Boolean),
          validateQuery: parsed.searchParams.has('validateQuery') ? parsed.searchParams.get('validateQuery') : undefined,
        }
        if (!cloud && req.method === 'POST' && params.validateQuery !== undefined && typeof params.validateQuery !== 'boolean') {
          return send(400, { errorMessages: [`Can not construct instance of java.lang.Boolean from String value '${params.validateQuery}'`] })
        }
        if (cloud && !(params.jql || '').trim()) return send(400, { errorMessages: ['Unbounded JQL queries are not allowed here. Please add a search restriction to your query.'] })
        const compiled = compile(params.jql || '')
        if (compiled.error) return send(400, { errorMessages: [compiled.error], warningMessages: [] })
        const keysInQuery = /key\s+in\s*\(([^)]*)\)/i.exec(params.jql || '')
        const strict = cloud ? true : !(params.validateQuery === false || params.validateQuery === 'false')
        if (keysInQuery && strict) {
          const missing = keysInQuery[1].split(',').map((k) => k.trim().replace(/"/g, '').toUpperCase()).find((k) => !data.issues[k] || !visibleProject(k.split('-')[0]))
          if (missing) return send(400, { errorMessages: [`An issue with key '${missing}' does not exist for field 'key'.`] })
        }
        const all = Object.keys(data.issues).filter((k) => visibleProject(k.split('-')[0]) && compiled.test(k))
        const max = Math.min(Number(params.maxResults) || 50, cloud ? 5000 : 1000)
        const start = cloud ? Number(params.nextPageToken || 0) : Number(params.startAt || 0)
        const page = all.slice(start, start + max)
        const issues = page.map((key) => ({ id: String(10000 + Object.keys(data.issues).indexOf(key)), key, self: `${url}${api}issue/${key}`, fields: pick(issueFields(key), params.fields) }))
        if (cloud) return send(200, { issues, ...(start + max < all.length ? { nextPageToken: String(start + max) } : {}), isLast: start + max >= all.length })
        return send(200, { startAt: start, maxResults: max, total: all.length, issues })
      }
      if ((m = new RegExp(`^${api}issue/([A-Za-z0-9_]+-\\d+)$`).exec(path))) {
        const key = m[1].toUpperCase()
        if (!data.issues[key] || !visibleProject(key.split('-')[0])) return send(404, { errorMessages: ['Issue does not exist or you do not have permission to see it.'] })
        const wanted = (parsed.searchParams.get('fields') || '').split(',').filter(Boolean)
        return send(200, { key, fields: pick(issueFields(key), wanted) })
      }
      if (path === '/rest/agile/1.0/board') {
        const projectKey = parsed.searchParams.get('projectKeyOrId')
        const values = data.boards.filter((b) => !projectKey || b.location.projectKey === projectKey)
        return send(200, { values, isLast: true, startAt: 0, maxResults: 50, total: values.length })
      }
      if ((m = /^\/rest\/agile\/1\.0\/board\/(\d+)\/sprint$/.exec(path))) {
        const states = (parsed.searchParams.get('state') || 'active,future,closed').split(',')
        const values = (data.sprints[m[1]] || []).filter((s) => states.includes(s.state)).map((s) => ({ ...s, originBoardId: Number(m[1]) }))
        return send(200, { values, isLast: true, startAt: 0, maxResults: 50 })
      }
      return send(404, { errorMessages: ['Not found'] })
    })
  })
  await new Promise((resolveListen) => server.listen(port, '127.0.0.1', resolveListen))
  return {
    url, requests, data, issues: data.issues,
    close: () => new Promise((resolveClose) => server.close(resolveClose)),
  }
}

function safeJson(text) {
  try { return JSON.parse(text) } catch { return null }
}

// Запуск отдельно: node e2e/jiraStub.mjs [server|cloud] [порт]
if (import.meta.url === `file://${process.argv[1]}`) {
  const flavor = process.argv[2] || 'server'
  const port = Number(process.argv[3] || 5099)
  const stub = await startJiraStub({ port, flavor })
  console.log(`Jira ${flavor} stub: ${stub.url} (логин svc-puls, пароль «пароль-сервиса», PAT pat-token)`)
}
