/**
 * Порядок строк: дробные позиции. Новая строка встаёт между соседями; если промежуток
 * исчерпан, братья перенумеровываются с шагом 1024.
 */
export const STEP = 1024

export function positionBetween(before, after) {
  if (before == null && after == null) return STEP
  if (before == null) return after - STEP
  if (after == null) return before + STEP
  const middle = (before + after) / 2
  if (middle - before < 1e-6 || after - middle < 1e-6) return null
  return middle
}

/**
 * Позиция для вставки в список братьев (отсортированных) перед индексом `index`.
 * Возвращает { position, renumber } — renumber: [{ key, position }] при перенумерации.
 */
export function insertPosition(siblings, index) {
  const before = index > 0 ? siblings[index - 1].position : null
  const after = index < siblings.length ? siblings[index].position : null
  const position = positionBetween(before, after)
  if (position != null) return { position, renumber: [] }
  const renumber = siblings.map((item, i) => ({ key: item.key, position: (i + (i >= index ? 2 : 1)) * STEP }))
  return { position: (index + 1) * STEP, renumber }
}
