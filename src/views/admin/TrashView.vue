<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../../api/client'
import { useProjectsStore } from '../../stores/projects'
import { useUiStore } from '../../stores/ui'
import { useDialogs } from '../../composables/useDialogs'
import { formatDateTime } from '../../utils/format'
import AppIcon from '../../components/common/AppIcon.vue'

const ui = useUiStore()
const projects = useProjectsStore()
const { confirm } = useDialogs()
const trash = ref({ projects: [], plans: [] })
const loading = ref(true)

async function load() {
  loading.value = true
  try { trash.value = await api.get('/trash') } catch (err) { ui.error(err) } finally { loading.value = false }
}
onMounted(load)

async function restoreProject(p) {
  try { await api.post(`/projects/${p.id}/restore`); ui.success('Проект восстановлен', p.title); await load(); projects.load() } catch (err) { ui.error(err) }
}
async function purgeProject(p) {
  const ok = await confirm({
    title: `Удалить «${p.title}» навсегда?`,
    message: 'Проект, все его планы, история и сохранённые версии будут удалены без возможности восстановления.',
    confirmText: 'Удалить навсегда',
  })
  if (!ok) return
  try { await api.delete(`/trash/projects/${p.id}`); await load() } catch (err) { ui.error(err) }
}
async function restorePlan(p) {
  try { await api.post(`/plans/${p.id}/restore`); ui.success('План возвращён в архив проекта', p.title); await load() } catch (err) { ui.error(err) }
}
async function purgePlan(p) {
  const ok = await confirm({ title: `Удалить план «${p.title}» навсегда?`, message: 'План, его история и версии будут удалены без возможности восстановления.', confirmText: 'Удалить навсегда' })
  if (!ok) return
  try { await api.delete(`/trash/plans/${p.id}`); await load() } catch (err) { ui.error(err) }
}
</script>

<template>
  <div class="page narrow">
    <div class="page-header">
      <div>
        <h1 class="page-title">Корзина</h1>
        <div class="page-subtitle">Удалённые проекты и планы хранятся здесь, пока их не удалят навсегда.</div>
      </div>
    </div>
    <section class="card block">
      <h2 class="section-title">Проекты</h2>
      <div v-for="p in trash.projects" :key="p.id" class="row">
        <AppIcon name="folder" />
        <div class="who"><div class="name">{{ p.title }}</div><div class="muted small">Удалён {{ formatDateTime(p.deletedAt) }}<span v-if="p.deletedByName"> · {{ p.deletedByName }}</span></div></div>
        <button class="btn btn-sm" type="button" @click="restoreProject(p)"><AppIcon name="undo" :size="14" /> Восстановить</button>
        <button class="btn btn-sm btn-ghost btn-text-danger" type="button" @click="purgeProject(p)">Удалить навсегда</button>
      </div>
      <p v-if="!loading && !trash.projects.length" class="muted small">Пусто</p>
    </section>
    <section class="card block">
      <h2 class="section-title">Планы</h2>
      <div v-for="p in trash.plans" :key="p.id" class="row">
        <AppIcon name="gantt" />
        <div class="who"><div class="name">{{ p.title }}</div><div class="muted small">Проект «{{ p.projectTitle }}» · удалён {{ formatDateTime(p.deletedAt) }}<span v-if="p.deletedByName"> · {{ p.deletedByName }}</span></div></div>
        <button class="btn btn-sm" type="button" @click="restorePlan(p)"><AppIcon name="undo" :size="14" /> Восстановить</button>
        <button class="btn btn-sm btn-ghost btn-text-danger" type="button" @click="purgePlan(p)">Удалить навсегда</button>
      </div>
      <p v-if="!loading && !trash.plans.length" class="muted small">Пусто</p>
    </section>
  </div>
</template>

<style scoped>
.narrow { max-width: 900px; }
.block { padding: 14px 16px; margin-bottom: 14px; display: flex; flex-direction: column; gap: 6px; }
.row { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-top: 1px solid var(--color-border); color: var(--color-text-secondary); }
.who { flex: 1; min-width: 0; color: var(--color-text); }
.name { font-weight: 600; }
</style>
