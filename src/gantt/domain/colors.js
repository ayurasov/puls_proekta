/** Тёмный текст — только на светлых полосах; на насыщенных и тёмных — белый (см. readableText). */
export const DARK_TEXT = '#000000'

function channel(value) {
  const c = value / 255
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}
function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}
/** Цвет с прозрачностью поверх белого фона (выполненные и заблокированные полосы бледнее). */
export function blendWithWhite(hex, opacity) {
  return '#' + [1, 3, 5].map((i) => Math.round(parseInt(hex.slice(i, i + 2), 16) * opacity + 255 * (1 - opacity))
    .toString(16).padStart(2, '0')).join('')
}
export function contrastRatio(a, b) {
  const [x, y] = [luminance(a), luminance(b)]
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}
/** Воспринимаемый контраст текста txt на фоне bg по APCA (|Lc|, 0–106). WCAG 2 на насыщенных средних тонах
 * ошибается: на синей, красной, фиолетовой полосе «контрастнее» у него чёрный текст, хотя глазу читаемее белый. */
export function apcaContrast(txt, bg) {
  const y = (hex) => {
    const [r, g, b] = [1, 3, 5].map((i) => (parseInt(hex.slice(i, i + 2), 16) / 255) ** 2.4)
    const value = 0.2126729 * r + 0.7151522 * g + 0.072175 * b
    return value > 0.022 ? value : value + (0.022 - value) ** 1.414
  }
  const [t, b] = [y(txt), y(bg)]
  if (b > t) {
    const sapc = (b ** 0.56 - t ** 0.57) * 1.14
    return sapc < 0.1 ? 0 : (sapc - 0.027) * 100
  }
  const sapc = (b ** 0.65 - t ** 0.62) * 1.14
  return sapc > -0.1 ? 0 : -(sapc + 0.027) * 100
}
/** Белый или тёмный текст — тот, что воспринимается контрастнее на полосе цвета hex (с учётом её прозрачности):
 * белый на синем, красном, фиолетовом, сером и тёмных цветах, тёмный — на жёлтом, лаймовом, зелёном, голубом. */
export function readableText(hex, opacity = 1) {
  const fill = opacity < 1 ? blendWithWhite(hex, opacity) : hex
  return apcaContrast('#ffffff', fill) >= apcaContrast(DARK_TEXT, fill) ? '#ffffff' : DARK_TEXT
}

/** Тот же оттенок, затемнённый до контраста ≥ 4,5 с белым фоном — для цветных подписей (пометки, вехи над шкалой). */
export function textColorOnWhite(hex) {
  let color = hex
  for (let step = 1; step <= 10 && contrastRatio(color, '#ffffff') < 4.5; step += 1) {
    const k = 1 - step * 0.08
    color = '#' + [1, 3, 5].map((i) => Math.round(parseInt(hex.slice(i, i + 2), 16) * k).toString(16).padStart(2, '0')).join('')
  }
  return color
}

/** Палитра 1.0.x (ключи хранятся в данных) и произвольные цвета #rrggbb. */
export const PALETTE = [
  { key: 'blue', color: '#3b82f6', label: 'Синий' },
  { key: 'teal', color: '#14b8a6', label: 'Бирюзовый' },
  { key: 'green', color: '#22c55e', label: 'Зелёный' },
  { key: 'orange', color: '#f97316', label: 'Оранжевый' },
  { key: 'red', color: '#ef4444', label: 'Красный' },
  { key: 'purple', color: '#8b5cf6', label: 'Фиолетовый' },
  { key: 'amber', color: '#f59e0b', label: 'Янтарный' },
  { key: 'pink', color: '#ec4899', label: 'Розовый' },
  { key: 'slate', color: '#64748b', label: 'Серый' },
  { key: 'indigo', color: '#6366f1', label: 'Индиго' },
  { key: 'cyan', color: '#06b6d4', label: 'Голубой' },
  { key: 'lime', color: '#84cc16', label: 'Лайм' },
].map((c) => ({ ...c, text: readableText(c.color) }))
const BY_KEY = new Map(PALETTE.map((c) => [c.key, c]))

export function isLight(hex) {
  return readableText(hex) !== '#ffffff'
}

/** { color, text } для ключа палитры или #rrggbb; неизвестное — синий. */
export function resolveColor(value, fallback = 'blue') {
  if (typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value)) {
    return { key: value, color: value, text: readableText(value) }
  }
  return BY_KEY.get(value) || BY_KEY.get(fallback) || PALETTE[0]
}

// color — точки и полосы; text — подписи статуса на белом фоне (контраст ≥ 4,5, WCAG AA)
export const STATUSES = [
  { key: 'new', label: 'Новая', color: '#4f7cff', text: '#2b4db5' },
  { key: 'inwork', label: 'В работе', color: '#e8a13a', text: '#a8670f' },
  { key: 'blocked', label: 'Заблокирована', color: '#e5484d', text: '#c62933' },
  { key: 'done', label: 'Завершена', color: '#2fa96b', text: '#1f7a4c' },
]
// Подписи сдвига в строке: просрочка и опережение
export const LATE_TEXT = '#c62933'
export const EARLY_TEXT = '#1f7a4c'
/** Прозрачность полос выполненных и заблокированных задач (экран и выгрузка). */
export const BAR_OPACITY = { done: 0.78, blocked: 0.7 }
export const STATUS_BY_KEY = Object.fromEntries(STATUSES.map((s) => [s.key, s]))

export const DEPENDENCY_TYPES = [
  { key: 'FS', label: 'Окончание → начало', short: 'ОН' },
  { key: 'SS', label: 'Начало → начало', short: 'НН' },
  { key: 'FF', label: 'Окончание → окончание', short: 'ОО' },
  { key: 'SF', label: 'Начало → окончание', short: 'НО' },
]
