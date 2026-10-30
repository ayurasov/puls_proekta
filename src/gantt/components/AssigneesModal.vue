<script setup>
import { computed, inject, onMounted, ref } from 'vue'
import BaseModal from '../../components/common/BaseModal.vue'
import AppIcon from '../../components/common/AppIcon.vue'
import HelpLink from '../../components/common/HelpLink.vue'
import PopupMenu from '../../components/common/PopupMenu.vue'
import ColorPicker from './ColorPicker.vue'
import UserAvatar from '../../components/common/UserAvatar.vue'
import { useDirectoryStore } from '../../stores/directory'
import { useDialogs } from '../../composables/useDialogs'
import { resolveColor } from '../domain/colors'
import { countLabel } from '../../utils/format'

/** Исполнители плана: ФИО, роль, цвет и (необязательно) связь с сотрудником системы. */
const emit = defineEmits(['close'])
const editor = inject('gantt')
const directory = useDirectoryStore()
const { confirm } = useDialogs()
const readonly = computed(() => !editor.canEdit)
// Цвет — из той же палитры, что у задач (ключ палитры или свой #rrggbb), а не системный выбор цвета:
// тот показывал ключи палитры («teal», «slate») как чёрный
const DEFAULT_COLOR = 'blue'
const form = ref({ name: '', role: '', color: DEFAULT_COLOR, userId: null })
const editing = ref(null)
const colorMenu = ref(null)
function openColor(event, target) {
  colorMenu.value = colorMenu.value?.target === target ? null : { anchor: event.currentTarget, target }
}
function pickColor(color) {
  colorMenu.value.target.color = color
  colorMenu.value = null
}
const colorOf = (value) => resolveColor(value).color

onMounted(() => directory.load().catch(() => {}))

const usage = computed(() => {
  const counts = {}
  for (const item of editor.itemList) if (item.assigneeKey) counts[item.assigneeKey] = (counts[item.assigneeKey] || 0) + 1
  return counts
})

function pickUser(target) {
  const user = directory.byId[target.userId]
  if (user && !target.name.trim()) target.name = user.name
}
function add() {
  if (!form.value.name.trim()) return
  editor.createAssignee({ name: form.value.name.trim(), role: form.value.role.trim(), color: form.value.color, userId: form.value.userId || null })
  form.value = { name: '', role: '', color: DEFAULT_COLOR, userId: null }
}
function startEdit(a) {
  editing.value = { key: a.key, name: a.name, role: a.role, color: a.color, userId: a.userId }
}
function saveEdit() {
  const e = editing.value
  if (!e.name.trim()) return
  editor.updateAssignee(e.key, { name: e.name.trim(), role: e.role.trim(), color: e.color, userId: e.userId || null })
  editing.value = null
}
async function remove(a) {
  const n = usage.value[a.key] || 0
  const ok = await confirm({
    title: `Удалить исполнителя «${a.name}»?`,
    message: n ? `Он назначен на ${countLabel(n, 'строку', 'строки', 'строк')} — у них исполнитель будет снят. Отменить можно через Ctrl+Z.` : 'Отменить удаление можно через Ctrl+Z.',
    confirmText: 'Удалить',
  })
  if (ok) editor.deleteAssignee(a.key)
}
</script>

