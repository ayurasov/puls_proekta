/**
 * Ключи интеграций (ADR-0011): права и системы — те же правила, что на сервере (services/integration_keys.py).
 * projects.stats без projects.read смысла не имеет — read добавляется сам; порядок прав — как в каталоге.
 */
export const SCOPES = [
  { value: 'projects.read', label: 'Проекты и планы', hint: 'Проекты, их планы и строки, направления — по правам сотрудника, если он передан' },
  { value: 'projects.stats', label: 'Показатели проектов', hint: 'Задачи в работе и заблокированные, направление, время изменения, окно плана — для сводных отчётов' },
]

export const APPS = [
  { value: 'podelu', label: '«По Делу»', name: 'По Делу', scopes: ['projects.read'] },
  { value: 'dashboards', label: '«Дашборды компании»', name: 'Дашборды компании', scopes: ['projects.read', 'projects.stats'] },
  { value: 'other', label: 'Другая система', name: '', scopes: ['projects.read'] },
]

export function normalizeScopes(list) {
  const wanted = new Set(list || [])
  if (wanted.has('projects.stats')) wanted.add('projects.read')
  return SCOPES.map((s) => s.value).filter((v) => wanted.has(v))
}

/** Переключить право: снятие read снимает и stats (stats без read не бывает), включение stats включает read. */
export function toggleScope(list, value) {
  const set = new Set(list || [])
  if (set.has(value)) {
    set.delete(value)
    if (value === 'projects.read') set.delete('projects.stats')
  } else set.add(value)
  return normalizeScopes([...set])
}

export function scopeLabel(value) {
  return SCOPES.find((s) => s.value === value)?.label || value
}

export function appLabel(value) {
  return APPS.find((a) => a.value === value)?.label || value
}

/** Действующие — сначала, по дате выпуска (новые выше); отозванные — в конце. */
export function sortKeys(keys) {
  return [...keys].sort((a, b) => (Number(!a.active) - Number(!b.active))
    || String(b.issuedAt || '').localeCompare(String(a.issuedAt || '')) || b.id - a.id)
}
