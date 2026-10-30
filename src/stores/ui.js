import { defineStore } from 'pinia'

let nextId = 1

/**
 * Уведомления (тосты) и диалоги приложения. Диалоги заменяют window.confirm/prompt/alert
 * (в 1.0.x подтверждения и ввод названий шли через системные окна браузера).
 */
export const useUiStore = defineStore('ui', {
  state: () => ({
    toasts: [],
    dialogs: [],
    sidebarCollapsed: localStorageGet('puls.sidebarCollapsed') === '1',
    mobileNavOpen: false,
  }),
  actions: {
    toast(input) {
      const toast = typeof input === 'string' ? { title: input } : { ...input }
      toast.id = nextId++
      toast.kind = toast.kind || 'info'
      this.toasts.push(toast)
      const timeout = toast.timeout ?? (toast.kind === 'error' ? 7000 : toast.action ? 6000 : 3500)
      if (timeout > 0) setTimeout(() => this.dismissToast(toast.id), timeout)
      if (this.toasts.length > 4) this.toasts.shift()
      return toast.id
    },
    success(title, body) { return this.toast({ title, body, kind: 'success' }) },
    error(title, body) {
      if (title instanceof Error) return this.toast({ title: title.message || 'Ошибка', kind: 'error' })
      return this.toast({ title, body, kind: 'error' })
    },
    dismissToast(id) {
      this.toasts = this.toasts.filter((t) => t.id !== id)
    },
    openDialog(dialog) {
      return new Promise((resolve) => {
        this.dialogs.push({ ...dialog, id: nextId++, resolve })
      })
    },
    closeDialog(id, value) {
      const dialog = this.dialogs.find((d) => d.id === id)
      this.dialogs = this.dialogs.filter((d) => d.id !== id)
      dialog?.resolve(value)
    },
    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed
      localStorageSet('puls.sidebarCollapsed', this.sidebarCollapsed ? '1' : '0')
    },
  },
})

export function localStorageGet(key) {
  try { return window.localStorage.getItem(key) } catch { return null }
}

export function localStorageSet(key, value) {
  try { window.localStorage.setItem(key, value) } catch { /* приватный режим — не сохраняем */ }
}
