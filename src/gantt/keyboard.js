/**
 * Где нажата клавиша — чтобы сочетания диаграммы (Enter, Delete, стрелки, F2, Insert, Ctrl+D) не
 * перехватывали клавиши у элементов, которым они принадлежат:
 *
 * * 'typing' — поле ввода: ничего, кроме собственных клавиш поля;
 * * 'owned'  — карточка строки, меню, окно, уведомление, боковое меню: у них своя клавиатура
 *              (Enter нажимает кнопку меню, стрелки ходят по пунктам);
 * * 'control' — кнопка, ссылка, вкладка: Enter и пробел нажимают её, а не открывают карточку;
 * * 'free'   — диаграмма и пустое место страницы: работают сочетания диаграммы.
 */
const TYPING_TAGS = /^(INPUT|TEXTAREA|SELECT)$/
const OWNERS = '.panel, .menu, [role="menu"], [role="dialog"], .modal-overlay, .toast-viewport, .sidebar'
const CONTROLS = 'button, a[href], summary, label, [role="button"], [role="tab"], [role="radio"], [role="checkbox"], [role="switch"], [role="menuitem"], [role="option"]'

export function keyContext(event) {
  const target = event?.target
  if (!target || typeof target.closest !== 'function') return 'free'
  if (target.isContentEditable || TYPING_TAGS.test(target.tagName || '')) return 'typing'
  if (target.closest(OWNERS)) return 'owned'
  if (target.closest(CONTROLS)) return 'control'
  return 'free'
}

/** Сочетания отмены — везде, кроме полей ввода (там своя отмена набранного текста). */
export function isUndoRedo(event) {
  const mod = Boolean(event.ctrlKey || event.metaKey)
  const key = (event.key || '').toLowerCase()
  return mod && (key === 'z' || key === 'y')
}
