<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import { useProjectsStore } from '../../stores/projects'
import { useUiStore } from '../../stores/ui'
import { useDialogs } from '../../composables/useDialogs'
import AppIcon from '../../components/common/AppIcon.vue'
import HelpLink from '../../components/common/HelpLink.vue'
import ColorPicker from '../../gantt/components/ColorPicker.vue'

const projects = useProjectsStore()
const ui = useUiStore()
const { confirm } = useDialogs()
const name = ref('')
const editing = ref(null)
const dragId = ref(null)

onMounted(() => projects.load().catch((err) => ui.error(err)))
const counts = computed(() => {
  const map = {}
  for (const p of projects.projects) map[p.directionId] = (map[p.directionId] || 0) + 1
  return map
})

async function add() {
  if (!name.value.trim()) return
  try {
    await api.post('/directions', { name: name.value.trim() })
    name.value = ''
    await projects.load()
  } catch (err) { ui.error(err) }
}
async function save() {
  const e = editing.value
  if (!e.name.trim()) return
  try {
    await api.patch(`/directions/${e.id}`, { name: e.name.trim(), description: e.description, color: e.color })
    editing.value = null
    await projects.load()
  } catch (err) { ui.error(err) }
}
async function remove(direction) {
  const n = counts.value[direction.id] || 0
  const ok = await confirm({
    title: `Удалить направление «${direction.name}»?`,
    message: n
      ? `${n} проект(ов) направления останутся и переместятся в «Без направления». Правила доступа, выданные на всё направление, будут удалены.`
      : 'Проектов в направлении нет.',
    confirmText: 'Удалить',
  })
  if (!ok) return
  try {
    await api.delete(`/directions/${direction.id}`)
    await projects.load()
  } catch (err) { ui.error(err) }
}
async function drop(target) {
  const moving = dragId.value
  dragId.value = null
  if (!moving || moving === target.id) return
  const ids = projects.directions.map((d) => d.id).filter((id) => id !== moving)
  ids.splice(ids.indexOf(target.id), 0, moving)
  try {
    await api.post('/directions/reorder', { ids })
    await projects.load()
  } catch (err) { ui.error(err) }
}
</script>

<template>
  <div class="page narrow">
    <div class="page-header">
      <div>
        <h1 class="page-title">Направления</h1>
        <div class="page-subtitle">Группы проектов на главной странице. Порядок меняется перетаскиванием.</div>
      </div>
      <HelpLink topic="admin-directions" />
    </div>
    <form class="card add" @submit.prevent="add">
      <input v-model="name" class="input" placeholder="Название нового направления" maxlength="255" />
      <button class="btn btn-primary" type="submit" :disabled="!name.trim()"><AppIcon name="plus" /> Добавить</button>
    </form>
    <div class="card list">
      <div v-for="d in projects.directions" :key="d.id" class="row" draggable="true" @dragstart="dragId = d.id" @dragover.prevent @drop="drop(d)">
        <template v-if="editing?.id === d.id">
          <div class="edit">
            <input v-model="editing.name" class="input" maxlength="255" />
            <input v-model="editing.description" class="input" maxlength="5000" placeholder="Описание (необязательно)" />
            <ColorPicker v-model="editing.color" />
            <div class="edit-actions">
              <button class="btn btn-primary btn-sm" type="button" @click="save">Сохранить</button>
              <button class="btn btn-ghost btn-sm" type="button" @click="editing = null">Отмена</button>
            </div>
          </div>
        </template>
        <template v-else>
          <span class="grip"><AppIcon name="grip" :size="14" /></span>
          <span class="swatch" :class="`c-${d.color}`" :style="d.color?.startsWith('#') ? { background: d.color } : null" />
          <div class="who">
            <div class="name">{{ d.name }}</div>
            <div v-if="d.description" class="muted small">{{ d.description }}</div>
          </div>
          <span class="muted small">{{ counts[d.id] || 0 }} проектов</span>
          <button class="btn btn-ghost btn-icon btn-sm" type="button" title="Изменить" @click="editing = { ...d }"><AppIcon name="edit" :size="15" /></button>
          <button class="btn btn-ghost btn-icon btn-sm btn-text-danger" type="button" title="Удалить" @click="remove(d)"><AppIcon name="trash" :size="15" /></button>
        </template>
      </div>
      <p v-if="!projects.directions.length" class="muted empty">Направлений нет — все проекты на главной в «Без направления».</p>
    </div>
  </div>
</template>

<style scoped>
.narrow { max-width: 820px; }
.add { display: flex; gap: 8px; padding: 12px; margin-bottom: 14px; }
.list { padding: 6px; }
.row { display: flex; align-items: center; gap: 10px; padding: 10px; border-radius: var(--radius-sm); }
.row:hover { background: var(--color-surface-2); }
.grip { color: #c0c6d4; cursor: grab; }
.swatch { width: 12px; height: 12px; border-radius: 4px; background: #64748b; flex-shrink: 0; }
.who { flex: 1; min-width: 0; }
.name { font-weight: 600; }
.edit { flex: 1; display: flex; flex-direction: column; gap: 8px; }
.edit-actions { display: flex; gap: 6px; }
.empty { padding: 16px; text-align: center; }
.c-blue { background: #3b82f6; } .c-teal { background: #14b8a6; } .c-green { background: #22c55e; } .c-orange { background: #f97316; }
.c-red { background: #ef4444; } .c-purple { background: #8b5cf6; } .c-amber { background: #f59e0b; } .c-pink { background: #ec4899; }
.c-slate { background: #64748b; } .c-indigo { background: #6366f1; } .c-cyan { background: #06b6d4; } .c-lime { background: #84cc16; }
</style>
