<script setup>
import { computed, ref } from 'vue'
import BaseModal from '../../components/common/BaseModal.vue'
import { formatDay } from '../domain/dates'

/** Дата фактического завершения задачи (в 1.0.x — системное окно prompt «ГГГГ-ММ-ДД»). */
const props = defineProps({ item: { type: Object, required: true }, today: { type: String, required: true } })
const emit = defineEmits(['confirm', 'cancel'])
const date = ref(props.today < props.item.start ? props.item.start : props.today)
// Задача не может завершиться раньше, чем началась: «Сегодня» недоступно для задачи из будущего
const todayBeforeStart = computed(() => props.today < props.item.start)
const tooEarly = computed(() => Boolean(date.value) && date.value < props.item.start)
function confirm() {
  if (date.value && !tooEarly.value) emit('confirm', date.value)
}
</script>

<template>
  <BaseModal title="Задача завершена" @close="emit('cancel')">
    <form class="form-stack" @submit.prevent="confirm">
      <p class="muted" style="margin:0">«{{ item.name }}» — плановое окончание {{ formatDay(item.end) }}. Когда задача фактически завершена?</p>
      <label class="field">
        <span class="field-label">Дата завершения</span>
        <input v-model="date" class="input" type="date" :min="item.start" :class="{ 'is-invalid': tooEarly }" />
        <span v-if="tooEarly" class="field-error">Не раньше начала задачи — {{ formatDay(item.start) }}</span>
      </label>
      <div class="quick">
        <button class="btn btn-sm" type="button" :disabled="todayBeforeStart" :title="todayBeforeStart ? `Задача начинается ${formatDay(item.start)} — позже сегодняшнего дня` : ''" @click="date = today">Сегодня</button>
        <button class="btn btn-sm" type="button" @click="date = item.end">В плановый срок</button>
      </div>
      <button type="submit" class="sr-only" tabindex="-1">ok</button>
    </form>
    <template #footer>
      <button class="btn btn-ghost" type="button" @click="emit('cancel')">Отмена</button>
      <button class="btn btn-primary" type="button" :disabled="!date || tooEarly" @click="confirm">Завершить</button>
    </template>
  </BaseModal>
</template>

<style scoped>
.quick { display: flex; gap: 6px; }
</style>
