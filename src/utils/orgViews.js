/**
 * Виды «Структуры компании» (ADR-0008) — как в todo-planner «По Делу» (src/domain/org/orgChart.js, ADR-0046):
 * оргсхема подразделений, схема «Люди» (кто кому подчиняется), дерево и таблица с выгрузкой CSV.
 * Чистые функции над ответом GET /api/org/tree, без Vue: деревья, путь к узлу, раскрытие уровней,
 * строки таблицы, сортировка, поиск. Всё линейное или n·log n — 300 подразделений и 2000 сотрудников мгновенно.
 */
import { csvText } from './csv'

export const ORG_VIEWS = Object.freeze([
  { value: 'chart', label: 'Оргсхема', icon: 'hierarchy', hint: 'Подразделения карточками с линиями подчинения' },
  { value: 'people', label: 'Люди', icon: 'users', hint: 'Кто кому подчиняется: руководители и их команды' },
  { value: 'tree', label: 'Дерево', icon: 'list', hint: 'Компактное дерево подразделений с карточкой справа' },
  { value: 'table', label: 'Таблица', icon: 'table', hint: 'Все подразделения с численностью, сортировка и выгрузка в CSV' },
])
export const DEFAULT_ORG_VIEW = 'chart'
export const DEFAULT_LEVELS = 3
export const ZOOM_RANGE = Object.freeze([0.3, 1.6])
/** Узел схемы «Люди», собирающий тех, у кого нет ни руководителя, ни подчинённых. */
export const LOOSE_ID = 'loose'

/** Код вида из адреса или настроек; неизвестный — null. */
export function normalizeView(value) {
  return ORG_VIEWS.some((v) => v.value === value) ? value : null
}

export function clampZoom(value) {
  const zoom = Number(value)
  if (!Number.isFinite(zoom)) return 1
  return Math.round(Math.min(ZOOM_RANGE[1], Math.max(ZOOM_RANGE[0], zoom)) * 100) / 100
}

/** Личные настройки страницы (localStorage): вид, сколько уровней раскрыть, масштаб, показывать ли тип. */
export function readOrgPrefs(raw) {
  let data = {}
  try { data = JSON.parse(raw || '{}') || {} } catch { data = {} }
  if (typeof data !== 'object' || Array.isArray(data)) data = {}
  const levels = data.levels === 'all' ? 'all' : Number.isInteger(data.levels) && data.levels >= 1 && data.levels <= 30 ? data.levels : DEFAULT_LEVELS
  return {
    view: normalizeView(data.view),
    levels,
    zoom: typeof data.zoom === 'number' ? clampZoom(data.zoom) : 1,
    showKind: data.showKind !== false,
  }
}

/** Текст для поиска: без регистра, «ё» = «е». */
export function normText(value) {
  return String(value ?? '').toLocaleLowerCase('ru').replace(/ё/g, 'е')
}

const byName = (a, b) => String(a.name).localeCompare(String(b.name), 'ru')

/**
 * Лес подразделений: [{ id, item, depth, children }]. Соседи — по порядку (sortOrder), при равном —
 * в порядке ответа сервера (он уже по названию). Потерявшие родителя — корни; цикл в данных не теряет узлы.
 */
export function unitForest(departments = []) {
  const ids = new Set(departments.map((d) => d.id))
  const children = new Map()
  for (const dep of departments) {
    const parent = dep.parentId && ids.has(dep.parentId) && dep.parentId !== dep.id ? dep.parentId : null
    if (!children.has(parent)) children.set(parent, [])
    children.get(parent).push(dep)
  }
  const order = (a, b) => (a.sortOrder || 0) - (b.sortOrder || 0)
  const seen = new Set()
  const build = (dep, depth) => {
    seen.add(dep.id)
    const kids = (children.get(dep.id) || []).filter((kid) => !seen.has(kid.id)).sort(order)
    return { id: dep.id, item: dep, depth, children: kids.map((kid) => build(kid, depth + 1)) }
  }
  const roots = (children.get(null) || []).sort(order).map((dep) => build(dep, 0))
  for (const dep of departments) if (!seen.has(dep.id)) roots.push(build(dep, 0))
  return roots
}

/**
 * Лес «кто кому подчиняется» по вычисленному руководителю (directManagerId, ADR-0008). Только активные.
 * У узла: reports — прямых подчинённых, branch — всего в ветке. Сначала руководители с большей командой.
 * Те, у кого нет ни руководителя, ни подчинённых (и кто не «первое лицо»), собраны в один узел LOOSE_ID —
 * иначе каждый стоял бы отдельным корнем и схема уходила бы вширь.
 */
