<script setup>
import { computed, inject, nextTick, onMounted, ref } from 'vue'
import BaseModal from '../../components/common/BaseModal.vue'
import AppIcon from '../../components/common/AppIcon.vue'
import HelpLink from '../../components/common/HelpLink.vue'
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
// Телефон: форма нового исполнителя свёрнута в кнопку, чтобы сначала был виден список
// (и клавиатура не открывалась сама); на компьютере форма открыта сразу
const isPhone = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(max-width: 768px)').matches : false
const formOpen = ref(!isPhone)
const nameInput = ref(null)
async function openForm() {
  formOpen.value = true
  await nextTick()
  nameInput.value?.focus()
}
const colorOf = (value) => resolveColor(value).color
/** Аватар исполнителя: связанный сотрудник (фото, полное имя) или инициалы ФИО — на цвете исполнителя. */
function avatarOf(target) {
  return (target.userId && directory.byId[target.userId]) || { name: target.name?.trim() || '?' }
}

onMounted(() => directory.load().catch(() => {}))

const usage = computed(() => {
  const counts = {}
  for (const item of editor.itemList) if (item.assigneeKey) counts[item.assigneeKey] = (counts[item.assigneeKey] || 0) + 1
  return counts
})
const assignedRows = computed(() => Object.values(usage.value).reduce((sum, n) => sum + n, 0))

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
    <div class="assignees">
      <!-- Новый исполнитель -->
      <button v-if="!readonly && !formOpen" class="add-toggle" type="button" @click="openForm"><AppIcon name="plus" :size="15" /> Новый исполнитель</button>
      <form v-if="!readonly && formOpen" class="add-card" @submit.prevent="add">
        <header class="card-head">
          <span class="head-icon"><AppIcon name="plus" :size="15" /></span>
          <div class="head-titles">
            <h3>Новый исполнитель</h3>
            <p>Свяжите с сотрудником системы, чтобы видеть его фото. Заказчика и подрядчиков добавляйте без связи.</p>
          </div>
        </header>
        <div class="form-fields">
          <label class="field"><span class="field-label">ФИО</span><input ref="nameInput" v-model="form.name" class="input" placeholder="ФИО исполнителя" maxlength="500" /></label>
          <label class="field"><span class="field-label">Роль</span><input v-model="form.role" class="input" placeholder="Роль (необязательно)" maxlength="500" /></label>
          <label class="field">
            <span class="field-label">Сотрудник системы</span>
            <select v-model="form.userId" class="select" title="Связать с сотрудником системы" @change="pickUser(form)">
              <option :value="null">Сотрудник системы…</option>
              <option v-for="u in directory.users" :key="u.id" :value="u.id">{{ u.name }}</option>
            </select>
          </label>
        </div>
        <div class="form-foot">
          <div class="field">
            <span class="field-label">Цвет</span>
            <ColorPicker v-model="form.color" aria-label="Цвет нового исполнителя" />
          </div>
          <div class="foot-end">
            <span class="chip-preview" :class="{ 'is-empty': !form.name.trim() }" :style="{ '--c': colorOf(form.color) }" title="Так исполнитель будет выглядеть в плане">
              <UserAvatar :user="avatarOf(form)" :color="colorOf(form.color)" :size="24" />
              <span class="chip-name">{{ form.name.trim() || 'ФИО исполнителя' }}</span>
              <span v-if="form.role.trim()" class="chip-role">{{ form.role.trim() }}</span>
            </span>
            <button class="btn btn-primary" type="submit" :disabled="!form.name.trim()"><AppIcon name="plus" :size="14" /> Добавить</button>
          </div>
        </div>
      </form>

      <!-- Список -->
      <div class="list-head">
        <span class="section-title">В плане</span>
        <span v-if="editor.assigneeList.length" class="muted small">{{ countLabel(editor.assigneeList.length, 'исполнитель', 'исполнителя', 'исполнителей') }} · назначено на {{ countLabel(assignedRows, 'строку', 'строки', 'строк') }}</span>
      </div>
      <ul v-if="editor.assigneeList.length" class="list">
        <li v-for="a in editor.assigneeList" :key="a.key" class="row" :class="{ 'is-editing': editing?.key === a.key }">
          <form v-if="editing?.key === a.key" class="edit" @submit.prevent="saveEdit">
            <div class="form-fields">
              <label class="field"><span class="field-label">ФИО</span><input v-model="editing.name" class="input" placeholder="ФИО" /></label>
              <label class="field"><span class="field-label">Роль</span><input v-model="editing.role" class="input" placeholder="Роль" /></label>
              <label class="field">
                <span class="field-label">Сотрудник системы</span>
                <select v-model="editing.userId" class="select" @change="pickUser(editing)">
                  <option :value="null">— не связан —</option>
                  <option v-for="u in directory.users" :key="u.id" :value="u.id">{{ u.name }}</option>
                </select>
              </label>
            </div>
            <div class="form-foot">
              <div class="field">
                <span class="field-label">Цвет</span>
                <ColorPicker v-model="editing.color" aria-label="Цвет исполнителя" />
              </div>
              <div class="foot-end">
                <span class="chip-preview" :style="{ '--c': colorOf(editing.color) }" title="Так исполнитель будет выглядеть в плане">
                  <UserAvatar :user="avatarOf(editing)" :color="colorOf(editing.color)" :size="24" />
                  <span class="chip-name">{{ editing.name.trim() || 'ФИО' }}</span>
                  <span v-if="editing.role.trim()" class="chip-role">{{ editing.role.trim() }}</span>
                </span>
                <button class="btn btn-ghost" type="button" @click="editing = null">Отмена</button>
                <button class="btn btn-primary" type="submit">Сохранить</button>
              </div>
            </div>
          </form>
          <template v-else>
            <span class="avatar-wrap">
              <UserAvatar :user="avatarOf(a)" :color="colorOf(a.color)" :size="34" />
              <span class="color-dot" :style="{ background: colorOf(a.color) }" />
            </span>
            <div class="who">
              <div class="name">{{ a.name }}</div>
              <div class="meta">
                <span v-if="a.role" class="role">{{ a.role }}</span>
                <span v-if="a.userId && directory.byId[a.userId]" class="linked" title="Связан с сотрудником системы"><AppIcon name="user" :size="11" /><span class="linked-text">сотрудник системы</span></span>
                <span v-if="!a.role && !(a.userId && directory.byId[a.userId])" class="muted">—</span>
                <span class="usage usage-inline">{{ countLabel(usage[a.key] || 0, 'строка', 'строки', 'строк') }}</span>
              </div>
            </div>
            <span class="usage usage-side" :class="{ 'is-zero': !usage[a.key] }">{{ countLabel(usage[a.key] || 0, 'строка', 'строки', 'строк') }}</span>
            <div v-if="!readonly" class="row-actions">
              <button class="btn btn-ghost btn-icon btn-sm" type="button" title="Изменить" :aria-label="`Изменить исполнителя «${a.name}»`" @click="startEdit(a)"><AppIcon name="edit" :size="15" /></button>
              <button class="btn btn-ghost btn-icon btn-sm btn-text-danger" type="button" title="Удалить" :aria-label="`Удалить исполнителя «${a.name}»`" @click="remove(a)"><AppIcon name="trash" :size="15" /></button>
            </div>
          </template>
        </li>
      </ul>
      <div v-else class="empty">
        <span class="empty-icon"><AppIcon name="users" :size="20" /></span>
        <p>Исполнителей пока нет.</p>
      </div>
    </div>
    <template #footer><button class="btn btn-primary" type="button" @click="emit('close')">Готово</button></template>
  </BaseModal>
