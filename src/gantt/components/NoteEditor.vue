<script setup>
import { computed, inject, ref } from 'vue'
import BaseModal from '../../components/common/BaseModal.vue'
import ToggleSwitch from '../../components/common/ToggleSwitch.vue'
import ColorPicker from './ColorPicker.vue'
import { useDialogs } from '../../composables/useDialogs'

/** Пометка на шкале: создание и правка (в 1.0.x текст пометки вводился в системном окне). */
const props = defineProps({
  noteKey: { type: String, default: null },
  date: { type: String, default: null },
  rowKey: { type: String, default: null },
})
const emit = defineEmits(['close'])
const editor = inject('gantt')
const { confirm } = useDialogs()
const existing = computed(() => (props.noteKey ? editor.source.notes[props.noteKey] : null))
const form = ref({
  date: existing.value?.date || props.date,
  text: existing.value?.text || '',
  rowKey: existing.value ? existing.value.rowKey : props.rowKey,
  color: existing.value?.color || 'orange',
  showDate: existing.value?.showDate || false,
})
const readonly = computed(() => !editor.canEdit)
const rows = computed(() => editor.visibleRows.map((r) => ({ key: r.item.key, name: r.item.name })))
// Длинный план — над списком строк поле поиска (выбранная строка остаётся в списке)
const rowQuery = ref('')
const shownRows = computed(() => {
  const q = rowQuery.value.trim().toLowerCase()
  return q ? rows.value.filter((r) => r.key === form.value.rowKey || r.name.toLowerCase().includes(q)) : rows.value
})

function save() {
  if (!form.value.date || !form.value.text.trim()) return
  const data = { date: form.value.date, text: form.value.text.trim(), rowKey: form.value.rowKey || null, color: form.value.color, showDate: form.value.showDate }
  if (existing.value) editor.updateNote(existing.value.key, data)
  else editor.createNote(data)
  emit('close')
}
async function remove() {
  if (!await confirm({ title: 'Удалить пометку?', message: existing.value.text, confirmText: 'Удалить' })) return
  editor.deleteNote(existing.value.key)
  emit('close')
}
</script>

<template>
  <BaseModal :title="existing ? 'Пометка' : 'Новая пометка'" @close="emit('close')">
    <form class="form-stack" @submit.prevent="save">
      <div class="form-grid">
        <label class="field"><span class="field-label">Дата</span><input v-model="form.date" class="input" type="date" :disabled="readonly" /></label>
        <div class="field">
          <span class="field-label">Привязка</span>
          <input v-if="!readonly && rows.length > 25" v-model="rowQuery" class="input input-sm" type="search" placeholder="Найти строку…" aria-label="Поиск строки" />
          <select v-model="form.rowKey" class="select" :disabled="readonly" aria-label="Привязка">
            <option :value="null">Ко всему графику (над шкалой)</option>
            <option v-for="row in shownRows" :key="row.key" :value="row.key">{{ row.name }}</option>
          </select>
        </div>
      </div>
      <label class="field"><span class="field-label">Текст</span><textarea v-model="form.text" class="textarea" rows="3" maxlength="5000" :readonly="readonly" placeholder="Что отметить на шкале" /></label>
      <div class="field"><span class="field-label">Цвет</span><ColorPicker v-model="form.color" :disabled="readonly" /></div>
      <div class="toggle-box"><ToggleSwitch v-model="form.showDate" label="Показывать дату рядом с текстом" :disabled="readonly" /></div>
    </form>
    <template #footer>
      <button v-if="existing && !readonly" class="btn btn-ghost btn-text-danger" type="button" style="margin-right:auto" @click="remove">Удалить</button>
      <button class="btn btn-ghost" type="button" @click="emit('close')">{{ readonly ? 'Закрыть' : 'Отмена' }}</button>
      <button v-if="!readonly" class="btn btn-primary" type="button" :disabled="!form.date || !form.text.trim()" @click="save">Сохранить</button>
    </template>
  </BaseModal>
</template>

<style scoped>
.toggle-box { border: 1px solid var(--color-border); border-radius: 10px; padding: 2px 4px; background: var(--color-surface-2); }
</style>
