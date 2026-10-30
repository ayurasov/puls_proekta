import { readZip, readZipText, sha256Hex, ZipFormatError } from './zip'

/**
 * Загрузка сотрудников файлом podelu.employees (как в todo-planner «По Делу»): чтение файла в
 * браузере, решения администратора по коллизиям и отправка фото пачками. Сервер строит план по
 * содержимому и решениям; здесь хранится только то, что выбрал администратор.
 */
export const CHOICE_LABEL = { current: 'Система', incoming: 'Файл', merge: 'Объединить', custom: 'Своё' }
export const NEW_USER_MODES = [
  { value: 'inactive', label: 'Неактивными', hint: 'Не входят и не выбираются исполнителями, пока администратор их не включит' },
  { value: 'active', label: 'Активными', hint: 'Видны в справочнике и структуре; войти смогут, когда администратор задаст пароль или включит вход через домен' },
  { value: 'file', label: 'Как в файле', hint: 'По столбцу «Активен» (нет столбца — активными)' },
]
export const ROLE_OPTIONS = [
  { value: 'viewer', label: 'Наблюдатель' },
  { value: 'editor', label: 'Редактор' },
  { value: 'manager', label: 'Менеджер' },
  { value: 'admin', label: 'Администратор' },
]
const MAX_FILE_BYTES = 200 * 1024 * 1024

export class EmployeeFileError extends Error {}

/** Текст CSV: UTF-8, а если файл сохранён Excel в кодировке Windows — windows-1251. */
export function decodeCsv(bytes) {
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes).replace(/^\uFEFF/, '')
  } catch {
    return new TextDecoder('windows-1251').decode(bytes)
  }
}

/** Ключ записи — как на сервере: email, а без него — логин. */
export function recordKey(item) {
  const email = String(item?.email || '').trim().toLowerCase()
  if (email) return email
  const login = String(item?.login || '').trim().toLowerCase()
  return login ? `login:${login}` : ''
}

function extension(name) {
  return (String(name || '').split('.').pop() || '').toLowerCase()
}

/**
 * File → { format, content, avatars: {ключ: sha256}, photos: Map(ключ → Blob), count }.
 * ZIP разбирается здесь: на сервер уходит только employees.json, фото — после применения.
 */
export async function readEmployeeFile(file) {
  if (!file) throw new EmployeeFileError('Файл не выбран')
  if (file.size > MAX_FILE_BYTES) throw new EmployeeFileError('Файл больше 200 МБ')
  const ext = extension(file.name)
  const buffer = await file.arrayBuffer()
  if (ext === 'csv' || ext === 'txt') {
    return { format: 'csv', content: decodeCsv(new Uint8Array(buffer)), avatars: {}, photos: new Map(), count: null }
  }
  if (ext === 'json') {
    return { format: 'json', content: new TextDecoder().decode(buffer), avatars: {}, photos: new Map(), count: null }
  }
  if (ext !== 'zip') throw new EmployeeFileError('Нужен файл CSV, JSON или ZIP-архив выгрузки')
  let entries
  try {
    entries = readZip(buffer)
  } catch (error) {
    throw new EmployeeFileError(error instanceof ZipFormatError ? error.message : 'Не удалось прочитать архив')
  }
  const content = await readZipText(entries, 'employees.json')
  if (content == null) throw new EmployeeFileError('В архиве нет employees.json — это не выгрузка сотрудников')
  let document
  try {
    document = JSON.parse(content)
  } catch {
    throw new EmployeeFileError('employees.json в архиве повреждён')
  }
  const avatars = {}
  const photos = new Map()
  for (const item of document?.employees || []) {
    const key = recordKey(item)
    const entry = item?.avatar ? entries.get(item.avatar) : null
    if (!key || !entry) continue
    const bytes = await entry.read()
    avatars[key] = await sha256Hex(bytes)
    photos.set(key, new Blob([bytes]))
  }
  return { format: 'json', content, avatars, photos, count: document?.employees?.length ?? null }
}

// --- решения администратора
export function decisionFor(decisions, record, conflict) {
  return decisions[record.key]?.fields?.[conflict.field]?.choice || conflict.choice || 'current'
}

export function customValueFor(decisions, record, conflict) {
  const stored = decisions[record.key]?.fields?.[conflict.field]
  if (stored && 'value' in stored) return stored.value
  const source = conflict.value ?? (conflict.incoming !== '' ? conflict.incoming : conflict.current)
  if (conflict.kind === 'role') return ROLE_OPTIONS.find((option) => option.label === source)?.value || source || ''
  return Array.isArray(source) ? source.join(conflict.field === 'heads' ? ' | ' : ', ') : (source ?? '')
}

export function withFieldChoice(decisions, key, field, choice, value) {
  const record = decisions[key] || {}
  const fields = { ...(record.fields || {}), [field]: value === undefined ? { choice } : { choice, value } }
  return { ...decisions, [key]: { ...record, fields } }
}

export function withIdentity(decisions, key, identity) {
  return { ...decisions, [key]: { ...(decisions[key] || {}), identity } }
}

/** «Во всех коллизиях: оставить значения системы / взять из файла» — там, где это возможно. */
export function withBulkChoice(decisions, plan, choice) {
  let next = { ...decisions }
  for (const record of plan?.records || []) {
    for (const conflict of record.conflicts || []) {
      if (conflict.choices.includes(choice)) next = withFieldChoice(next, record.key, conflict.field, choice)
    }
  }
  return next
}

/** Варианты решения, которые показываются: «своё» для да/нет и фото не нужно. */
export function visibleChoices(conflict) {
  if (conflict.kind === 'bool' || conflict.kind === 'avatar') return conflict.choices.filter((choice) => choice !== 'custom')
  return conflict.choices
}

export function needsAttention(record) {
  return record.unresolved || record.errors.length > 0 || record.conflicts.length > 0 || record.warnings.length > 0
}

/** Фото по пачкам — каждый запрос меньше предела размера запроса к серверу. */
export function avatarBatches(uploads, ids, photos, { maxBytes = 8 * 1024 * 1024, maxFiles = 40 } = {}) {
  const batches = []
  let current = []
  let size = 0
  for (const key of uploads || []) {
    const blob = photos.get(key)
    const userId = ids?.[key]
    if (!blob || !userId) continue
    if (current.length && (size + blob.size > maxBytes || current.length >= maxFiles)) {
      batches.push(current)
      current = []
      size = 0
    }
    current.push({ userId, blob })
    size += blob.size
  }
  if (current.length) batches.push(current)
  return batches
}
