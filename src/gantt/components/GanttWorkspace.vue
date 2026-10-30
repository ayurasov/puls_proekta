<script setup>
import { computed, onBeforeUnmount, onMounted, provide, ref, watch } from 'vue'
import { api } from '../../api/client'
import { useUiStore } from '../../stores/ui'
import { useDirectoryStore } from '../../stores/directory'
import { useDialogs } from '../../composables/useDialogs'
import { createPlanEditor } from '../editor'
import { createJira } from '../jira'
import { computeDependencyShifts } from '../domain/dependencies'
import { descendantKeys } from '../domain/tree'
import { formatRelative } from '../../utils/format'
import AppIcon from '../../components/common/AppIcon.vue'
import UserAvatar from '../../components/common/UserAvatar.vue'
import GanttToolbar from './GanttToolbar.vue'
import GanttChart from './GanttChart.vue'
import ItemPanel from './ItemPanel.vue'
import PlanSettingsModal from './PlanSettingsModal.vue'
import AssigneesModal from './AssigneesModal.vue'
import StatsModal from './StatsModal.vue'
import HistoryModal from './HistoryModal.vue'
import CompletionDialog from './CompletionDialog.vue'
import NoteEditor from './NoteEditor.vue'
import JiraSyncModal from './JiraSyncModal.vue'
import JiraImportModal from './JiraImportModal.vue'

const props = defineProps({
  planId: { type: Number, required: true },
  project: { type: Object, required: true },
})
const emit = defineEmits(['plan-meta'])
const ui = useUiStore()
const directory = useDirectoryStore()
const { confirm } = useDialogs()

const editor = createPlanEditor({
  planId: props.planId,
  ui,
  onRemoteChange: () => {},
})
provide('gantt', editor)
const jira = createJira(editor, ui)
provide('jira', jira)
defineExpose({ editor })

const chart = ref(null)
const toolbar = ref(null)
const modal = ref(null)
const completion = ref(null)
const noteEditor = ref(null)
const savedTick = ref(0)
let tickTimer = null

const state = editor.state
const panelItem = computed(() => (state.view.panelKey ? editor.source.items[state.view.panelKey] : null))

onMounted(async () => {
  directory.load().catch(() => {})
  try {
    await editor.load()
    editor.start()
    chart.value?.scrollToToday({ initial: true })
    jira.init()
  } catch {
    /* сообщение показывает шаблон */
  }
  tickTimer = setInterval(() => { savedTick.value += 1 }, 30000)
  window.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  editor.stop()
  clearInterval(tickTimer)
  window.removeEventListener('keydown', onKeydown)
})

watch(() => state.plan?.title, (title, old) => { if (old && title !== old) emit('plan-meta') })
// Коллега привязал строку к задаче Jira — подгружаем её, не дожидаясь обновления всего плана
watch(() => (jira.state.loaded ? jira.linkedItems().map((item) => jira.keyFor(item)).join(' ') : ''), (keys) => {
  if (keys) jira.fetchKeys(keys.split(' '))
})

const saveStatus = computed(() => {
  void savedTick.value
  if (state.authLost) return { kind: 'offline', text: `Сессия истекла · ${editor.pendingCount} ${editor.pendingCount === 1 ? 'изменение ждёт' : 'изменений ждут'} входа` }
  if (state.offline) return { kind: 'offline', text: `Нет связи · ${editor.pendingCount} ${editor.pendingCount === 1 ? 'изменение ждёт' : 'изменений ждут'} отправки` }
  if (state.inFlight || state.queue.length) return { kind: 'saving', text: 'Сохранение…' }
  if (state.lastSavedAt) return { kind: 'saved', text: `Сохранено ${formatRelative(state.lastSavedAt)}` }
  return { kind: 'idle', text: editor.canEdit ? 'Все изменения сохраняются автоматически' : '' }
})

const inconsistent = computed(() => {
  if (!editor.canEdit || !editor.settings.display.autoShift || !state.plan) return 0
  return computeDependencyShifts(editor.itemList).size
})
function alignDependencies() {
  const ops = editor.cascadeOps(new Map(), { all: true })
  editor.commit(ops, 'Выравнивание по связям')
}

