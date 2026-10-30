<script setup>
import { inject, onMounted, ref, watch } from 'vue'
import { api } from '../../api/client'
import { formatDateTime, formatRelative } from '../../utils/format'
import AppIcon from '../../components/common/AppIcon.vue'

const props = defineProps({ item: { type: Object, required: true } })
const editor = inject('gantt')
const events = ref([])
const loading = ref(true)
const nextBefore = ref(null)
const ICONS = { create: 'plus', update: 'edit', delete: 'trash', restore: 'undo', comment: 'message', legacy: 'history' }

async function load(more = false) {
  loading.value = true
  try {
    const res = await api.get(`/plans/${editor.state.planId}/events`, { entityKey: props.item.key, before: more ? nextBefore.value : null, limit: 50 })
    events.value = more ? [...events.value, ...res.events] : res.events
    nextBefore.value = res.hasMore ? res.nextBefore : null
  } finally { loading.value = false }
}
onMounted(() => load())
watch(() => props.item.updatedRev, () => load())
</script>

<template>
  <div class="history">
    <div v-if="!loading && !events.length" class="muted small empty">Изменений пока нет</div>
    <div v-for="event in events" :key="event.id" class="event">
      <span class="icon" :class="`a-${event.action}`"><AppIcon :name="ICONS[event.action] || 'edit'" :size="13" /></span>
      <div class="event-main">
        <div class="summary">{{ event.summary }}</div>
        <div class="muted small">
          {{ event.userName || 'система' }} · <span :title="formatDateTime(event.createdAt)">{{ formatRelative(event.createdAt) }}</span>
          <span v-if="event.action === 'legacy'" class="badge" title="Запись перенесена из прежней версии «Пульса Проекта»">до переноса</span>
        </div>
      </div>
    </div>
    <button v-if="nextBefore" class="btn btn-sm btn-ghost" type="button" :disabled="loading" @click="load(true)">Показать ещё</button>
  </div>
</template>

<style scoped>
.history { display: flex; flex-direction: column; gap: 10px; }
.empty { text-align: center; padding: 20px 0; }
.event { display: flex; gap: 10px; }
.icon { width: 24px; height: 24px; border-radius: 50%; background: #eef1f7; color: var(--color-text-muted); display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.icon.a-create { background: var(--color-success-soft); color: #1f7a4c; }
.icon.a-delete { background: var(--color-danger-soft); color: #c62933; }
.icon.a-comment { background: var(--color-primary-soft); color: var(--color-primary-dark); }
.event-main { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.summary { font-size: 12.5px; line-height: 1.4; overflow-wrap: anywhere; }
.badge { margin-left: 6px; font-size: 10px; padding: 0 6px; }
</style>
