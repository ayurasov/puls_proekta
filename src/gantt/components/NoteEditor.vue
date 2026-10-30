<script setup>
import { computed, inject, ref } from 'vue'
import BaseModal from '../../components/common/BaseModal.vue'
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
const rows = computed(() => editor.itemList.map((i) => ({ key: i.key, name: i.name })))

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
        <label class="field">
          <span class="field-label">Привязка</span>
          <select v-model="form.rowKey" class="select" :disabled="readonly">
            <option :value="null">Ко всему графику (над шкалой)</option>
            <option v-for="row in rows" :key="row.key" :value="row.key">{{ row.name }}</option>
          </select>
        </label>
      </div>
      <label class="field"><span class="field-label">Текст</span><textarea v-model="form.text" class="textarea" rows="3" maxlength="5000" :readonly="readonly" /></label>
      <div class="field"><span class="field-label">Цвет</span><ColorPicker v-model="form.color" :disabled="readonly" /></div>
      <label class="checkbox"><input v-model="form.showDate" type="checkbox" :disabled="readonly" /> Показывать дату рядом с текстом</label>
    </form>
    <template #footer>
      <button v-if="existing && !readonly" class="btn btn-ghost btn-text-danger" type="button" style="margin-right:auto" @click="remove">Удалить</button>
      <button class="btn btn-ghost" type="button" @click="emit('close')">{{ readonly ? 'Закрыть' : 'Отмена' }}</button>
      <button v-if="!readonly" class="btn btn-primary" type="button" :disabled="!form.date || !form.text.trim()" @click="save">Сохранить</button>
    </template>
  </BaseModal>
</template>
