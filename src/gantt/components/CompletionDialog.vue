<script setup>
import { computed, ref } from 'vue'
import BaseModal from '../../components/common/BaseModal.vue'
import AppIcon from '../../components/common/AppIcon.vue'
import { diffDays, formatDay } from '../domain/dates'

/** Дата фактического завершения задачи (в 1.0.x — системное окно prompt «ГГГГ-ММ-ДД»). */
const props = defineProps({ item: { type: Object, required: true }, today: { type: String, required: true } })
const emit = defineEmits(['confirm', 'cancel'])
const date = ref(props.today < props.item.start ? props.item.start : props.today)
// Задача не может завершиться раньше, чем началась: «Сегодня» недоступно для задачи из будущего
const todayBeforeStart = computed(() => props.today < props.item.start)
const tooEarly = computed(() => Boolean(date.value) && date.value < props.item.start)
// Сдвиг, который получит задача: от планового окончания до даты завершения
const offset = computed(() => (date.value && !tooEarly.value ? diffDays(props.item.end, date.value) : null))
function confirm() {
  if (date.value && !tooEarly.value) emit('confirm', date.value)
}
</script>

<template>
  <BaseModal title="Задача завершена" @close="emit('cancel')">
    <form class="form-stack" @submit.prevent="confirm">
      <div class="task-card">
        <span class="task-icon"><AppIcon name="calendarCheck" :size="16" /></span>
        <div class="task-text">
          <div class="task-name">«{{ item.name }}»</div>
          <div class="muted small">Плановое окончание {{ formatDay(item.end) }}</div>
        </div>
      </div>
      <p class="question">Когда задача фактически завершена?</p>
      <label class="field">
        <span class="field-label">Дата завершения</span>
        <input v-model="date" class="input" type="date" :min="item.start" :class="{ 'is-invalid': tooEarly }" />
        <span v-if="tooEarly" class="field-error">Не раньше начала задачи — {{ formatDay(item.start) }}</span>
      </label>
      <div class="quick">
        <button class="btn btn-sm" type="button" :disabled="todayBeforeStart" :title="todayBeforeStart ? `Задача начинается ${formatDay(item.start)} — позже сегодняшнего дня` : ''" @click="date = today">Сегодня</button>
        <button class="btn btn-sm" type="button" @click="date = item.end">В плановый срок</button>
        <span v-if="offset !== null" class="offset" :class="offset > 0 ? 'is-late' : offset < 0 ? 'is-early' : 'is-ontime'">
          {{ offset > 0 ? `просрочка +${offset} дн.` : offset < 0 ? `раньше срока на ${-offset} дн.` : 'в срок' }}
        </span>
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
.task-card { display: flex; align-items: center; gap: 12px; padding: 12px 14px; border-radius: 12px; background: var(--color-success-soft); border: 1px solid #c4ead5; }
.task-icon { width: 32px; height: 32px; border-radius: 10px; flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center; background: var(--color-surface); color: #1f7a4c; }
.task-text { min-width: 0; }
.task-name { font-weight: 650; overflow-wrap: anywhere; }
.question { margin: 0; color: var(--color-text-secondary); }
.quick { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.offset { margin-left: auto; display: inline-flex; align-items: center; min-height: 24px; padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.offset.is-late { background: var(--color-danger-soft); color: #c62933; }
.offset.is-early, .offset.is-ontime { background: var(--color-success-soft); color: #1f7a4c; }
</style>
