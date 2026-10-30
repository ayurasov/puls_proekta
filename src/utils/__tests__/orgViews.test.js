import { describe, expect, it } from 'vitest'
import { csvCell, csvText } from '../csv'
import {
  LOOSE_ID, ancestorsOf, clampZoom, expandedToLevel, filterRows, forestDepth, indexForest, normalizeView, pathTo,
  peopleForest, readOrgPrefs, revealIn, searchOrg, sortRows, tableCsv, unitForest, unitTableRows,
} from '../orgViews'

// Как в ответе GET /api/org/tree: подразделения сверху вниз, соседи по порядку и названию
const departments = [
  { id: 'c', name: 'ООО «Вектор»', parentId: null, kind: 'company', sortOrder: 0, headIds: [1], primaryHeadId: 1, employeeCount: 1, branchEmployeeCount: 6, childCount: 2 },
  { id: 'dev', name: 'Разработка', parentId: 'c', kind: 'department', sortOrder: 0, headIds: [2], primaryHeadId: 2, employeeCount: 1, branchEmployeeCount: 4, childCount: 2 },
  { id: 'qa', name: 'Группа тестирования', parentId: 'dev', kind: 'group', sortOrder: 1, headIds: [], primaryHeadId: null, employeeCount: 1, branchEmployeeCount: 1, childCount: 0 },
  { id: 'an', name: 'Группа аналитики', parentId: 'dev', kind: 'group', sortOrder: 0, headIds: [5, 4], primaryHeadId: null, employeeCount: 2, branchEmployeeCount: 2, childCount: 0 },
  { id: 'fin', name: 'Финансовая служба', parentId: 'c', kind: 'service', sortOrder: 1, headIds: [], primaryHeadId: null, employeeCount: 1, branchEmployeeCount: 1, childCount: 0 },
  { id: 'lost', name: 'Без родителя', parentId: 'missing', kind: 'department', sortOrder: 0, headIds: [], employeeCount: 0, branchEmployeeCount: 0 },
]
const people = [
  { id: 1, name: 'Егорова Анна', position: 'Генеральный директор', departmentId: 'c', directManagerId: null, reportsToNone: true, isActive: true },
  { id: 2, name: 'Лебедев Олег', position: 'Руководитель разработки', departmentId: 'dev', directManagerId: 1, isActive: true },
  { id: 3, name: 'Никитина Ольга', position: 'Тестировщик', departmentId: 'qa', directManagerId: 2, isActive: true },
  { id: 4, name: 'Ковалёва Мария', position: 'Аналитик', departmentId: 'an', directManagerId: 2, isActive: true },
  { id: 5, name: 'Орлов Сергей', position: 'Аналитик', departmentId: 'an', directManagerId: 4, isActive: true },
  { id: 6, name: 'Волков Пётр', position: '=Финансист', departmentId: 'fin', directManagerId: 1, isActive: true },
  { id: 7, name: 'Уволен Иван', departmentId: 'dev', directManagerId: 2, isActive: false },
  { id: 8, name: 'Яковлев Без Отдела', departmentId: null, directManagerId: null, isActive: true },
  { id: 9, name: 'Белов Без Отдела', departmentId: null, directManagerId: null, isActive: true },
  { id: 10, name: 'Цикл А', directManagerId: 11, isActive: true },
  { id: 11, name: 'Цикл Б', directManagerId: 10, isActive: true },
]

describe('оргсхема подразделений', () => {
  it('строит лес по порядку, осиротевшие — корни, считает глубину', () => {
    const forest = unitForest(departments)
    expect(forest.map((n) => n.id)).toEqual(['c', 'lost'])
    expect(forest[0].children.map((n) => n.id)).toEqual(['dev', 'fin'])
    // sortOrder важнее порядка ответа: «аналитика» (0) раньше «тестирования» (1)
    expect(forest[0].children[0].children.map((n) => [n.id, n.depth])).toEqual([['an', 2], ['qa', 2]])
    expect(forestDepth(forest)).toBe(3)
  })

  it('цикл в данных не теряет подразделения и не зацикливает обход', () => {
    const forest = unitForest([{ id: 'a', name: 'А', parentId: 'b' }, { id: 'b', name: 'Б', parentId: 'a' }])
    expect([...indexForest(forest).keys()].sort()).toEqual(['a', 'b'])
  })

  it('раскрывает заданное число уровней, находит предков и путь, раскрывает путь к узлу', () => {
    const forest = unitForest(departments)
    const index = indexForest(forest)
    expect([...expandedToLevel(forest, 1)]).toEqual([])
    expect([...expandedToLevel(forest, 2)]).toEqual(['c'])
    expect([...expandedToLevel(forest, null)].sort()).toEqual(['c', 'dev'])
    expect(ancestorsOf(index, 'qa')).toEqual(['c', 'dev'])
    expect(ancestorsOf(index, 'c')).toEqual([])
    expect([...pathTo(index, 'qa')]).toEqual(['c', 'dev', 'qa'])
    expect(pathTo(index, 'нет').size).toBe(0)
    expect([...revealIn(new Set(), index, 'qa')].sort()).toEqual(['c', 'dev'])
  })
})