<template>
  <BaseModal title="Исполнители плана" size="wide" @close="emit('close')">
    <template #header-actions><HelpLink topic="plan-assignees" /></template>
    <div class="list">
      <div v-for="a in editor.assigneeList" :key="a.key" class="row">
        <template v-if="editing?.key === a.key">
          <button class="color" type="button" :style="{ background: colorOf(editing.color) }" title="Цвет" aria-label="Цвет исполнителя" @click="openColor($event, editing)" />
          <input v-model="editing.name" class="input input-sm" placeholder="ФИО" />
          <input v-model="editing.role" class="input input-sm" placeholder="Роль" />
          <select v-model="editing.userId" class="select input-sm" @change="pickUser(editing)">
            <option :value="null">— не связан —</option>
            <option v-for="u in directory.users" :key="u.id" :value="u.id">{{ u.name }}</option>
          </select>
          <button class="btn btn-sm btn-primary" type="button" @click="saveEdit">Сохранить</button>
          <button class="btn btn-sm btn-ghost" type="button" @click="editing = null">Отмена</button>
        </template>
        <template v-else>
          <UserAvatar v-if="a.userId && directory.byId[a.userId]" :user="directory.byId[a.userId]" :color="colorOf(a.color)" :size="24" />
          <span v-else class="dot" :style="{ background: colorOf(a.color) }" />
          <div class="who">
            <div class="name">{{ a.name }}</div>
            <div class="muted small">{{ [a.role, a.userId && directory.byId[a.userId] ? 'сотрудник системы' : ''].filter(Boolean).join(' · ') || '—' }}</div>
          </div>
          <span class="muted small">{{ countLabel(usage[a.key] || 0, 'строка', 'строки', 'строк') }}</span>
          <button v-if="!readonly" class="btn btn-ghost btn-icon btn-sm" type="button" title="Изменить" @click="startEdit(a)"><AppIcon name="edit" :size="14" /></button>
          <button v-if="!readonly" class="btn btn-ghost btn-icon btn-sm btn-text-danger" type="button" title="Удалить" @click="remove(a)"><AppIcon name="trash" :size="14" /></button>
        </template>
      </div>
      <p v-if="!editor.assigneeList.length" class="muted">Исполнителей пока нет.</p>
    </div>
    <form v-if="!readonly" class="add" @submit.prevent="add">
      <button class="color" type="button" :style="{ background: colorOf(form.color) }" title="Цвет" aria-label="Цвет нового исполнителя" @click="openColor($event, form)" />
      <input v-model="form.name" class="input" placeholder="ФИО исполнителя" maxlength="500" />
      <input v-model="form.role" class="input" placeholder="Роль (необязательно)" maxlength="500" />
      <select v-model="form.userId" class="select" title="Связать с сотрудником системы" @change="pickUser(form)">
        <option :value="null">Сотрудник системы…</option>
        <option v-for="u in directory.users" :key="u.id" :value="u.id">{{ u.name }}</option>
      </select>
      <button class="btn btn-primary" type="submit" :disabled="!form.name.trim()">Добавить</button>
    </form>
    <PopupMenu v-if="colorMenu" :anchor="colorMenu.anchor" :min-width="190" @close="colorMenu = null">
      <div class="menu-label">Цвет исполнителя</div>
      <div class="color-menu"><ColorPicker :model-value="colorMenu.target.color" @update:model-value="pickColor" /></div>
    </PopupMenu>
    <template #footer><button class="btn btn-primary" type="button" @click="emit('close')">Готово</button></template>
  </BaseModal>
</template>

<style scoped>
.list { display: flex; flex-direction: column; gap: 6px; margin-bottom: 14px; }
.row { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border: 1px solid var(--color-border); border-radius: var(--radius-md); }
.dot { width: 24px; height: 24px; border-radius: 50%; flex-shrink: 0; box-shadow: inset 0 0 0 7px #fff; }
.who { flex: 1; min-width: 0; }
.name { font-weight: 600; font-size: 13px; }
.color { width: 34px; height: 30px; padding: 0; border: 3px solid #fff; box-shadow: 0 0 0 1px var(--color-border-strong); border-radius: 8px; flex-shrink: 0; }
.color-menu { padding: 4px 10px 10px; max-width: 190px; }
.add { display: grid; grid-template-columns: 34px 1.4fr 1fr 1fr auto; gap: 8px; align-items: center; }
.row .input, .row .select { flex: 1; }
@media (max-width: 768px) { .add { grid-template-columns: 34px 1fr; } }
</style>
