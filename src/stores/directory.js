import { defineStore } from 'pinia'
import { api } from '../api/client'

/** Справочник сотрудников (имя, фото) — для исполнителей, авторов и присутствия. */
export const useDirectoryStore = defineStore('directory', {
  state: () => ({ users: [], loaded: false, loading: null }),
  getters: {
    byId: (s) => Object.fromEntries(s.users.map((u) => [u.id, u])),
  },
  actions: {
    async load(force = false) {
      if (this.loaded && !force) return
      if (this.loading) return this.loading
      this.loading = api.get('/users/directory').then((users) => {
        this.users = users
        this.loaded = true
      }).finally(() => { this.loading = null })
      return this.loading
    },
  },
})