// ---------- действия, общие для меню и клавиатуры ----------
async function deleteRows(keys) {
  const items = keys.map((k) => state.items[k]).filter(Boolean)
  if (!items.length) return
  const children = new Set()
  for (const item of items) for (const d of descendantKeys(editor.index, item.key)) children.add(d)
  let withChildren = false
  if (children.size) {
    const choice = await ui.openDialog({
      type: 'choice',
      title: items.length === 1 ? `Удалить «${items[0].name}»?` : `Удалить строки (${items.length})?`,
      message: `Внутри ${children.size} ${children.size === 1 ? 'вложенная строка' : 'вложенных строк'}. Удалить их тоже или оставить, подняв на уровень выше?`,
      choices: [
        { value: 'keep', label: 'Оставить вложенные' },
        { value: 'all', label: 'Удалить всё', danger: true },
      ],
    })
    if (!choice) return
    withChildren = choice === 'all'
  } else if (items.length > 1) {
    const ok = await confirm({
      title: `Удалить строки (${items.length})?`,
      message: 'Удаление можно отменить сочетанием Ctrl+Z или из истории плана.',
      confirmText: 'Удалить',
    })
    if (!ok) return
  }
  // Одна строка без вложенных удаляется сразу: отмена — кнопкой в уведомлении, Ctrl+Z или из истории плана
  const entry = editor.deleteItems(keys, { withChildren, label: items.length === 1 ? `Удаление «${items[0].name}»` : 'Удаление строк' })
  if (!entry) return
  ui.toast({
    title: items.length === 1 ? `«${items[0].name}» удалена` : 'Строки удалены', kind: 'success', timeout: 8000,
    // Отменяется именно это удаление, даже если после него были другие правки
    action: { label: 'Отменить', handler: () => editor.undo(entry) },
  })
}

function askCompletion(key) {
  const item = state.items[key]
  if (!item) return
  completion.value = { key, item }
}
function applyCompletion(date) {
  const { key } = completion.value
  completion.value = null
  editor.setStatus(key, 'done', date)
}

function setStatus(key, status) {
  if (status === 'done' && state.items[key]?.status !== 'done') askCompletion(key)
  else editor.setStatus(key, status)
}

function openNoteEditor(payload) {
  noteEditor.value = payload
}

function isTyping(event) {
  const t = event.target
  return t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))
}

function onKeydown(event) {
  if (document.querySelector('.modal-overlay')) return
  const mod = event.ctrlKey || event.metaKey
  if (mod && event.key.toLowerCase() === 'z' && !isTyping(event)) {
    event.preventDefault()
    if (event.shiftKey) editor.redo()
    else editor.undo()
    return
  }
  if (mod && event.key.toLowerCase() === 'y' && !isTyping(event)) {
    event.preventDefault()
    editor.redo()
    return
  }
  if (isTyping(event)) return
  const selected = state.view.selectedKey
  if (event.key === 'Escape') {
    if (state.view.panelKey) state.view.panelKey = null
    else state.view.selectedKey = null
    return
  }
  if (event.key === 'Insert' && editor.canEdit) { event.preventDefault(); toolbar.value?.addRow('task'); return }
  if (!selected) return
  if ((event.key === 'Delete') && editor.canEdit) { event.preventDefault(); deleteRows([selected]); return }
  if (event.key === 'Enter') { event.preventDefault(); state.view.panelKey = selected; return }
  if (event.key === 'F2' && editor.canEdit) { event.preventDefault(); chart.value?.startRename(selected); return }
  if (mod && event.key.toLowerCase() === 'd' && editor.canEdit) {
    event.preventDefault()
    const copy = editor.duplicateItem(selected)
    if (copy) editor.select(copy)
    return
  }
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    const rows = editor.visibleRows
    const i = rows.findIndex((r) => r.item.key === selected)
    const next = rows[Math.max(0, Math.min(rows.length - 1, i + (event.key === 'ArrowDown' ? 1 : -1)))]
    if (next) { editor.select(next.item.key); chart.value?.scrollToRow(next.item.key) }
  }
}

async function restoreArchived() {
  try {
    await api.post(`/plans/${props.planId}/unarchive`)
    await editor.load({ keepView: true })
    emit('plan-meta')
  } catch (err) { ui.error(err) }
}

const actions = { deleteRows, setStatus, askCompletion, openNoteEditor, openModal: (name, payload) => { modal.value = { name, payload } } }
provide('ganttActions', actions)
</script>

