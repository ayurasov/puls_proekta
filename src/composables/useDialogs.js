import { useUiStore } from '../stores/ui'

/**
 * Диалоги в стиле приложения вместо системных confirm/prompt:
 *   if (await confirm({ title: 'Удалить задачу?', message: '…', confirmText: 'Удалить', danger: true })) …
 *   const name = await prompt({ title: 'Новая группа', label: 'Название', value: '' })
 */
export function useDialogs() {
  const ui = useUiStore()
  return {
    confirm: (options) => ui.openDialog({ type: 'confirm', confirmText: 'Удалить', danger: true, ...options }),
    prompt: (options) => ui.openDialog({ type: 'prompt', confirmText: 'Сохранить', ...options }),
    alert: (options) => ui.openDialog({ type: 'alert', confirmText: 'Понятно', ...options }),
  }
}