describe('схема «Люди»', () => {
  it('по вычисленному руководителю, без отключённых; прямые и вся ветка; сначала большие команды', () => {
    const forest = peopleForest(people)
    const index = indexForest(forest)
    expect(index.has(7)).toBe(false)
    const ceo = forest[0]
    expect(ceo.id).toBe(1)
    expect(ceo.children.map((n) => n.id)).toEqual([2, 6])
    expect(ceo).toMatchObject({ reports: 2, branch: 5 })
    expect(index.get(2).node).toMatchObject({ reports: 2, branch: 3 })
    // У Ковалёвой есть подчинённый — она раньше Никитиной
    expect(index.get(2).node.children.map((n) => n.id)).toEqual([4, 3])
    expect(ancestorsOf(index, 5)).toEqual([1, 2, 4])
    expect(forestDepth(forest)).toBe(4)
  })

  it('без руководителя и подчинённых — одной свёрнутой группой; цикл не прячет людей', () => {
    const forest = peopleForest(people)
    const loose = forest.find((n) => n.id === LOOSE_ID)
    expect(loose.group).toBe(true)
    expect(loose.children.map((n) => [n.id, n.depth])).toEqual([[9, 1], [8, 1]])
    expect(forest[forest.length - 1]).toBe(loose)
    expect(ancestorsOf(indexForest(forest), 8)).toEqual([LOOSE_ID])
    expect(expandedToLevel(forest, null).has(LOOSE_ID)).toBe(false)
    const ids = [...indexForest(forest).keys()]
    expect(ids).toEqual(expect.arrayContaining([10, 11]))
    expect(peopleForest(people, { groupLoose: false }).some((n) => n.id === LOOSE_ID)).toBe(false)
  })

  it('«первое лицо» без команды остаётся корнем, а не уходит в группу', () => {
    const forest = peopleForest([{ id: 1, name: 'Председатель', directManagerId: null, reportsToNone: true }])
    expect(forest.map((n) => n.id)).toEqual([1])
  })

  it('2000 сотрудников и 300 подразделений — быстро и без потерь', () => {
    const deps = Array.from({ length: 300 }, (_, i) => ({ id: `d${i}`, name: `Отдел ${i}`, parentId: i ? `d${Math.floor((i - 1) / 4)}` : null, headIds: [i], primaryHeadId: i }))
    const staff = Array.from({ length: 2000 }, (_, i) => ({ id: i, name: `Сотрудник ${i}`, directManagerId: i ? Math.floor((i - 1) / 5) : null, isActive: true }))
    const started = performance.now()
    const team = peopleForest(staff)
    const units = unitForest(deps)
    const rows = unitTableRows(deps, staff)
    expect(indexForest(team).size).toBe(2000)
    expect(indexForest(units).size).toBe(300)
    expect(rows).toHaveLength(300)
    expect(team[0].branch).toBe(1999)
    expect(performance.now() - started).toBeLessThan(500)
  })
})

