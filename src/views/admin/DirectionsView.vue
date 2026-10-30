<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import { useProjectsStore } from '../../stores/projects'
import { useUiStore } from '../../stores/ui'
import { useDialogs } from '../../composables/useDialogs'
import AppIcon from '../../components/common/AppIcon.vue'
import { countLabel } from '../../utils/format'
import ColorPicker from '../../gantt/components/ColorPicker.vue'

const projects = useProjectsStore()
const ui = useUiStore()
const { confirm } = useDialogs()
const name = ref('')
const editing = ref(null)
const dragId = ref(null)
const dropTarget = ref(null)

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
async function startEdit(d) {
  editing.value = { ...d }
  await nextTick()
  document.querySelector('.edit .input')?.focus()
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
      ? `${countLabel(n, 'проект', 'проекта', 'проектов')} направления ${n % 10 === 1 && n % 100 !== 11 ? 'останется и переместится' : 'останутся и переместятся'} в «Без направления». Правила доступа, выданные на всё направление, будут удалены.`
      : 'Проектов в направлении нет.',
    confirmText: 'Удалить',
  })
  if (!ok) return
  try {
    await api.delete(`/directions/${direction.id}`)
    await projects.load()
  } catch (err) { ui.error(err) }
}
// Перетаскивание: строка в режиме правки не перетаскивается (иначе выделение текста в поле тащило строку),
// место вставки показывает линия над или под строкой
function onDragStart(event, d) {
  if (editing.value) { event.preventDefault(); return }
  dragId.value = d.id
  event.dataTransfer.effectAllowed = 'move'
}
function onDragOver(event, d) {
  if (!dragId.value) return
  event.preventDefault()
  const rect = event.currentTarget.getBoundingClientRect()
  dropTarget.value = { id: d.id, after: event.clientY > rect.top + rect.height / 2 }
}
function onDragEnd() {
  dragId.value = null
  dropTarget.value = null
}
async function drop(target) {
  const moving = dragId.value
  const after = dropTarget.value?.id === target.id && dropTarget.value.after
  dragId.value = null
  dropTarget.value = null
  if (!moving || moving === target.id) return
  const ids = projects.directions.map((d) => d.id).filter((id) => id !== moving)
  ids.splice(ids.indexOf(target.id) + (after ? 1 : 0), 0, moving)
  try {
    await api.post('/directions/reorder', { ids })
    await projects.load()
  } catch (err) { ui.error(err) }
}
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Направления</h1>
        <div class="page-subtitle">Группы проектов на главной странице. Порядок меняется перетаскиванием.</div>
      </div>
    </div>
    <form class="card add" @submit.prevent="add">
      <input v-model="name" class="input" placeholder="Название нового направления" maxlength="255" />
      <button class="btn btn-primary" type="submit" :disabled="!name.trim()"><AppIcon name="plus" /> Добавить</button>
    </form>
    <div class="card list">
      <div
        v-for="d in projects.directions" :key="d.id" class="row"
        :class="{ dragging: dragId === d.id, 'drop-before': dropTarget?.id === d.id && !dropTarget.after && dragId !== d.id, 'drop-after': dropTarget?.id === d.id && dropTarget.after && dragId !== d.id }"
        :draggable="!editing" @dragstart="onDragStart($event, d)" @dragover="onDragOver($event, d)" @drop.prevent="drop(d)" @dragend="onDragEnd"
      >
        <template v-if="editing?.id === d.id">
          <form class="edit" @submit.prevent="save" @keydown.esc.stop.prevent="editing = null">
            <input v-model="editing.name" class="input" maxlength="255" aria-label="Название" />
            <input v-model="editing.description" class="input" maxlength="5000" placeholder="Описание (необязательно)" aria-label="Описание" />
            <ColorPicker v-model="editing.color" />
            <div class="edit-actions">
              <button class="btn btn-primary btn-sm" type="submit" :disabled="!editing.name.trim()">Сохранить</button>
              <button class="btn btn-ghost btn-sm" type="button" @click="editing = null">Отмена</button>
              <span class="muted small">Enter — сохранить, Esc — отменить</span>
            </div>
          </form>
        </template>
        <template v-else>
          <span class="grip" :class="{ disabled: editing }" title="Перетащите, чтобы изменить порядок"><AppIcon name="grip" :size="14" /></span>
          <span class="swatch" :class="`c-${d.color}`" :style="d.color?.startsWith('#') ? { background: d.color } : null" />
          <div class="who">
            <div class="name">{{ d.name }}</div>
            <div v-if="d.description" class="muted small">{{ d.description }}</div>
          </div>
          <span class="muted small">{{ countLabel(counts[d.id] || 0, 'проект', 'проекта', 'проектов') }}</span>
          <button class="btn btn-ghost btn-icon btn-sm" type="button" title="Изменить" @click="startEdit(d)"><AppIcon name="edit" :size="15" /></button>
          <button class="btn btn-ghost btn-icon btn-sm btn-text-danger" type="button" title="Удалить" @click="remove(d)"><AppIcon name="trash" :size="15" /></button>
        </template>
      </div>
      <p v-if="!projects.directions.length" class="muted empty">Направлений нет — все проекты на главной в «Без направления».</p>
    </div>
  </div>
</template>

<style scoped>
.add { display: flex; gap: 8px; padding: 12px; margin-bottom: 14px; }
.list { padding: 6px; }
.row { display: flex; align-items: center; gap: 10px; padding: 10px; border-radius: var(--radius-sm); }
.row:hover { background: var(--color-surface-2); }
.grip { color: #c0c6d4; cursor: grab; }
.grip.disabled { cursor: default; opacity: 0.4; }
.row.dragging { opacity: 0.45; }
.row.drop-before { box-shadow: inset 0 2px 0 var(--color-primary); }
.row.drop-after { box-shadow: inset 0 -2px 0 var(--color-primary); }
.edit-actions { align-items: center; }
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
