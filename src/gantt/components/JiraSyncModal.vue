<script setup>
import { computed, inject, onMounted, reactive, ref } from 'vue'
import { api } from '../../api/client'
import BaseModal from '../../components/common/BaseModal.vue'
import HelpLink from '../../components/common/HelpLink.vue'
import AppIcon from '../../components/common/AppIcon.vue'
import { STATUS_BY_KEY } from '../domain/colors'
import { formatDay } from '../domain/dates'
import { formatRelative } from '../../utils/format'

/** Сверка плана с Jira: строки, у которых в Jira другое название, статус, сроки или прогресс. */
const emit = defineEmits(['close'])
const editor = inject('gantt')
const jira = inject('jira')
const FIELDS = [
  { key: 'name', label: 'Название' }, { key: 'status', label: 'Статус' }, { key: 'dates', label: 'Сроки' },
  { key: 'date', label: 'Дата' }, { key: 'progress', label: 'Прогресс' },
]
const enabled = reactive(Object.fromEntries(FIELDS.map((f) => [f.key, f.key !== 'name'])))
// Автоперенос — настройка плана: выбранные поля переносятся сами при открытии плана и обновлении из Jira
const AUTO = [
  { key: 'status', label: 'Статус' }, { key: 'dates', label: 'Сроки' }, { key: 'progress', label: 'Прогресс' },
  { key: 'name', label: 'Название' },
]
const auto = computed(() => new Set(jira.autoFields()))
function toggleAuto(key) {
  const next = new Set(auto.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  jira.setAutoFields(AUTO.map((f) => f.key).filter((k) => next.has(k)))
}
const excluded = ref(new Set())
const refreshing = ref(false)

// Источник задач плана: проект Jira и уточняющий JQL — план сообщает о новых задачах источника
const source = reactive({ ...jira.sourceSetting() })
const sourceSaved = computed(() => {
  const saved = jira.sourceSetting()
  return saved.project === source.project && saved.jql === source.jql.trim()
})
const projects = ref(null)
onMounted(async () => {
  if (!editor.canEdit) return
  try { projects.value = (await api.get('/jira/projects', { planId: editor.state.plan.id })).projects } catch { projects.value = [] }
})
function saveSource() { jira.setSource(source) }
function clearSource() { Object.assign(source, { project: '', jql: '' }); jira.setSource(source) }

const rows = computed(() => jira.linkedItems().map((item) => {
  const issue = jira.issueFor(item)
  return { item, key: jira.keyFor(item), issue, changes: issue ? jira.changesFor(item) : [] }
}))
const differing = computed(() => rows.value.filter((r) => r.changes.length))
const missing = computed(() => rows.value.filter((r) => r.issue === null))
const counts = computed(() => Object.fromEntries(FIELDS.map((f) => [f.key, differing.value.filter((r) => r.changes.some((c) => c.field === f.key)).length])))
const selected = computed(() => differing.value.flatMap((r) => r.changes
  .filter((c) => enabled[c.field] && !excluded.value.has(`${r.item.key}:${c.field}`))
  .map((c) => ({ key: r.item.key, fields: c.fields }))))

function toggle(rowKey, field) {
  const id = `${rowKey}:${field}`
  const next = new Set(excluded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  excluded.value = next
}
function describe(change) {
  if (change.field === 'status') return [STATUS_BY_KEY[change.from]?.label, STATUS_BY_KEY[change.to]?.label]
  if (change.field === 'dates') return [`${formatDay(change.from[0])} — ${formatDay(change.from[1])}`, `${formatDay(change.to[0])} — ${formatDay(change.to[1])}`]
  if (change.field === 'date') return [formatDay(change.from), formatDay(change.to)]
  if (change.field === 'progress') return [`${change.from}%`, `${change.to}%`]
  return [change.from, change.to]
}
async function refresh() {
  refreshing.value = true
  try { await jira.load({ refresh: true }) } finally { refreshing.value = false }
}
function apply() {
  const count = new Set(selected.value.map((s) => s.key)).size
  jira.apply(selected.value, `Сверка с Jira (${count})`)
  emit('close')
}
function open(key) {
  editor.revealItem(key)
  editor.select(key, { openPanel: true })
  emit('close')
}
</script>

<template>
  <BaseModal title="Сверка и настройки Jira" size="wide" @close="emit('close')">
    <template #header-actions><HelpLink topic="jira-sync" /></template>
    <div class="top">
      <p class="muted small">Строки плана, привязанные к задачам Jira: {{ rows.length }}. Отметьте, что перенести из Jira в план, —
        всё переносится одной правкой и отменяется кнопкой «Отменить» или Ctrl+Z.</p>
      <button class="btn btn-sm" type="button" :disabled="refreshing" @click="refresh">
        <AppIcon name="refresh" :size="13" /> {{ refreshing ? 'Обновляем…' : 'Обновить из Jira' }}
      </button>
    </div>
    <section class="auto" :class="{ on: auto.size }" data-testid="jira-auto">
      <div class="auto-text">
        <strong>Переносить из Jira автоматически <HelpLink topic="jira-auto" /></strong>
        <span class="muted small">Выбранное переносится в строки само — при открытии плана, обновлении из Jira и привязке
          строки — одной правкой, которую можно отменить. Настройка плана: действует у всех, кто его правит.</span>
      </div>
      <div class="auto-fields">
        <label v-for="f in AUTO" :key="f.key" class="chip-check" :class="{ checked: auto.has(f.key) }">
          <input type="checkbox" :checked="auto.has(f.key)" :disabled="!editor.canEdit" :data-testid="`jira-auto-${f.key}`" @change="toggleAuto(f.key)" />
          {{ f.label }}
        </label>
      </div>
    </section>
    <section v-if="editor.canEdit" class="auto source" :class="{ on: jira.sourceSetting().project || jira.sourceSetting().jql }" data-testid="jira-source">
      <div class="auto-text">
        <strong>Источник задач плана <HelpLink topic="jira-source" /></strong>
        <span class="muted small">Проект Jira (и, по желанию, условие JQL), задачи которого ведутся в этом плане. «Задачи из Jira»
          откроются сразу на нём, а кнопка J подскажет о новых задачах, которых в плане ещё нет.</span>
        <span v-if="jira.state.source?.configured && !jira.state.source.error" class="small source-state">
          В источнике задач: {{ jira.state.source.total }}{{ jira.state.source.truncated ? '+' : '' }} · новых: <strong>{{ jira.state.source.newCount }}</strong>
        </span>
      </div>
      <div class="source-form">
        <select v-model="source.project" class="select select-sm" aria-label="Проект Jira" data-testid="jira-source-project">
          <option value="">Без проекта</option>
          <option v-for="p in projects || []" :key="p.key" :value="p.key">{{ p.name }} · {{ p.key }}</option>
          <option v-if="source.project && projects && !projects.some((p) => p.key === source.project)" :value="source.project">{{ source.project }}</option>
        </select>
        <input v-model="source.jql" class="input input-sm mono" maxlength="1000" placeholder="Условие JQL, например fixVersion = &quot;Релиз 2&quot;" aria-label="Условие JQL" data-testid="jira-source-jql" />
        <button class="btn btn-sm btn-primary" type="button" :disabled="sourceSaved" data-testid="jira-source-save" @click="saveSource">Сохранить</button>
        <button v-if="jira.sourceSetting().project || jira.sourceSetting().jql" class="btn btn-sm btn-ghost" type="button" @click="clearSource">Убрать</button>
      </div>
    </section>
    <p v-if="jira.state.error" class="error small"><AppIcon name="warning" :size="13" /> {{ jira.state.error }}</p>
    <p v-else-if="jira.state.fetchedAt" class="muted small">Данные Jira {{ formatRelative(jira.state.fetchedAt) }}</p>

    <div class="fields">
      <label v-for="f in FIELDS.filter((x) => counts[x.key])" :key="f.key" class="chip-check">
        <input v-model="enabled[f.key]" type="checkbox" /> {{ f.label }} <span class="muted">{{ counts[f.key] }}</span>
      </label>
    </div>

    <div v-if="!differing.length" class="empty-state">
      <h3>Всё совпадает</h3>
      <p>Названия, статусы, сроки и прогресс строк совпадают с задачами Jira.</p>
    </div>
    <div v-else class="table scroll-thin" data-testid="jira-sync-table">
      <template v-for="row in differing" :key="row.item.key">
        <div class="row-head">
          <button class="row-name ellipsis" type="button" :title="row.item.name" @click="open(row.item.key)">{{ row.item.name }}</button>
          <a class="key" :href="row.issue.url" target="_blank" rel="noopener noreferrer">{{ row.key }}</a>
          <span class="jira-status" :class="`cat-${row.issue.statusCategory}`">{{ row.issue.status }}</span>
        </div>
        <label v-for="change in row.changes" :key="change.field" class="change" :class="{ off: !enabled[change.field] }">
          <input type="checkbox" :disabled="!enabled[change.field]" :checked="enabled[change.field] && !excluded.has(`${row.item.key}:${change.field}`)" @change="toggle(row.item.key, change.field)" />
          <span class="muted label">{{ change.label }}</span>
          <span class="from ellipsis" :title="String(describe(change)[0])">{{ describe(change)[0] }}</span>
          <AppIcon name="arrowRight" :size="12" class="muted" />
          <span class="to ellipsis" :title="String(describe(change)[1]) + (change.note ? ` · ${change.note}` : '')">{{ describe(change)[1] }}<span v-if="change.note" class="muted"> · {{ change.note }}</span></span>
        </label>
      </template>
    </div>
    <p v-if="missing.length" class="muted small">
      Нет в Jira или недоступны учётной записи подключения: {{ missing.map((r) => r.key).join(', ') }}
    </p>

    <template #footer>
      <button class="btn" type="button" @click="emit('close')">Закрыть</button>
      <button class="btn btn-primary" type="button" :disabled="!selected.length || !editor.canEdit" data-testid="jira-sync-apply" @click="apply">
        Перенести выбранное<span v-if="selected.length"> ({{ selected.length }})</span>
      </button>
    </template>
  </BaseModal>
</template>

<style scoped>
.top { display: flex; align-items: flex-start; gap: 12px; justify-content: space-between; }
.top p { margin: 0; line-height: 1.5; }
.error { color: var(--color-danger); display: flex; align-items: center; gap: 6px; }
.fields { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0; }
.auto { display: flex; align-items: center; gap: 12px; justify-content: space-between; flex-wrap: wrap; margin: 12px 0 8px; padding: 10px 12px; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface-2); }
.auto.on { border-color: #b3c7f5; background: #f3f6ff; }
.auto-text { display: flex; flex-direction: column; gap: 2px; flex: 1 1 320px; line-height: 1.45; }
.auto-fields { display: flex; flex-wrap: wrap; gap: 6px; }
.source-form { display: grid; grid-template-columns: minmax(160px, 220px) minmax(200px, 1fr) auto auto; gap: 6px; flex: 1 1 100%; align-items: center; }
.source-state { color: #0052cc; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
@media (max-width: 768px) { .source-form { grid-template-columns: 1fr; } }
.chip-check.checked { border-color: #0052cc; color: #0052cc; background: #fff; }
.chip-check { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border: 1px solid var(--color-border); border-radius: 20px; font-size: 12.5px; cursor: pointer; }
.table { max-height: 52vh; overflow: auto; border: 1px solid var(--color-border); border-radius: var(--radius-md); }
.row-head { display: flex; align-items: center; gap: 8px; padding: 8px 12px 4px; border-top: 1px solid var(--color-border); background: var(--color-surface-2); position: sticky; top: 0; }
.row-head:first-child { border-top: 0; }
.row-name { flex: 1; border: 0; background: none; padding: 0; text-align: left; font-weight: 600; font-size: 13px; color: var(--color-text); }
.row-name:hover { color: var(--color-primary-dark); text-decoration: underline; }
.key { font-size: 12px; font-weight: 700; color: #0052cc; text-decoration: none; }
.change { display: grid; grid-template-columns: 18px 84px minmax(0, 1fr) 14px minmax(0, 1fr); align-items: center; gap: 8px; padding: 4px 12px 4px 16px; font-size: 12.5px; cursor: pointer; }
.change.off { opacity: 0.45; }
.label { font-size: 12px; }
.from { color: var(--color-text-muted); text-decoration: line-through; text-decoration-color: rgba(107, 114, 128, 0.5); }
.to { font-weight: 600; }
</style>