export function peopleForest(people = [], { groupLoose = true } = {}) {
  const active = people.filter((p) => p.isActive !== false)
  const ids = new Set(active.map((p) => p.id))
  const children = new Map()
  for (const person of active) {
    const manager = person.directManagerId
    const parent = manager != null && manager !== person.id && ids.has(manager) ? manager : null
    if (!children.has(parent)) children.set(parent, [])
    children.get(parent).push(person)
  }
  const order = (a, b) => b.branch - a.branch || byName(a.item, b.item)
  const seen = new Set()
  const build = (person, depth) => {
    seen.add(person.id)
    const kids = (children.get(person.id) || []).filter((kid) => !seen.has(kid.id)).map((kid) => build(kid, depth + 1))
    kids.sort(order)
    return { id: person.id, item: person, depth, children: kids, reports: kids.length, branch: kids.reduce((sum, kid) => sum + 1 + kid.branch, 0) }
  }
  let roots = (children.get(null) || []).map((person) => build(person, 0))
  // Цикл подчинения (ошибка данных) не должен прятать людей: оставшихся — в корни
  for (const person of active) if (!seen.has(person.id)) roots.push(build(person, 0))
  roots.sort(order)
  if (!groupLoose) return roots
  const loose = roots.filter((node) => !node.children.length && !node.item.reportsToNone)
  if (!loose.length) return roots
  roots = roots.filter((node) => node.children.length || node.item.reportsToNone)
  const members = loose.map((node) => ({ ...node, depth: 1 })).sort((a, b) => byName(a.item, b.item))
  roots.push({ id: LOOSE_ID, group: true, depth: 0, item: { id: LOOSE_ID, name: 'Без руководителя' }, children: members, reports: members.length, branch: members.length })
  return roots
}

/** Индекс узлов леса: id → { node, parentId }. */
export function indexForest(forest) {
  const index = new Map()
  const stack = forest.map((node) => [node, null])
  while (stack.length) {
    const [node, parentId] = stack.pop()
    index.set(node.id, { node, parentId })
    for (const child of node.children) stack.push([child, node.id])
  }
  return index
}

/** Предки узла от корня (без самого узла). */
export function ancestorsOf(index, id) {
  const path = []
  const seen = new Set()
  let current = index.get(id)?.parentId ?? null
  while (current !== null && current !== undefined && !seen.has(current)) {
    seen.add(current)
    path.unshift(current)
    current = index.get(current)?.parentId ?? null
  }
  return path
}

/** Путь к узлу для подсветки: предки и сам узел; узла нет — пусто. */
export function pathTo(index, id) {
  return id !== null && id !== undefined && index.has(id) ? new Set([...ancestorsOf(index, id), id]) : new Set()
}

/** Раскрытые ветки плюс все предки узла — чтобы он стал виден. */
export function revealIn(expanded, index, id) {
  const next = new Set(expanded)
  for (const ancestor of ancestorsOf(index, id)) next.add(ancestor)
  return next
}

/** Узлы, раскрытые до заданной глубины (levels — сколько уровней видно; null — все). Группа LOOSE_ID — свёрнута. */
export function expandedToLevel(forest, levels) {
  const result = new Set()
  const stack = [...forest]
  while (stack.length) {
    const node = stack.pop()
    if (node.group) continue
    if (node.children.length && (levels === null || node.depth + 1 < levels)) result.add(node.id)
    stack.push(...node.children)
  }
  return result
}

/** Глубина леса (1 — только корни). */
export function forestDepth(forest) {
  let depth = 0
  const stack = [...forest]
  while (stack.length) {
    const node = stack.pop()
    if (node.group) continue
    depth = Math.max(depth, node.depth + 1)
    stack.push(...node.children)
  }
  return depth
}

// ---------------------------------------------------------------- таблица

/** Колонки таблицы на экране; уровень виден по отступу и пути, в CSV он отдельной колонкой. */
export const TABLE_COLUMNS = Object.freeze([
  { key: 'path', label: 'Подразделение', hint: 'В порядке структуры; ниже — вышестоящие' },
  { key: 'kindLabel', label: 'Тип' },
  { key: 'headName', label: 'Руководитель' },
  { key: 'employeeCount', label: 'Сотрудников', num: true, hint: 'В самом подразделении' },
  { key: 'branchEmployeeCount', label: 'В ветке', num: true, hint: 'Сотрудников вместе с вложенными подразделениями' },
  { key: 'childCount', label: 'Вложенных', num: true, hint: 'Подразделений уровнем ниже' },
])

