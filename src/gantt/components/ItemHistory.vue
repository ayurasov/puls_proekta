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
    <div v-if="!loading && !events.length" class="muted small empty">
      <span class="empty-icon"><AppIcon name="history" :size="18" /></span>
      Изменений пока нет
    </div>
    <ol v-if="events.length" class="timeline">
      <li v-for="event in events" :key="event.id" class="event">
        <span class="icon" :class="`a-${event.action}`"><AppIcon :name="ICONS[event.action] || 'edit'" :size="13" /></span>
        <div class="event-main">
          <div class="summary">{{ event.summary }}</div>
          <div class="muted small">
            {{ event.userName || 'система' }} · <span :title="formatDateTime(event.createdAt)">{{ formatRelative(event.createdAt) }}</span>
            <span v-if="event.action === 'legacy'" class="badge" title="Запись перенесена из прежней версии «Пульса Проекта»">до переноса</span>
          </div>
        </div>
      </li>
    </ol>
    <button v-if="nextBefore" class="btn btn-sm more" type="button" :disabled="loading" @click="load(true)">Показать ещё</button>
  </div>
</template>

<style scoped>
.history { display: flex; flex-direction: column; gap: 10px; }
.empty { display: flex; flex-direction: column; align-items: center; gap: 8px; text-align: center; padding: 28px 0 12px; font-size: 13px; }
.empty-icon { width: 40px; height: 40px; border-radius: 12px; background: #eef1f7; display: inline-flex; align-items: center; justify-content: center; }
/* Лента событий: значки на вертикальной линии */
.timeline { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.event { position: relative; display: flex; gap: 12px; padding-bottom: 16px; }
.event:not(:last-child)::before { content: ''; position: absolute; left: 13px; top: 28px; bottom: 2px; width: 2px; border-radius: 1px; background: var(--color-border); }
.icon { width: 28px; height: 28px; border-radius: 50%; background: #eef1f7; color: var(--color-text-muted); display: flex; align-items: center; justify-content: center; flex-shrink: 0; box-shadow: 0 0 0 3px var(--color-surface); }
.icon.a-create { background: var(--color-success-soft); color: #1f7a4c; }
.icon.a-delete { background: var(--color-danger-soft); color: #c62933; }
.icon.a-comment { background: var(--color-primary-soft); color: var(--color-primary-dark); }
.icon.a-restore { background: var(--color-warning-soft); color: #a8670f; }
.event-main { min-width: 0; display: flex; flex-direction: column; gap: 3px; padding-top: 4px; }
.summary { font-size: 13px; line-height: 1.45; overflow-wrap: anywhere; }
.badge { margin-left: 6px; font-size: 10px; padding: 0 6px; }
.more { align-self: center; }
</style>
