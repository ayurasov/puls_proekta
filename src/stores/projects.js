import { defineStore } from 'pinia'
import { api } from '../api/client'

export const ACCESS_LABELS = { view: 'Просмотр', edit: 'Редактирование', manage: 'Полное управление' }

export const useProjectsStore = defineStore('projects', {
  state: () => ({ projects: [], directions: [], loaded: false, loading: false }),
  getters: {
    byId: (s) => Object.fromEntries(s.projects.map((p) => [p.id, p])),
    grouped(state) {
      const groups = state.directions.map((d) => ({
        direction: d,
        projects: state.projects.filter((p) => p.directionId === d.id).sort(byPosition),
      }))
      const known = new Set(state.directions.map((d) => d.id))
      const orphans = state.projects.filter((p) => !p.directionId || !known.has(p.directionId)).sort(byPosition)
      groups.push({ direction: null, projects: orphans })
      return groups
    },
  },
  actions: {
    async load() {
      this.loading = true
      try {
        const [projects, directions] = await Promise.all([api.get('/projects'), api.get('/directions')])
        this.projects = projects
        this.directions = directions
        this.loaded = true
      } finally {
        this.loading = false
      }
    },
    async create(payload) {
      const project = await api.post('/projects', payload)
      await this.load()
      return project
    },
    async update(id, payload) {
      const project = await api.patch(`/projects/${id}`, payload)
      const index = this.projects.findIndex((p) => p.id === id)
      if (index >= 0) this.projects[index] = { ...this.projects[index], ...project }
      return project
    },
    async remove(id) {
      await api.delete(`/projects/${id}`)
      this.projects = this.projects.filter((p) => p.id !== id)
    },
    async reorder(items) {
      for (const item of items) {
        const project = this.projects.find((p) => p.id === item.id)
        if (project) Object.assign(project, { directionId: item.directionId, position: item.position })
      }
      await api.post('/projects/reorder', { items })
    },
  },
})

function byPosition(a, b) {
  return (a.position - b.position) || (a.id - b.id)
}
