<script setup>
import { computed, inject, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import BaseModal from '../../components/common/BaseModal.vue'
import AppIcon from '../../components/common/AppIcon.vue'
import { useUiStore } from '../../stores/ui'
import { useDialogs } from '../../composables/useDialogs'
import { formatDateTime, formatRelative } from '../../utils/format'

/**
 * История плана: журнал изменений (кто, когда, что было → стало) и сохранённые версии
 * (автоматические снимки, ручные версии, состояние на момент переноса из 1.0.1).
 */
const props = defineProps({ planId: { type: Number, required: true }, initial: { type: Object, default: null } })
const emit = defineEmits(['close', 'restored'])
const editor = inject('gantt')
const ui = useUiStore()
const { confirm, prompt } = useDialogs()
const tab = ref(props.initial?.restore ? 'versions' : 'events')
const events = ref([])
const eventsMore = ref(null)
const snapshots = ref([])
const loading = ref(false)
const userFilter = ref('')

const REASONS = { auto: 'Автосохранение', manual: 'Сохранённая версия', before_restore: 'Перед восстановлением', legacy_import: 'Перенос из 1.0.1', before_import: 'Перед импортом' }
const ACTION_ICONS = { create: 'plus', update: 'edit', delete: 'trash', restore: 'undo', comment: 'message', legacy: 'history', archive: 'archive', unarchive: 'unarchive', import: 'upload', restore_snapshot: 'history' }
const users = computed(() => [...new Set(events.value.map((e) => e.userName).filter(Boolean))].sort())
const filtered = computed(() => (userFilter.value ? events.value.filter((e) => e.userName === userFilter.value) : events.value))
const grouped = computed(() => {
  const groups = []
  for (const event of filtered.value) {
    const day = new Date(event.createdAt).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })
    if (!groups.length || groups[groups.length - 1].day !== day) groups.push({ day, events: [] })
    groups[groups.length - 1].events.push(event)
  }
  return groups
})

function rowName(event) {
  return event.entityKey ? (editor.state.items[event.entityKey]?.name || '') : ''
}

async function loadEvents(more = false) {
  loading.value = true
  try {
    const res = await api.get(`/plans/${props.planId}/events`, { limit: 100, before: more ? eventsMore.value : null })
    events.value = more ? [...events.value, ...res.events] : res.events
    eventsMore.value = res.hasMore ? res.nextBefore : null
  } catch (err) { ui.error(err) } finally { loading.value = false }
}
async function loadSnapshots() {
  try { snapshots.value = await api.get(`/plans/${props.planId}/snapshots`) } catch (err) { ui.error(err) }
}
onMounted(async () => {
  await Promise.all([loadEvents(), loadSnapshots()])
  if (props.initial?.restore) {
    const snap = snapshots.value.find((s) => s.id === props.initial.restore)
    if (snap) restore(snap)
  }
})

async function saveVersion() {
  const label = await prompt({ title: 'Сохранить версию плана', label: 'Название версии', value: `Версия от ${new Date().toLocaleDateString('ru-RU')}`, confirmText: 'Сохранить' })
  if (!label) return
  try {
    await api.post(`/plans/${props.planId}/snapshots`, { label })
    await loadSnapshots()
    ui.success('Версия сохранена')
  } catch (err) { ui.error(err) }
}

async function preview(snap) {
  try {
    const res = await api.get(`/plans/${props.planId}/snapshots/${snap.id}`)
    const toMap = (list) => Object.fromEntries((list || []).map((e) => [e.key, e]))
    editor.state.previewSnapshot = {
      id: snap.id, label: formatDateTime(snap.createdAt),
      items: toMap(res.data.items), notes: toMap(res.data.notes), assignees: toMap(res.data.assignees),
    }
    editor.state.view.panelKey = null
    emit('close')
  } catch (err) { ui.error(err) }
}

async function restore(snap) {
  const ok = await confirm({
    title: 'Восстановить это состояние плана?',
    message: `План станет таким, каким был ${formatDateTime(snap.createdAt)} (${snap.itemsCount} строк). Текущее состояние сохранится отдельной версией — восстановление тоже можно отменить.`,
    confirmText: 'Восстановить', danger: false,
  })
  if (!ok) return
  try {
    await editor.flushNow()
    const res = await api.post(`/plans/${props.planId}/snapshots/${snap.id}/restore`)
    editor.state.previewSnapshot = null
    ui.success('План восстановлен', `Изменений: ${res.changes}`)
    emit('restored')
    emit('close')
  } catch (err) { ui.error(err) }
}
</script>

