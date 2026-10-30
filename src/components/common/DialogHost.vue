<script setup>
import { computed, ref, watch } from 'vue'
import { useUiStore } from '../../stores/ui'
import BaseModal from './BaseModal.vue'

const ui = useUiStore()
const dialog = computed(() => ui.dialogs[ui.dialogs.length - 1] || null)
const value = ref('')
const error = ref('')

watch(dialog, (d) => {
  value.value = d?.value ?? ''
  error.value = ''
}, { immediate: true })

function cancel() {
  if (!dialog.value) return
  ui.closeDialog(dialog.value.id, ['prompt', 'choice'].includes(dialog.value.type) ? null : false)
}

function choose(value) {
  if (dialog.value) ui.closeDialog(dialog.value.id, value)
}

function submit() {
  const d = dialog.value
  if (!d) return
  if (d.type === 'prompt') {
    const text = String(value.value ?? '').trim()
    if (d.required !== false && !text) { error.value = d.requiredMessage || 'Заполните поле'; return }
    const problem = d.validate?.(text)
    if (problem) { error.value = problem; return }
    ui.closeDialog(d.id, text)
    return
  }
  ui.closeDialog(d.id, true)
}
</script>

<template>
  <BaseModal v-if="dialog" :key="dialog.id" :title="dialog.title" size="md" @close="cancel">
    <form class="dialog-body" @submit.prevent="submit">
      <p v-if="dialog.message" class="dialog-message">{{ dialog.message }}</p>
      <ul v-if="dialog.details?.length" class="dialog-details">
        <li v-for="line in dialog.details" :key="line">{{ line }}</li>
      </ul>
      <label v-if="dialog.type === 'prompt'" class="field">
        <span v-if="dialog.label" class="field-label">{{ dialog.label }}</span>
        <textarea v-if="dialog.multiline" v-model="value" class="textarea" :placeholder="dialog.placeholder" rows="4" />
        <input v-else v-model="value" class="input" :type="dialog.inputType || 'text'" :placeholder="dialog.placeholder" :class="{ 'is-invalid': error }" />
        <span v-if="error" class="field-error">{{ error }}</span>
        <span v-else-if="dialog.hint" class="field-hint">{{ dialog.hint }}</span>
      </label>
      <button type="submit" class="sr-only" tabindex="-1">ok</button>
    </form>
    <template #footer>
      <button v-if="dialog.type !== 'alert'" class="btn btn-ghost" type="button" @click="cancel">{{ dialog.cancelText || 'Отмена' }}</button>
      <template v-if="dialog.type === 'choice'">
        <button
          v-for="choice in dialog.choices" :key="choice.value" class="btn" :class="choice.danger ? 'btn-danger' : 'btn-primary'"
          type="button" @click="choose(choice.value)"
        >{{ choice.label }}</button>
      </template>
      <button v-else class="btn" :class="dialog.type === 'confirm' && dialog.danger ? 'btn-danger' : 'btn-primary'" type="button" @click="submit">
        {{ dialog.confirmText }}
      </button>
    </template>
  </BaseModal>
</template>

<style scoped>
.dialog-body { display: flex; flex-direction: column; gap: 12px; }
.dialog-message { margin: 0; color: var(--color-text-secondary); line-height: 1.5; white-space: pre-line; }
.dialog-details { margin: 0; padding-left: 18px; color: var(--color-text-secondary); font-size: 13px; }
</style>
