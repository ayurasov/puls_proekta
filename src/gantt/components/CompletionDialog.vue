<script setup>
import { ref } from 'vue'
import BaseModal from '../../components/common/BaseModal.vue'
import { formatDay } from '../domain/dates'

/** Дата фактического завершения задачи (в 1.0.x — системное окно prompt «ГГГГ-ММ-ДД»). */
const props = defineProps({ item: { type: Object, required: true }, today: { type: String, required: true } })
const emit = defineEmits(['confirm', 'cancel'])
const date = ref(props.today < props.item.start ? props.item.start : props.today)
</script>

<template>
  <BaseModal title="Задача завершена" @close="emit('cancel')">
    <form class="form-stack" @submit.prevent="date && emit('confirm', date)">
      <p class="muted" style="margin:0">«{{ item.name }}» — плановое окончание {{ formatDay(item.end) }}. Когда задача фактически завершена?</p>
      <label class="field">
        <span class="field-label">Дата завершения</span>
        <input v-model="date" class="input" type="date" :min="item.start" />
      </label>
      <div class="quick">
        <button class="btn btn-sm" type="button" @click="date = today">Сегодня</button>
        <button class="btn btn-sm" type="button" @click="date = item.end">В плановый срок</button>
      </div>
      <button type="submit" class="sr-only" tabindex="-1">ok</button>
    </form>
    <template #footer>
      <button class="btn btn-ghost" type="button" @click="emit('cancel')">Отмена</button>
      <button class="btn btn-primary" type="button" :disabled="!date" @click="emit('confirm', date)">Завершить</button>
    </template>
  </BaseModal>
</template>

<style scoped>
.quick { display: flex; gap: 6px; }
</style>