<template>
  <BaseModal title="История плана" size="wide" @close="emit('close')">
    <template #header-actions>
      <div class="segmented">
        <button type="button" :class="{ 'is-active': tab === 'events' }" @click="tab = 'events'">Журнал изменений</button>
        <button type="button" :class="{ 'is-active': tab === 'versions' }" @click="tab = 'versions'">Версии · {{ snapshots.length }}</button>
      </div>
    </template>

    <div v-if="tab === 'events'">
      <div class="toolbar">
        <select v-model="userFilter" class="select input-sm" style="max-width: 260px">
          <option value="">Все сотрудники</option>
          <option v-for="u in users" :key="u" :value="u">{{ u }}</option>
        </select>
        <span class="muted small">Отменить свои последние правки можно кнопкой «Отменить» на панели (Ctrl+Z).</span>
      </div>
      <div v-for="group in grouped" :key="group.day" class="day">
        <div class="day-title">{{ group.day }}</div>
        <div v-for="event in group.events" :key="event.id" class="event">
          <span class="icon"><AppIcon :name="ACTION_ICONS[event.action] || 'edit'" :size="13" /></span>
          <div class="event-main">
            <div v-if="event.action === 'legacy' && rowName(event)" class="target">{{ rowName(event) }}</div>
            <div class="summary">{{ event.summary }}</div>
            <div class="muted small">{{ event.userName || 'система' }} · {{ formatDateTime(event.createdAt) }}<span v-if="event.action === 'legacy'"> · из 1.0</span></div>
          </div>
        </div>
      </div>
      <p v-if="!loading && !filtered.length" class="muted">Записей нет.</p>
      <button v-if="eventsMore" class="btn btn-sm" type="button" :disabled="loading" @click="loadEvents(true)">Показать ещё</button>
    </div>

    <div v-else>
      <div class="toolbar">
        <button v-if="editor.canEdit" class="btn btn-sm btn-primary" type="button" @click="saveVersion"><AppIcon name="save" :size="14" /> Сохранить текущую версию</button>
        <span class="muted small">Автоснимки делаются перед правками не чаще раза в 30 минут: все за двое суток, затем по одному в день (60 дней) и в неделю.</span>
      </div>
      <div v-for="snap in snapshots" :key="snap.id" class="snapshot">
        <div class="snap-main">
          <div class="snap-title">{{ snap.label || REASONS[snap.reason] || snap.reason }}</div>
          <div class="muted small">
            <span :title="formatDateTime(snap.createdAt)">{{ formatRelative(snap.createdAt) }}</span> · {{ REASONS[snap.reason] || snap.reason }} · {{ snap.itemsCount }} строк<span v-if="snap.userName"> · {{ snap.userName }}</span>
          </div>
        </div>
        <button class="btn btn-sm" type="button" @click="preview(snap)"><AppIcon name="eye" :size="14" /> Просмотреть</button>
        <button v-if="editor.canEdit" class="btn btn-sm" type="button" @click="restore(snap)"><AppIcon name="history" :size="14" /> Восстановить</button>
      </div>
      <p v-if="!snapshots.length" class="muted">Сохранённых версий пока нет.</p>
    </div>
  </BaseModal>
</template>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.day { margin-bottom: 12px; }
.day-title { font-size: 12px; font-weight: 700; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 6px; position: sticky; top: -16px; background: #fff; padding: 4px 0; }
.event { display: flex; gap: 10px; padding: 5px 0; }
.icon { width: 24px; height: 24px; border-radius: 50%; background: #eef1f7; color: var(--color-text-muted); display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.event-main { min-width: 0; }
.summary { font-size: 13px; overflow-wrap: anywhere; }
.target { font-size: 12px; font-weight: 600; color: var(--color-text-secondary); }
.snapshot { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border: 1px solid var(--color-border); border-radius: var(--radius-md); margin-bottom: 6px; }
.snap-main { flex: 1; min-width: 0; }
.snap-title { font-weight: 600; font-size: 13px; }
</style>
