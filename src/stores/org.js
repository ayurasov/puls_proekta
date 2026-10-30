import { defineStore } from 'pinia'
import { api } from '../api/client'

/**
 * Структура компании (ADR-0008): подразделения, их руководители и сотрудники с вычисленным руководителем.
 * Одна загрузка на страницу; после правок — reload().
 */
export const useOrgStore = defineStore('org', {
  state: () => ({ departments: [], people: [], settings: { unitKinds: [], maxDepth: 15 }, loaded: false, loading: null }),
  getters: {
    depById: (s) => Object.fromEntries(s.departments.map((d) => [d.id, d])),
    personById: (s) => Object.fromEntries(s.people.map((p) => [p.id, p])),
    kindById: (s) => Object.fromEntries(s.settings.unitKinds.map((k) => [k.value, k])),
    children: (s) => {
      const map = new Map()
      for (const d of s.departments) {
        if (!map.has(d.parentId || null)) map.set(d.parentId || null, [])
        map.get(d.parentId || null).push(d)
      }
      return map
    },
    membersOf: (s) => {
      const map = new Map()
      for (const p of s.people) {
        if (!p.departmentId) continue
        if (!map.has(p.departmentId)) map.set(p.departmentId, [])
        map.get(p.departmentId).push(p)
      }
      return map
    },
  },
  actions: {
    async load(force = false) {
      if (this.loaded && !force) return
      if (this.loading) return this.loading
      this.loading = api.get('/org/tree').then((data) => {
        this.departments = data.departments
        this.people = data.people
        this.settings = data.settings
        this.loaded = true
      }).finally(() => { this.loading = null })
      return this.loading
    },
    reload() {
      this.loaded = false
      return this.load(true)
    },
    path(depId) {
      const names = []
      const seen = new Set()
      let dep = this.depById[depId]
      while (dep && !seen.has(dep.id)) {
        seen.add(dep.id)
        names.unshift(dep.name)
        dep = this.depById[dep.parentId]
      }
      return names
    },
    pathText(depId) {
      return this.path(depId).join(' / ')
    },
    kindLabel(kind) {
      return this.kindById[kind]?.label || kind
    },
    kindColor(kind) {
      return this.kindById[kind]?.color || '#64748b'
    },
    /** Подразделения списком сверху вниз с отступом — для выпадающих списков. */
    options({ exclude = null } = {}) {
      const out = []
      const skip = new Set()
      if (exclude) {
        const stack = [exclude]
        while (stack.length) {
          const id = stack.pop()
          skip.add(id)
          for (const child of this.children.get(id) || []) stack.push(child.id)
        }
      }
      for (const dep of this.departments) {
        if (!skip.has(dep.id)) out.push({ id: dep.id, label: `${'\u00a0\u00a0\u00a0'.repeat(Math.max(0, dep.level - 1))}${dep.name}`, depth: dep.level })
      }
      return out
    },
    subtreeIds(depId) {
      const out = []
      const stack = [depId]
      while (stack.length) {
        const id = stack.pop()
        out.push(id)
        for (const child of this.children.get(id) || []) stack.push(child.id)
      }
      return out
    },
  },
})
