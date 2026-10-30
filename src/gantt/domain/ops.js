/**
 * Операции над планом на стороне клиента: применение к локальному состоянию (сразу, до
 * ответа сервера) и вычисление обратных операций для «Отменить» / «Повторить».
 * Формат операций совпадает с API POST /api/plans/{id}/ops.
 *
 * Состояние: { items, notes, assignees } — объекты «ключ → сущность» и graveyard —
 * удалённые сущности (для восстановления при отмене удаления).
 */

const COLLECTIONS = { item: 'items', note: 'notes', assignee: 'assignees' }

export function entityOf(op) {
  return op.op.split('.')[0]
}

function collection(state, entity) {
  return state[COLLECTIONS[entity]]
}

function graveyard(state, entity) {
  if (!state.graveyard) state.graveyard = { items: {}, notes: {}, assignees: {} }
  return state.graveyard[COLLECTIONS[entity]]
}

function payloadOf(op, entity) {
  return op[entity] || {}
}

/** Обратные операции для op относительно текущего состояния (вызывать ДО применения). */
export function inverseOf(state, op) {
  const entity = entityOf(op)
  const action = op.op.split('.')[1]
  if (entity === 'plan') {
    const old = {}
    for (const field of Object.keys(op.fields || {})) {
      old[field] = field === 'settings' ? structuredCloneSafe(state.plan?.settings) : state.plan?.[field]
    }
    return [{ op: 'plan.update', fields: old }]
  }
  const coll = collection(state, entity)
  if (action === 'create') return [{ op: `${entity}.delete`, key: payloadOf(op, entity).key }]
  if (action === 'delete') return coll[op.key] ? [{ op: `${entity}.restore`, key: op.key }] : []
  if (action === 'restore') return graveyard(state, entity)[op.key] ? [{ op: `${entity}.delete`, key: op.key }] : []
  if (action === 'update') {
    const current = coll[op.key]
    if (!current) return []
    const old = {}
    for (const field of Object.keys(op.fields || {})) old[field] = current[field] ?? null
    return [{ op: `${entity}.update`, key: op.key, fields: old }]
  }
  return []
}

/** Применить операцию к локальному состоянию. */
export function applyLocal(state, op) {
  const entity = entityOf(op)
  const action = op.op.split('.')[1]
  if (entity === 'plan') {
    if (!state.plan) return
    for (const [field, value] of Object.entries(op.fields || {})) {
      if (field === 'settings') state.plan.settings = mergeSettings(state.plan.settings, value)
      else state.plan[field] = value
    }
    return
  }
  const coll = collection(state, entity)
  const grave = graveyard(state, entity)
  if (action === 'create') {
    const data = { ...payloadOf(op, entity) }
    coll[data.key] = data
    delete grave[data.key]
  } else if (action === 'update') {
    const current = coll[op.key]
    if (current) Object.assign(current, op.fields || {})
  } else if (action === 'delete') {
    const current = coll[op.key]
    if (current) {
      grave[op.key] = current
      delete coll[op.key]
    }
  } else if (action === 'restore') {
    const buried = grave[op.key]
    if (buried) {
      coll[op.key] = buried
      delete grave[op.key]
    }
  }
}

/** Применить пакет, вернуть обратные операции (в обратном порядке). */
export function applyBatch(state, ops) {
  const inverse = []
  for (const op of ops) {
    inverse.unshift(...inverseOf(state, op))
    applyLocal(state, op)
  }
  return inverse
}

/** Ключи сущностей, которые затрагивает пакет: 'item:r12', 'note:n1', 'plan'. */
export function touchedKeys(ops) {
  const keys = new Set()
  for (const op of ops) {
    const entity = entityOf(op)
    if (entity === 'plan') { keys.add('plan'); continue }
    const key = op.key || payloadOf(op, entity).key
    if (key) keys.add(`${entity}:${key}`)
  }
  return keys
}

export function mergeSettings(base, patch) {
  const result = structuredCloneSafe(base || {})
  for (const [key, value] of Object.entries(patch || {})) {
    if (value && typeof value === 'object' && !Array.isArray(value) && result[key] && typeof result[key] === 'object' && !Array.isArray(result[key])) {
      result[key] = { ...result[key], ...value }
    } else {
      result[key] = value
    }
  }
  return result
}

function structuredCloneSafe(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value))
}