</template>

<style scoped>
.assignees { display: flex; flex-direction: column; gap: 12px; }

/* Форма нового исполнителя — мягкая карточка; на телефоне сначала кнопка */
.add-toggle { display: flex; align-items: center; justify-content: center; gap: 8px; min-height: 48px; border: 1px dashed var(--color-border-strong); border-radius: 12px; background: var(--color-surface-2); color: var(--color-primary-dark); font-size: 14px; font-weight: 600; transition: background 0.15s, border-color 0.15s; }
.add-toggle:hover { background: var(--color-primary-soft); border-color: #c9d6ff; }
.add-card { display: flex; flex-direction: column; gap: 14px; padding: 16px; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-surface-2); }
.card-head { display: flex; align-items: flex-start; gap: 12px; }
.head-icon { width: 30px; height: 30px; border-radius: 9px; flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center; background: var(--color-primary-soft); color: var(--color-primary-dark); }
.head-titles { min-width: 0; }
.head-titles h3 { font-size: 13.5px; font-weight: 650; line-height: 1.3; }
.head-titles p { margin: 2px 0 0; font-size: 12px; color: var(--color-text-muted); line-height: 1.4; }
.form-fields { display: grid; grid-template-columns: 1.4fr 1fr 1fr; gap: 12px; }
.form-foot { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px 16px; flex-wrap: wrap; }
.foot-end { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; margin-left: auto; }