describe('таблица подразделений', () => {
  const rows = unitTableRows(departments, people, (kind) => ({ company: 'Компания', group: 'Группа' })[kind] || kind)

  it('путь, уровень, тип, руководители и численность — в порядке дерева', () => {
    expect(rows.map((r) => r.id)).toEqual(['c', 'dev', 'an', 'qa', 'fin', 'lost'])
    const an = rows.find((r) => r.id === 'an')
    expect(an).toMatchObject({
      path: 'ООО «Вектор» / Разработка / Группа аналитики', parentPath: 'ООО «Вектор» / Разработка', level: 3, kindLabel: 'Группа',
      // Основной не отмечен — первым показан первый из руководителей
      headName: 'Орлов Сергей', headNames: 'Орлов Сергей, Ковалёва Мария', headCount: 2, employeeCount: 2, branchEmployeeCount: 2,
    })
    expect(rows.find((r) => r.id === 'c')).toMatchObject({ headName: 'Егорова Анна', headPosition: 'Генеральный директор', childCount: 2, level: 1 })
    expect(rows.find((r) => r.id === 'qa')).toMatchObject({ headName: '', headCount: 0 })
  })

  it('сортировка по колонкам: путь — порядок дерева, числа, текст; без руководителя — в конце', () => {
    expect(sortRows(rows, 'path', 'desc').map((r) => r.id)).toEqual(['lost', 'fin', 'qa', 'an', 'dev', 'c'])
    expect(sortRows(rows, 'branchEmployeeCount', 'desc').map((r) => r.id).slice(0, 2)).toEqual(['c', 'dev'])
    expect(sortRows(rows, 'employeeCount', 'asc')[0].id).toBe('lost')
    const byHead = sortRows(rows, 'headName', 'asc').map((r) => r.headName)
    expect(byHead.slice(0, 3)).toEqual(['Егорова Анна', 'Лебедев Олег', 'Орлов Сергей'])
    expect(sortRows(rows, 'headName', 'desc').map((r) => r.headName).slice(-1)).toEqual([''])
    // Исходный массив не меняется
    expect(rows[0].id).toBe('c')
  })

  it('фильтр по названию, пути, типу и руководителю; «ё» = «е»', () => {
    expect(filterRows(rows, 'группа').map((r) => r.id)).toEqual(['an', 'qa'])
    expect(filterRows(rows, 'ковалева').map((r) => r.id)).toEqual(['an'])
    expect(filterRows(rows, 'разработка').map((r) => r.id)).toEqual(['dev', 'an', 'qa'])
    expect(filterRows(rows, '  ')).toBe(rows)
  })

  it('CSV для Excel: BOM, «;», кавычки и апостроф перед формулой', () => {
    const csv = tableCsv(rows)
    expect(csv.charCodeAt(0)).toBe(0xfeff)
    const lines = csv.slice(1).split('\r\n')
    expect(lines).toHaveLength(rows.length + 1)
    expect(lines[0]).toBe('Путь;Подразделение;Тип;Уровень;Вышестоящее;Руководитель;Должность руководителя;Все руководители;Сотрудников;Всего в ветке;Вложенных подразделений')
    expect(lines[1]).toBe('ООО «Вектор»;ООО «Вектор»;Компания;1;;Егорова Анна;Генеральный директор;Егорова Анна;1;6;2')
    expect(lines[3]).toContain(';Орлов Сергей;Аналитик;Орлов Сергей, Ковалёва Мария;2;2;0')
  })
})

describe('CSV', () => {
  it('ячейки: кавычки, формулы, числа', () => {
    expect(csvCell('ООО «Вектор»; филиал')).toBe('"ООО «Вектор»; филиал"')
    expect(csvCell('x"y')).toBe('"x""y"')
    expect(csvCell('=СУММ(A1)')).toBe("'=СУММ(A1)")
    expect(csvCell('+7 900')).toBe("'+7 900")
    expect(csvCell('@admin')).toBe("'@admin")
    expect(csvCell(-3)).toBe('-3')
    expect(csvCell('-2,5')).toBe('-2,5')
    expect(csvCell(null)).toBe('')
    expect(csvText(['А', 'Б'], [[1, 'a\nb']])).toBe('\uFEFFА;Б\r\n1;"a\nb"')
  })
})

describe('поиск и настройки', () => {
  it('поиск находит подразделения и людей, сначала по началу слова, отключённых — нет', () => {
    expect(searchOrg('группа', departments, people).map((r) => r.id)).toEqual(['an', 'qa'])
    expect(searchOrg('ольга', departments, people).map((r) => r.id)).toEqual([3])
    expect(searchOrg('аналитик', departments, people).map((r) => [r.type, r.id])).toEqual([['unit', 'an'], ['person', 4], ['person', 5]])
    expect(searchOrg('уволен', departments, people)).toEqual([])
    expect(searchOrg('ковалева', departments, people).map((r) => r.id)).toEqual([4])
    expect(searchOrg('  ', departments, people)).toEqual([])
    expect(searchOrg('о', departments, people, 3)).toHaveLength(3)
  })

  it('вид и личные настройки: неизвестное и повреждённое — по умолчанию', () => {
    expect(normalizeView('people')).toBe('people')
    expect(normalizeView('grid')).toBe(null)
    expect(readOrgPrefs(null)).toEqual({ view: null, levels: 3, zoom: 1, showKind: true })
    expect(readOrgPrefs('{не json')).toEqual({ view: null, levels: 3, zoom: 1, showKind: true })
    expect(readOrgPrefs('[1]').levels).toBe(3)
    expect(readOrgPrefs(JSON.stringify({ view: 'table', levels: 'all', zoom: 9, showKind: false }))).toEqual({ view: 'table', levels: 'all', zoom: 1.6, showKind: false })
    expect(readOrgPrefs(JSON.stringify({ view: 'x', levels: 0, zoom: 'a' }))).toMatchObject({ view: null, levels: 3, zoom: 1 })
    expect(clampZoom(0.1)).toBe(0.3)
    expect(clampZoom(0.75)).toBe(0.75)
  })
})
