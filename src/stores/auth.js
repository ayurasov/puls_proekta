import { defineStore } from 'pinia'
import { api, AuthRequiredError, ensureCsrf } from '../api/client'

export const ROLE_LABELS = { admin: 'Администратор', manager: 'Менеджер', editor: 'Редактор', viewer: 'Наблюдатель' }

export const useAuthStore = defineStore('auth', {
  state: () => ({ user: null, checked: false, config: { mode: 'local', ldapEnabled: false } }),
  getters: {
    isAdmin: (s) => s.user?.role === 'admin',
    canCreateProjects: (s) => ['admin', 'manager'].includes(s.user?.role),
    roleLabel: (s) => ROLE_LABELS[s.user?.role] || '',
  },
  actions: {
    async bootstrap() {
      try {
        await ensureCsrf()
        const data = await api.get('/auth/me')
        this.user = data.user
      } catch (err) {
        if (!(err instanceof AuthRequiredError)) console.warn('Проверка сессии не удалась', err)
        this.user = null
      } finally {
        this.checked = true
      }
    },
    async loadConfig() {
      try { this.config = await api.get('/auth/config') } catch { /* экран входа работает и без этого */ }
    },
    async login(login, password) {
      const data = await api.post('/auth/login', { login, password })
      this.user = data.user
      // После входа сессия новая — CSRF-токен берём заново
      await ensureCsrf(true)
      return this.user
    },
    async logout() {
      try { await api.post('/auth/logout') } finally {
        this.user = null
        await ensureCsrf(true).catch(() => {})
      }
    },
    setUser(user) {
      this.user = user
    },
  },
})