const CSV_COLUMNS = Object.freeze([
  { key: 'path', label: 'Путь' },
  { key: 'name', label: 'Подразделение' },
  { key: 'kindLabel', label: 'Тип' },
  { key: 'level', label: 'Уровень' },
  { key: 'parentPath', label: 'Вышестоящее' },
  { key: 'headName', label: 'Руководитель' },
  { key: 'headPosition', label: 'Должность руководителя' },
  { key: 'headNames', label: 'Все руководители' },
  { key: 'employeeCount', label: 'Сотрудников' },
  { key: 'branchEmployeeCount', label: 'Всего в ветке' },
  { key: 'childCount', label: 'Вложенных подразделений' },
])

/**
 * Строки таблицы подразделений: название и путь, тип, уровень, руководители, численность.
 * kindLabel — функция «код типа → название». Порядок строк — как в дереве (treeOrder).
 */
export function unitTableRows(departments = [], people = [], kindLabel = (kind) => kind) {
  const persons = new Map(people.map((p) => [p.id, p]))
  const rows = []
  const walk = (nodes, parentPath) => {
    for (const node of nodes) {
      const dep = node.item
      const path = [...parentPath, dep.name]
      const heads = [dep.primaryHeadId, ...(dep.headIds || [])].filter((id, i, all) => id != null && all.indexOf(id) === i)
        .map((id) => persons.get(id)).filter((p) => p && p.isActive !== false)
      const head = heads[0] || null
      rows.push({
        id: dep.id,
        name: dep.name,
        path: path.join(' / '),
        parentPath: parentPath.join(' / '),
        level: node.depth + 1,
        kind: dep.kind,
        kindLabel: kindLabel(dep.kind) || dep.kind || '',
        headId: head?.id ?? null,
        headName: head?.name || '',
        headPosition: head?.position || '',
        headNames: heads.map((p) => p.name).join(', '),
        headCount: heads.length,
        employeeCount: dep.employeeCount || 0,
        branchEmployeeCount: dep.branchEmployeeCount || 0,
        childCount: node.children.length,
        treeOrder: rows.length,
      })
      walk(node.children, path)
    }
  }
  walk(unitForest(departments), [])
  return rows
}

/** Строки, где запрос встречается в названии, пути, типе или имени руководителя. */
export function filterRows(rows, query) {
  const needle = normText(query).trim()
  if (!needle) return rows
  return rows.filter((row) => [row.path, row.kindLabel, row.headNames].some((value) => normText(value).includes(needle)))
}

/** Сортировка по колонке: путь — порядок дерева, числа — по величине, текст — по алфавиту; при равенстве — порядок дерева. */
export function sortRows(rows, key, dir = 'asc') {
  const sign = dir === 'desc' ? -1 : 1
  if (key === 'path') return [...rows].sort((a, b) => (a.treeOrder - b.treeOrder) * sign)
  return [...rows].sort((a, b) => {
    const x = a[key]
    const y = b[key]
    const diff = typeof x === 'number' && typeof y === 'number' ? x - y : String(x ?? '').localeCompare(String(y ?? ''), 'ru')
    // Пустые значения (нет руководителя) — в конце при любом направлении
    if (x === '' && y !== '') return 1
    if (y === '' && x !== '') return -1
    return diff * sign || a.treeOrder - b.treeOrder
  })
}

/** CSV таблицы подразделений для Excel (UTF-8 с BOM, «;», защита от формул). */
export function tableCsv(rows) {
  return csvText(CSV_COLUMNS.map((c) => c.label), rows.map((row) => CSV_COLUMNS.map((c) => row[c.key])))
}

// ---------------------------------------------------------------- поиск

/**
 * Поиск по подразделениям и людям (имя или должность): до limit совпадений; сначала те, где запрос — начало
 * слова («ольга» находит «Никитина Ольга»), затем совпадения внутри слова.
 */
export function searchOrg(query, departments = [], people = [], limit = 12) {
  const needle = normText(query).trim()
  if (!needle) return []
  const score = (text) => {
    const value = normText(text)
    const at = value.indexOf(needle)
    if (at < 0) return 0
    return at === 0 || value.includes(` ${needle}`) || value.includes(`«${needle}`) ? 2 : 1
  }
  const results = []
  for (const dep of departments) {
    const s = score(dep.name)
    if (s) results.push({ type: 'unit', id: dep.id, title: dep.name, subtitle: '', score: s })
  }
  for (const person of people) {
    if (person.isActive === false) continue
    const s = Math.max(score(person.name), score(person.position) ? 1 : 0)
    if (s) results.push({ type: 'person', id: person.id, title: person.name, subtitle: person.position || '', score: s })
  }
  return results.sort((a, b) => b.score - a.score || a.title.localeCompare(b.title, 'ru')).slice(0, limit)
}
