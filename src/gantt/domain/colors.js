/** Палитра 1.0.x (ключи хранятся в данных) и произвольные цвета #rrggbb. */
export const PALETTE = [
  { key: 'blue', color: '#3b82f6', text: '#fff', label: 'Синий' },
  { key: 'teal', color: '#14b8a6', text: '#fff', label: 'Бирюзовый' },
  { key: 'green', color: '#22c55e', text: '#fff', label: 'Зелёный' },
  { key: 'orange', color: '#f97316', text: '#fff', label: 'Оранжевый' },
  { key: 'red', color: '#ef4444', text: '#fff', label: 'Красный' },
  { key: 'purple', color: '#8b5cf6', text: '#fff', label: 'Фиолетовый' },
  { key: 'amber', color: '#f59e0b', text: '#111', label: 'Янтарный' },
  { key: 'pink', color: '#ec4899', text: '#fff', label: 'Розовый' },
  { key: 'slate', color: '#64748b', text: '#fff', label: 'Серый' },
  { key: 'indigo', color: '#6366f1', text: '#fff', label: 'Индиго' },
  { key: 'cyan', color: '#06b6d4', text: '#fff', label: 'Голубой' },
  { key: 'lime', color: '#84cc16', text: '#111', label: 'Лайм' },
]
const BY_KEY = new Map(PALETTE.map((c) => [c.key, c]))

export function isLight(hex) {
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  return (r * 299 + g * 587 + b * 114) / 1000 > 150
}

/** { color, text } для ключа палитры или #rrggbb; неизвестное — синий. */
export function resolveColor(value, fallback = 'blue') {
  if (typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value)) {
    return { key: value, color: value, text: isLight(value) ? '#111' : '#fff' }
  }
  return BY_KEY.get(value) || BY_KEY.get(fallback) || PALETTE[0]
}

export const STATUSES = [
  { key: 'new', label: 'Новая', color: '#4f7cff' },
  { key: 'inwork', label: 'В работе', color: '#e8a13a' },
  { key: 'blocked', label: 'Заблокирована', color: '#e5484d' },
  { key: 'done', label: 'Завершена', color: '#2fa96b' },
]
export const STATUS_BY_KEY = Object.fromEntries(STATUSES.map((s) => [s.key, s]))

export const DEPENDENCY_TYPES = [
  { key: 'FS', label: 'Окончание → начало', short: 'ОН' },
  { key: 'SS', label: 'Начало → начало', short: 'НН' },
  { key: 'FF', label: 'Окончание → окончание', short: 'ОО' },
  { key: 'SF', label: 'Начало → окончание', short: 'НО' },
]