<template>
  <Teleport defer to="#project-header-slot">
    <div v-if="state.presence.length" class="presence" :title="state.presence.map((p) => p.name + (p.editing ? ' — редактирует' : ' — смотрит')).join('\n')">
      <span class="presence-label muted small">Сейчас на плане</span>
      <span v-for="person in state.presence.slice(0, 5)" :key="person.id" class="presence-avatar" :class="{ editing: person.editing }">
        <UserAvatar :user="person" :size="26" />
      </span>
      <span v-if="state.presence.length > 5" class="badge">+{{ state.presence.length - 5 }}</span>
    </div>
    <div v-if="saveStatus.text" class="save-status small" :class="`is-${saveStatus.kind}`" data-testid="save-status">
      <AppIcon :name="saveStatus.kind === 'offline' ? 'wifiOff' : saveStatus.kind === 'saving' ? 'refresh' : 'cloudCheck'" :size="15" />
      <span>{{ saveStatus.text }}</span>
    </div>
  </Teleport>

  <div v-if="state.loadError" class="page">
    <div class="card empty-state">
      <h3>Не удалось открыть план</h3>
      <p>{{ state.loadError.message }}</p>
      <button class="btn btn-primary" type="button" @click="editor.load().then(() => editor.start())">Повторить</button>
    </div>
  </div>
  <div v-else-if="state.loading" class="loading muted">Загрузка плана…</div>
  <div v-else class="workspace">
    <GanttToolbar ref="toolbar" @open-modal="(name) => (modal = { name })" @scroll-today="chart?.scrollToToday()" @fit="chart?.fitToWidth()" />
    <div v-if="state.previewSnapshot" class="banner banner-info">
      <AppIcon name="history" /> Вы смотрите сохранённое состояние плана от {{ state.previewSnapshot.label }}. Изменения недоступны.
      <span class="spacer" />
      <button class="btn btn-sm" type="button" @click="modal = { name: 'history', payload: { restore: state.previewSnapshot.id } }">Восстановить это состояние</button>
      <button class="btn btn-sm btn-primary" type="button" @click="state.previewSnapshot = null">Вернуться к текущему</button>
    </div>
    <div v-else-if="state.plan?.isArchived" class="banner banner-warning">
      <AppIcon name="archive" /> План в архиве — только просмотр.
      <span class="spacer" />
      <button v-if="['edit', 'manage'].includes(state.access)" class="btn btn-sm" type="button" @click="restoreArchived">Вернуть во вкладки</button>
    </div>
    <div v-else-if="state.access === 'view'" class="banner banner-muted">
      <AppIcon name="eye" /> У вас доступ только для просмотра: можно смотреть, фильтровать и выгружать план.
    </div>
    <div v-if="state.offline" class="banner banner-danger">
      <AppIcon name="wifiOff" /> Нет связи с сервером. Изменения сохраняются на этой странице и будут отправлены, когда связь восстановится — не закрывайте вкладку.
    </div>
    <div v-if="inconsistent && !state.previewSnapshot" class="banner banner-info">
      <AppIcon name="link" /> Сроки {{ inconsistent }} {{ inconsistent === 1 ? 'задачи не согласованы' : 'задач не согласованы' }} со связями «предшественник».
      <span class="spacer" />
      <button class="btn btn-sm" type="button" @click="alignDependencies">Выровнять по связям</button>
    </div>
    <div class="workspace-body">
      <GanttChart ref="chart" class="chart" />
      <ItemPanel v-if="panelItem" :key="panelItem.key" :item="panelItem" @close="state.view.panelKey = null" />
    </div>
  </div>

  <PlanSettingsModal v-if="modal?.name === 'settings'" @close="modal = null" />
  <AssigneesModal v-if="modal?.name === 'assignees'" @close="modal = null" />
  <StatsModal v-if="modal?.name === 'stats'" @close="modal = null" />
  <HistoryModal v-if="modal?.name === 'history'" :plan-id="planId" :initial="modal.payload" @close="modal = null" @restored="editor.load({ keepView: true, resetHistory: true })" />
  <CompletionDialog v-if="completion" :item="completion.item" :today="state.today" @cancel="completion = null" @confirm="applyCompletion" />
  <NoteEditor v-if="noteEditor" v-bind="noteEditor" @close="noteEditor = null" />
  <JiraSyncModal v-if="modal?.name === 'jira-sync'" @close="modal = null" />
  <JiraImportModal v-if="modal?.name === 'jira-import'" @close="modal = null" />
</template>

<style scoped>
.workspace { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.workspace-body { flex: 1; display: flex; min-height: 0; position: relative; }
.chart { flex: 1; min-width: 0; }
.loading { padding: 40px; text-align: center; }
.banner { display: flex; align-items: center; gap: 8px; padding: 7px 16px; font-size: 13px; border-bottom: 1px solid var(--color-border); flex-shrink: 0; }
.banner-info { background: #eef3ff; color: #2b4db5; }
.banner-warning { background: var(--color-warning-soft); color: #8a5a10; }
.banner-muted { background: var(--color-surface-2); color: var(--color-text-secondary); }
.banner-danger { background: var(--color-danger-soft); color: #a3242b; }
.presence { display: flex; align-items: center; gap: 4px; }
.presence-label { margin-right: 4px; }
.presence-avatar { border-radius: 50%; box-shadow: 0 0 0 2px #fff; display: inline-flex; }
.presence-avatar.editing { box-shadow: 0 0 0 2px #fff, 0 0 0 4px var(--color-success); }
.save-status { display: flex; align-items: center; gap: 6px; color: var(--color-text-muted); white-space: nowrap; }
.save-status.is-saved { color: #1f7a4c; }
.save-status.is-offline { color: var(--color-danger); font-weight: 600; }
.save-status.is-saving svg { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 900px) { .presence-label { display: none; } }
</style>
