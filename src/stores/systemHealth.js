import { defineStore } from 'pinia'
import { api } from '../api/client'

/**
 * Состояние системы (ADR-0014): последняя сводка — для страницы и точки у пункта меню. Загружается при входе
 * сотрудника с полномочием «Смотрит состояние системы» (у администратора — всегда) и при открытии страницы.
 */
export const useSystemHealthStore = defineStore('systemHealth', {
  state: () => ({ data: null, loading: false, error: null }),
  getters: {
    status: (s) => s.data?.status || null,
  },
  actions: {
    async load() {
      this.loading = true
      try {
        this.data = await api.get('/system/health')
        this.error = null
      } catch (err) {
        this.error = err
      } finally {
        this.loading = false
      }
      return this.data
    },
    async check() {
      const data = await api.post('/system/health/check')
      this.data = data
      return data
    },
    reset() {
      this.data = null
      this.error = null
    },
  },
})