/* Как исполнитель выглядит в плане: аватар и имя на оттенке его цвета */
.chip-preview { display: inline-flex; align-items: center; gap: 7px; max-width: 280px; min-height: 32px; padding: 3px 12px 3px 4px; border-radius: 999px; background: color-mix(in srgb, var(--c) 11%, white); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--c) 35%, white); font-size: 12.5px; transition: background 0.15s, box-shadow 0.15s; }
.chip-preview.is-empty .chip-name { color: var(--color-text-muted); font-weight: 500; }
.chip-name { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.chip-role { color: var(--color-text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.chip-role::before { content: '·'; margin-right: 6px; }

/* Список */
.list-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-top: 4px; padding: 0 2px; }
.list { list-style: none; margin: 0; padding: 0; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-surface); overflow: hidden; }
.row { display: flex; align-items: center; gap: 12px; min-height: 60px; padding: 10px 12px 10px 14px; transition: background 0.15s; }
.row + .row { border-top: 1px solid #eef0f5; }
.row:not(.is-editing):hover { background: #fafbfe; }
.row.is-editing { display: block; padding: 16px; background: #f6f8ff; box-shadow: inset 3px 0 0 var(--color-primary); }
.avatar-wrap { position: relative; display: inline-flex; flex-shrink: 0; }
.color-dot { position: absolute; right: -2px; bottom: -2px; width: 12px; height: 12px; border-radius: 50%; border: 2px solid var(--color-surface); }
.who { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.name { font-weight: 600; font-size: 13.5px; overflow-wrap: anywhere; }
.meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 12px; color: var(--color-text-secondary); }
.linked { display: inline-flex; align-items: center; gap: 4px; min-height: 20px; padding: 1px 8px 1px 6px; border-radius: 999px; background: var(--color-primary-soft); color: var(--color-primary-dark); font-size: 11px; font-weight: 600; }
.usage { display: inline-flex; align-items: center; height: 24px; padding: 0 10px; border-radius: 999px; background: #eef1f7; color: var(--color-text-secondary); font-size: 12px; font-weight: 600; white-space: nowrap; flex-shrink: 0; }
.usage.is-zero { color: var(--color-text-muted); background: transparent; box-shadow: inset 0 0 0 1px var(--color-border); }
.usage-inline { display: none; }
.row-actions { display: flex; gap: 2px; flex-shrink: 0; }
.edit { display: flex; flex-direction: column; gap: 14px; }

.empty { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 28px 16px; border: 1px dashed var(--color-border-strong); border-radius: 12px; color: var(--color-text-muted); text-align: center; }
.empty p { margin: 0; font-size: 13px; }
.empty-icon { width: 40px; height: 40px; border-radius: 12px; background: #eef1f7; display: inline-flex; align-items: center; justify-content: center; }

@media (max-width: 768px) {
  .add-card { padding: 14px; }
  .form-fields { grid-template-columns: 1fr; }
  .foot-end { width: 100%; justify-content: stretch; }
  .foot-end .chip-preview { max-width: 100%; flex: 1 1 100%; }
  .foot-end .btn { flex: 1; }
  .row { padding: 10px 6px 10px 12px; gap: 10px; }
  .row.is-editing { padding: 14px; }
  .usage-side { display: none; }
  /* Телефон: «сотрудник системы» — значком, чтобы роль и число строк уместились в одну строку */
  .linked { padding: 0; width: 20px; justify-content: center; }
  .linked-text { display: none; }
  .usage-inline { display: inline-flex; height: 20px; padding: 0 8px; font-size: 11px; }
}
</style>
