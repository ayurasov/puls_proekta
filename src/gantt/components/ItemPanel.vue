<script setup>
import { computed, inject, onBeforeUnmount, reactive, ref, watch } from 'vue'
import AppIcon from '../../components/common/AppIcon.vue'
import HelpLink from '../../components/common/HelpLink.vue'
import SegmentedControl from '../../components/common/SegmentedControl.vue'
import UserAvatar from '../../components/common/UserAvatar.vue'
import ColorPicker from './ColorPicker.vue'
import ItemComments from './ItemComments.vue'
import ItemHistory from './ItemHistory.vue'
import JiraIssueCard from './JiraIssueCard.vue'
import PodeluTaskList from '../../components/integrations/PodeluTaskList.vue'
import { ISSUE_KEY_RE, issueKeyFromLinks, jiraLinkFromInput } from '../jira'
import { STATUSES, STATUS_BY_KEY, DEPENDENCY_TYPES, resolveColor } from '../domain/colors'
import { addDays, diffDays, formatDay, formatDayNoYear } from '../domain/dates'
import { resolveEnd } from '../domain/calendar'
import { dependencyConstraints, dependentKeys } from '../domain/dependencies'
import { descendantKeys, groupSpan } from '../domain/tree'
import { useDialogs } from '../../composables/useDialogs'
import { useDirectoryStore } from '../../stores/directory'
import { useUiStore } from '../../stores/ui'
import { formatDateTime } from '../../utils/format'

const props = defineProps({ item: { type: Object, required: true } })
const emit = defineEmits(['close'])
const editor = inject('gantt')
const actions = inject('ganttActions')
const jira = inject('jira')
const { prompt } = useDialogs()
const ui = useUiStore()
const directory = useDirectoryStore()
const tab = ref('main')
const readonly = computed(() => !editor.canEdit)
const item = computed(() => props.item)

// Черновики текстовых полей: удалённые правки не затирают то, что сотрудник сейчас печатает
const drafts = reactive({ name: '', barText: '', jiraUrl: '', comment: '' })
const focused = ref(null)
function syncDrafts() {
  for (const field of Object.keys(drafts)) if (focused.value !== field) drafts[field] = item.value[field] || ''
}
watch(item, syncDrafts, { immediate: true, deep: true })

function save(fields, label) {
  editor.updateItem(item.value.key, fields, label || `Изменение «${item.value.name}»`)
}
let cancelled = null
/** Esc в текстовом поле карточки — вернуть прежнее значение, ничего не сохранять. */
function cancelText(field, event) {
  cancelled = field
  drafts[field] = item.value[field] || ''
  event.target.blur()
}
function commitText(field) {
  focused.value = null
  if (cancelled === field) { cancelled = null; return }
  const value = drafts[field].trim()
  if (field === 'name' && !value) { drafts.name = item.value.name; return }
  if (field === 'jiraUrl') {
    // Ключ задачи (ABC-123) превращается в ссылку на подключённую Jira
    const link = jiraLinkFromInput(value, jira.state.baseUrl)
    if (link.error) { ui.error(link.error); return }
    drafts.jiraUrl = link.url || ''
    if ((item.value.jiraUrl || '') !== (link.url || '')) {
      save({ jiraUrl: link.url }, link.url ? 'Задача в Jira' : 'Отвязка от Jira')
      const key = issueKeyFromLinks(link.url, jira.state.baseUrl, jira.state.linkUrls)
      if (key) jira.fetchKeys([key])
    }
    return
  }
  if ((item.value[field] || '') !== value) save({ [field]: value || null })
}

// Подсказки задач Jira при вводе слов из названия: выбрать задачу, не зная её ключа
const suggestions = ref([])
const suggestBusy = ref(false)
let suggestTimer = null
let suggestSeq = 0
watch(() => drafts.jiraUrl, (value) => {
  clearTimeout(suggestTimer)
  const text = (value || '').trim()
  if (focused.value !== 'jiraUrl' || readonly.value || !jira.state.connected || text.length < 3 || /^https?:/i.test(text)
      || (ISSUE_KEY_RE.test(text.toUpperCase()) && text === item.value.jiraUrl)) {
    suggestions.value = []
    return
  }
  suggestTimer = setTimeout(async () => {
    const seq = ++suggestSeq
    suggestBusy.value = true
    try {
      const found = await jira.suggest(text)
      if (seq === suggestSeq && focused.value === 'jiraUrl') suggestions.value = found
    } catch { if (seq === suggestSeq) suggestions.value = [] } finally { if (seq === suggestSeq) suggestBusy.value = false }
  }, 350)
})
function pickSuggestion(issue) {
  drafts.jiraUrl = issue.url
  suggestions.value = []
  document.activeElement?.blur?.()
}
function hideSuggestions() { setTimeout(() => { suggestions.value = [] }, 150) }
onBeforeUnmount(() => clearTimeout(suggestTimer))
const foreignHost = computed(() => jira.foreignHostFor(item.value))
// Задачи «По Делу», которые ссылаются на эту строку плана (ADR-0010)
const podeluTasks = inject('podeluTasks', null)
const rowPodeluTasks = computed(() => (podeluTasks?.state.enabled ? podeluTasks.forItem(editor.state.plan?.id, item.value.key) : []))

const typeLabel = computed(() => ({ task: 'Задача', milestone: 'Веха', group: 'Группа' })[item.value.type])
const status = computed(() => STATUS_BY_KEY[item.value.status || 'new'] || STATUS_BY_KEY.new)
const STATUS_OPTIONS = STATUSES.map((s) => ({ value: s.key, label: s.label, color: s.color, text: s.text }))
const tabs = computed(() => [
  { value: 'main', label: 'Основное' },
  ...(item.value.type === 'task' ? [{ value: 'comments', label: 'Комментарии', badge: item.value.commentCount || null }] : []),
  { value: 'history', label: 'История' },
])
const END_MODES = [
  { value: 'date', label: 'По дате', title: 'Окончание — датой' },
  { value: 'duration', label: 'По длительности', title: 'Окончание считается от начала по числу дней' },
]
const DURATION_TYPES = [
  { value: 'calendar', label: 'Календарные' },
  { value: 'working', label: 'Рабочие', title: 'Рабочие дни по производственному календарю' },
]
const LINE_STYLES = [
  { value: 'dashed', label: 'Пунктирная', sample: 'dashed' },
  { value: 'solid', label: 'Сплошная', sample: 'solid' },
  { value: 'dotted', label: 'Точечная', sample: 'dotted' },
]
const parentName = computed(() => (item.value.parentKey ? editor.index.byKey.get(item.value.parentKey)?.name : ''))
// Исполнитель — аватаром на его цвете (фото, если связан с сотрудником системы)
const assignee = computed(() => (item.value.assigneeKey ? editor.assigneeList.find((a) => a.key === item.value.assigneeKey) : null))
const assigneeUser = computed(() => {
  const a = assignee.value
  return a ? (a.userId && directory.byId[a.userId]) || { name: a.name } : null
})
// Ползунок прогресса: заливка следует за ним при перетаскивании, сохраняется — как раньше, по отпусканию
const progressDraft = ref(null)
const progressShown = computed(() => (progressDraft.value !== null ? Number(progressDraft.value) : item.value.progress || 0))
const byKey = computed(() => editor.index.byKey)
const descendants = computed(() => descendantKeys(editor.index, item.value.key))
const parentOptions = computed(() => editor.itemList
  .filter((i) => i.type !== 'milestone' && i.key !== item.value.key && !descendants.value.has(i.key))
  .map((i) => ({ key: i.key, label: `${i.type === 'group' ? '▣ ' : ''}${i.name}` })))
// Зависящие от этой задачи не предлагаются: связь с ними замкнула бы цикл
const dependents = computed(() => dependentKeys(editor.itemList, item.value.key))
const dependencyOptions = computed(() => editor.itemList
  .filter((i) => (i.type === 'task' || i.type === 'milestone') && i.key !== item.value.key && !dependents.value.has(i.key))
  .map((i) => ({ key: i.key, label: `${i.type === 'milestone' ? '◆ ' : ''}${i.name}` })))
// В большом плане списки строк длинные — над ними поле поиска (выбранная строка видна всегда)
const LONG_LIST = 25
const dependencyQuery = ref('')
const parentQuery = ref('')
function filterOptions(options, query, selectedKey) {
  const q = query.trim().toLowerCase()
  if (!q) return options
  return options.filter((o) => o.key === selectedKey || o.label.toLowerCase().includes(q))
}
const shownDependencyOptions = computed(() => filterOptions(dependencyOptions.value, dependencyQuery.value, item.value.dependencyKey))
const shownParentOptions = computed(() => filterOptions(parentOptions.value, parentQuery.value, item.value.parentKey))
const constraints = computed(() => (item.value.type === 'task' ? dependencyConstraints(item.value, byKey.value) : null))
const duration = computed(() => (item.value.type === 'task' ? diffDays(item.value.start, item.value.end) + 1 : 0))
const workingDays = computed(() => (item.value.type === 'task' ? editor.calendar.countWorkingDays(item.value.start, item.value.end) : 0))
const span = computed(() => (item.value.type !== 'milestone' ? groupSpan(editor.index, item.value.key) : null))
const updatedBy = computed(() => (item.value.updatedById ? editor.state.users[String(item.value.updatedById)]?.name : ''))

function setStart(value) {
  if (!value) return
  const it = item.value
  if (it.endMode === 'duration') {
    save({ start: value, end: resolveEnd(editor.calendar, value, 'duration', it.end, it.duration || duration.value, it.durationType) }, 'Начало задачи')
  } else if (value > it.end) {
    save({ start: value, end: addDays(value, duration.value - 1) }, 'Начало задачи')
  } else {
    save({ start: value }, 'Начало задачи')
  }
}
function setEnd(value) {
  if (!value) return
  if (value < item.value.start) { ui.error('Окончание не может быть раньше начала'); return }
  save({ end: value, duration: item.value.durationType === 'working' ? editor.calendar.countWorkingDays(item.value.start, value) : diffDays(item.value.start, value) + 1 }, 'Окончание задачи')
}
function setEndMode(mode) {
  const it = item.value
  if (mode === it.endMode) return
  const fields = { endMode: mode }
  if (mode === 'duration') fields.duration = it.durationType === 'working' ? workingDays.value : duration.value
  save(fields, 'Режим завершения')
}
/** Тип дней раньше выбирался из списка — сохраняется только при смене, как у списка. */
function setDurationType(type) {
  if (type !== (item.value.durationType || 'calendar')) setDuration(item.value.duration || duration.value, type)
}
function setDuration(value, type = item.value.durationType) {
  const n = Math.max(1, Math.min(5000, parseInt(value, 10) || 1))
  save({ duration: n, durationType: type, end: resolveEnd(editor.calendar, item.value.start, 'duration', item.value.end, n, type) }, 'Длительность')
}
function setProgress(value) {
  progressDraft.value = null
  const n = Math.max(0, Math.min(100, parseInt(value, 10) || 0))
  save({ progress: n }, 'Прогресс')
}
function setOffset(value) {
  save({ offset: parseInt(value, 10) || 0 }, 'Временной сдвиг')
}
function setCompletedDate(value) {
  if (!value) return
  const it = item.value
  save({ completedDate: value, offset: diffDays(it.start, value) - diffDays(it.start, it.end) }, 'Дата завершения')
}
function setStatus(status) {
  actions.setStatus(item.value.key, status)
}
function setLineStyle(style) {
  if (style !== (item.value.lineStyle || 'dashed')) save({ lineStyle: style }, 'Стиль линии')
}
function setParent(key) {
  editor.moveItem(item.value.key, { parentKey: key || null })
}
async function addCategory() {
  const name = await prompt({ title: 'Новая категория', label: 'Название категории', confirmText: 'Добавить' })
  if (!name) return
  const categories = [...(editor.settings.categories || [])]
  if (!categories.includes(name)) editor.updatePlan({ settings: { categories: [...categories, name] } }, 'Новая категория')
  save({ category: name }, 'Категория')
}
function onCategory(event) {
  if (event.target.value === '__add__') {
    event.target.value = item.value.category || ''
    addCategory()
    return
  }
  save({ category: event.target.value || null }, 'Категория')
}
function hintText(hint) {
  if (hint.kind === 'parent') return `В пределах родительской задачи: ${formatDay(hint.from)} — ${formatDay(hint.to)}`
  if (hint.kind === 'start') return `По связи начало не раньше ${formatDay(hint.value)}`
  return `По связи окончание не раньше ${formatDay(hint.value)}`
}

function duplicate() {
  const key = editor.duplicateItem(item.value.key)
  if (key) editor.select(key, { openPanel: true })
}
</script>


<template>
  <aside class="panel" aria-label="Карточка строки">
    <header class="panel-head">
      <span class="type-badge" :class="`t-${item.type}`">
        <AppIcon :name="item.type === 'group' ? 'folder' : item.type === 'milestone' ? 'diamond' : 'gantt'" :size="13" /> {{ typeLabel }}
      </span>
      <span v-if="item.type === 'task'" class="status-pill" :style="{ '--c': status.color, '--t': status.text }"><span class="status-pill-dot" />{{ status.label }}</span>
      <span class="spacer" />
      <button v-if="!readonly" class="btn btn-ghost btn-icon btn-sm" type="button" title="Дублировать (Ctrl+D)" @click="duplicate"><AppIcon name="copy" :size="15" /></button>
      <button v-if="!readonly" class="btn btn-ghost btn-icon btn-sm btn-text-danger" type="button" title="Удалить (Delete)" @click="actions.deleteRows([item.key])"><AppIcon name="trash" :size="15" /></button>
      <HelpLink :topic="item.type === 'milestone' ? 'milestones' : item.type === 'group' ? 'groups' : 'item-card'" />
      <button class="btn btn-ghost btn-icon btn-sm" type="button" title="Закрыть (Esc)" @click="emit('close')"><AppIcon name="close" /></button>
    </header>

    <div class="title-wrap">
      <textarea
        v-model="drafts.name" class="title-input" rows="1" :readonly="readonly" maxlength="5000" placeholder="Название"
        data-testid="panel-name"
        @focus="focused = 'name'" @blur="commitText('name')" @keydown.enter.prevent="$event.target.blur()" @keydown.esc.stop.prevent="cancelText('name', $event)"
      />
      <div class="title-meta">
        <span v-if="item.type === 'task'"><AppIcon name="calendar" :size="12" /> {{ formatDayNoYear(item.start) }} — {{ formatDay(item.end) }} · {{ duration }} дн.</span>
        <span v-else-if="item.type === 'milestone'"><AppIcon name="calendar" :size="12" /> {{ formatDay(item.date) }}</span>
        <span v-else-if="span"><AppIcon name="calendar" :size="12" /> {{ formatDayNoYear(span.start) }} — {{ formatDay(span.end) }}</span>
        <span v-if="parentName" class="crumb" :title="`Входит в «${parentName}»`"><AppIcon name="folder" :size="12" /> <span class="ellipsis">{{ parentName }}</span></span>
      </div>
    </div>

    <div class="tabs-wrap">
      <SegmentedControl class="tabs" kind="tabs" block fit="content" aria-label="Разделы карточки" :model-value="tab" :options="tabs" @update:model-value="(value) => (tab = value)" />
    </div>

    <div v-if="tab === 'main'" class="panel-body scroll-thin">
      <!-- Задача -->
      <template v-if="item.type === 'task'">
        <section class="sec">
          <div class="sec-head"><span class="sec-title">Статус</span></div>
          <SegmentedControl class="statuses" :columns="2" aria-label="Статус" :model-value="item.status || 'new'" :options="STATUS_OPTIONS" :disabled="readonly" @update:model-value="setStatus" />
          <label v-if="item.status === 'done'" class="field done-card">
            <span class="field-label"><AppIcon name="calendarCheck" :size="13" /> Дата завершения</span>
            <input class="input" type="date" :value="item.completedDate || item.end" :min="item.start" :disabled="readonly" @change="setCompletedDate($event.target.value)" />
            <span class="field-hint">Сдвиг считается от планового окончания: {{ item.offset > 0 ? '+' : '' }}{{ item.offset || 0 }} дн.</span>
          </label>
        </section>
        <JiraIssueCard v-if="jira.keyFor(item)" :item="item" />

        <section class="sec">
          <div class="sec-head">
            <span class="sec-title">Сроки</span>
            <SegmentedControl size="sm" aria-label="Окончание задаётся" :model-value="item.endMode === 'duration' ? 'duration' : 'date'" :options="END_MODES" :disabled="readonly" @update:model-value="setEndMode" />
          </div>
          <div class="sec-card">
            <div class="form-grid">
              <label class="field">
                <span class="field-label">Начало</span>
                <input class="input" type="date" :value="item.start" :min="constraints?.minStart || undefined" :max="constraints?.maxStart || undefined" :disabled="readonly" data-testid="panel-start" @change="setStart($event.target.value)" />
              </label>
              <label v-if="item.endMode !== 'duration'" class="field">
                <span class="field-label">Окончание</span>
                <input class="input" type="date" :value="item.end" :min="item.start" :max="constraints?.maxEnd || undefined" :disabled="readonly" data-testid="panel-end" @change="setEnd($event.target.value)" />
              </label>
              <label v-else class="field">
                <span class="field-label">Дней</span>
                <input class="input" type="number" min="1" max="5000" :value="item.duration || duration" :disabled="readonly" @change="setDuration($event.target.value)" />
              </label>
            </div>
            <div v-if="item.endMode === 'duration'" class="field">
              <span class="field-label">Тип дней</span>
              <SegmentedControl block size="sm" aria-label="Тип дней" :model-value="item.durationType || 'calendar'" :options="DURATION_TYPES" :disabled="readonly" @update:model-value="setDurationType" />
            </div>
            <div class="facts">
              <span v-if="item.endMode === 'duration'" class="fact is-strong"><AppIcon name="flag" :size="12" /> Окончание: {{ formatDay(item.end) }}</span>
              <span class="fact">{{ duration }} кал. дн.</span>
              <span class="fact">{{ workingDays }} раб. дн.</span>
            </div>
            <ul v-if="constraints?.hints.length" class="hints">
              <li v-for="(hint, i) in constraints.hints" :key="i"><AppIcon name="link" :size="12" /> {{ hintText(hint) }}</li>
            </ul>
          </div>
        </section>

        <section class="sec">
          <div class="sec-head"><span class="sec-title">Ход работ</span></div>
          <div class="sec-card">
            <label class="field">
              <span class="field-label">Прогресс</span>
              <div class="progress-input">
                <input
                  class="range" type="range" min="0" max="100" step="5" :value="item.progress || 0" :disabled="readonly" :style="{ '--p': progressShown + '%' }"
                  @input="progressDraft = $event.target.value" @change="setProgress($event.target.value)"
                />
                <span class="number-suffix">
                  <input class="input input-sm" type="number" min="0" max="100" :value="progressShown" :disabled="readonly" aria-label="Прогресс в процентах" @change="setProgress($event.target.value)" />
                  <span class="suffix" aria-hidden="true">%</span>
                </span>
              </div>
            </label>
            <label class="field">
              <span class="field-label">Временной сдвиг, дн.</span>
              <div class="offset-input">
                <input class="input" type="number" :value="item.offset || 0" :disabled="readonly" @change="setOffset($event.target.value)" @keydown.esc.stop="$event.target.value = item.offset || 0; $event.target.blur()" />
                <span v-if="item.offset" class="offset-pill" :class="item.offset > 0 ? 'is-late' : 'is-early'">{{ item.offset > 0 ? 'просрочка' : 'ранняя сдача' }} · фактически до {{ formatDay(addDays(item.end, item.offset)) }}</span>
                <span v-else class="field-hint">+ просрочка, − ранняя сдача</span>
              </div>
            </label>
          </div>
        </section>

        <section class="sec">
          <div class="sec-head"><span class="sec-title">Исполнитель и связь</span></div>
          <div class="sec-card">
            <label class="field">
              <span class="field-label">Исполнитель</span>
              <span class="with-avatar">
                <UserAvatar v-if="assignee" class="avatar-adorn" :user="assigneeUser" :color="resolveColor(assignee.color).color" :size="22" />
                <span v-else class="avatar-adorn avatar-empty"><AppIcon name="user" :size="13" /></span>
                <select class="select" :value="item.assigneeKey || ''" :disabled="readonly" @change="save({ assigneeKey: $event.target.value || null }, 'Исполнитель')">
                  <option value="">— не назначен —</option>
                  <option v-for="a in editor.assigneeList" :key="a.key" :value="a.key">{{ a.name }}{{ a.role ? ' · ' + a.role : '' }}</option>
                </select>
              </span>
            </label>
            <div class="field">
              <span class="field-label">Предшественник</span>
              <input v-if="!readonly && dependencyOptions.length > LONG_LIST" v-model="dependencyQuery" class="input input-sm list-search" type="search" placeholder="Найти задачу или веху…" aria-label="Поиск предшественника" />
              <select class="select" :value="item.dependencyKey || ''" :disabled="readonly" aria-label="Предшественник" @change="save({ dependencyKey: $event.target.value || null }, 'Предшественник'); dependencyQuery = ''">
                <option value="">— нет —</option>
                <option v-for="o in shownDependencyOptions" :key="o.key" :value="o.key">{{ o.label }}</option>
              </select>
              <span v-if="dependencyQuery && shownDependencyOptions.length <= (item.dependencyKey ? 1 : 0)" class="field-hint">Ничего не найдено</span>
            </div>
            <div v-if="item.dependencyKey" class="form-grid">
              <label class="field">
                <span class="field-label">Тип связи</span>
                <select class="select" :value="item.dependencyType || 'FS'" :disabled="readonly" @change="save({ dependencyType: $event.target.value }, 'Тип связи')">
                  <option v-for="t in DEPENDENCY_TYPES" :key="t.key" :value="t.key">{{ t.label }}</option>
                </select>
              </label>
              <label class="field">
                <span class="field-label">Лаг, дн.</span>
                <input class="input" type="number" :value="item.dependencyLag || 0" :disabled="readonly" @change="save({ dependencyLag: parseInt($event.target.value, 10) || 0 }, 'Лаг связи')" />
                <span class="field-hint">+ задержка, − опережение</span>
              </label>
            </div>
          </div>
        </section>
      </template>

      <!-- Веха -->
      <template v-if="item.type === 'milestone'">
        <!-- Веха, связанная с задачей Jira: срок задачи — дата вехи -->
        <JiraIssueCard v-if="jira.keyFor(item)" :item="item" />
        <section class="sec">
          <div class="sec-head"><span class="sec-title">Параметры вехи</span></div>
          <div class="sec-card">
            <label class="field">
              <span class="field-label">Дата</span>
              <input class="input" type="date" :value="item.date" :disabled="readonly" @change="$event.target.value && save({ date: $event.target.value }, 'Дата вехи')" />
            </label>
            <div class="field">
              <span class="field-label">Линия</span>
              <SegmentedControl block stacked aria-label="Линия" :model-value="item.lineStyle || 'dashed'" :options="LINE_STYLES" :disabled="readonly" @update:model-value="setLineStyle" />
            </div>
            <label class="field">
              <span class="field-label">Подпись над линией</span>
              <input v-model="drafts.comment" class="input" :readonly="readonly" maxlength="500" placeholder="По умолчанию — название вехи"
                     @focus="focused = 'comment'" @blur="commitText('comment')" @keydown.enter.prevent="$event.target.blur()" @keydown.esc.stop.prevent="cancelText('comment', $event)" />
            </label>
            <label class="field">
              <span class="field-label">Исполнитель</span>
              <span class="with-avatar">
                <UserAvatar v-if="assignee" class="avatar-adorn" :user="assigneeUser" :color="resolveColor(assignee.color).color" :size="22" />
                <span v-else class="avatar-adorn avatar-empty"><AppIcon name="user" :size="13" /></span>
                <select class="select" :value="item.assigneeKey || ''" :disabled="readonly" @change="save({ assigneeKey: $event.target.value || null }, 'Исполнитель')">
                  <option value="">— не назначен —</option>
                  <option v-for="a in editor.assigneeList" :key="a.key" :value="a.key">{{ a.name }}</option>
                </select>
              </span>
            </label>
          </div>
        </section>
      </template>

      <!-- Группа и задача с вложенными: сроки и ход работ по вложенным -->
      <section v-if="item.type === 'group' || span" class="sec">
        <div class="sec-head"><span class="sec-title">{{ item.type === 'group' ? 'Сводка' : 'Вложенные задачи' }}</span></div>
        <div v-if="span" class="span-card">
          <div class="span-cell"><span class="muted small">Начало</span><strong>{{ formatDay(span.start) }}</strong></div>
          <div class="span-cell"><span class="muted small">Окончание</span><strong>{{ formatDay(span.end) }}</strong></div>
          <div v-if="span.offset" class="span-cell"><span class="muted small">Со сдвигами</span><strong :class="span.offset > 0 ? 'late' : 'early'">{{ formatDay(span.shiftedEnd) }} ({{ span.offset > 0 ? '+' : '' }}{{ span.offset }} дн.)</strong></div>
          <div class="span-cell"><span class="muted small">Задач</span><strong>{{ span.done }} / {{ span.count }} · {{ span.progress }}%</strong></div>
          <div class="span-progress" :title="`Прогресс: ${span.progress}%`"><span :style="{ width: span.progress + '%' }" /></div>
        </div>
        <p v-else class="empty-note muted small">В группе пока нет задач — сроки появятся автоматически.</p>
      </section>

      <section class="sec">
        <div class="sec-head"><span class="sec-title">{{ item.type === 'task' ? 'Подробности' : 'В плане' }}</span></div>
        <div class="sec-card">
          <div class="field">
            <span class="field-label">Входит в</span>
            <input v-if="!readonly && parentOptions.length > LONG_LIST" v-model="parentQuery" class="input input-sm list-search" type="search" placeholder="Найти группу или задачу…" aria-label="Поиск родителя" />
            <select class="select" :value="item.parentKey || ''" :disabled="readonly" aria-label="Входит в" @change="setParent($event.target.value); parentQuery = ''">
              <option value="">— верхний уровень —</option>
              <option v-for="o in shownParentOptions" :key="o.key" :value="o.key">{{ o.label }}</option>
            </select>
          </div>

          <div v-if="item.type === 'task'" class="form-grid">
            <label class="field">
              <span class="field-label">Категория</span>
              <select class="select" :value="item.category || ''" :disabled="readonly" @change="onCategory">
                <option value="">— без категории —</option>
                <option v-for="c in editor.categories" :key="c" :value="c">{{ c }}</option>
                <option v-if="!readonly" value="__add__">+ Новая категория…</option>
              </select>
            </label>
            <label class="field">
              <span class="field-label">Текст на полосе</span>
              <input v-model="drafts.barText" class="input" :readonly="readonly" maxlength="500" placeholder="По умолчанию — даты"
                     @focus="focused = 'barText'" @blur="commitText('barText')" @keydown.enter.prevent="$event.target.blur()" @keydown.esc.stop.prevent="cancelText('barText', $event)" />
            </label>
          </div>

          <div class="field">
            <span class="field-label">{{ item.type === 'milestone' ? 'Цвет линии' : 'Цвет' }}</span>
            <ColorPicker
              :model-value="item.type === 'milestone' ? (item.lineColor || item.color) : item.color" :disabled="readonly"
              @update:model-value="(c) => save(item.type === 'milestone' ? { lineColor: c, color: c } : { color: c }, 'Цвет')"
            />
          </div>
        </div>
      </section>

      <!-- Задача, веха (срок) или группа (эпик) могут быть связаны с задачей Jira -->
      <section class="sec">
        <div class="sec-head"><span class="sec-title">Jira</span></div>
        <div class="sec-card">
          <label class="field">
            <span class="field-label">Задача в Jira</span>
            <span class="with-action">
              <input v-model="drafts.jiraUrl" class="input" :readonly="readonly" maxlength="2000" data-testid="item-jira" autocomplete="off"
                     :placeholder="jira.state.connected ? 'ABC-123, ссылка или слова из названия' : 'https://jira.example.com/browse/PROJ-123'"
                     role="combobox" :aria-expanded="suggestions.length > 0" aria-autocomplete="list"
                     @focus="focused = 'jiraUrl'" @blur="hideSuggestions(); commitText('jiraUrl')" @keydown.enter.prevent="$event.target.blur()" @keydown.esc.stop.prevent="suggestions = []; cancelText('jiraUrl', $event)" />
              <a v-if="item.jiraUrl" class="btn btn-icon" :href="item.jiraUrl" target="_blank" rel="noopener noreferrer" title="Открыть в Jira"><AppIcon name="external" :size="15" /></a>
            </span>
            <span v-if="suggestBusy && !suggestions.length" class="field-hint">Ищем в Jira…</span>
          </label>
          <ul v-if="suggestions.length" class="suggest scroll-thin" role="listbox" data-testid="item-jira-suggest">
            <li v-for="issue in suggestions" :key="issue.key" role="option" @mousedown.prevent="pickSuggestion(issue)">
              <span class="s-key">{{ issue.key }}</span>
              <span class="s-summary ellipsis">{{ issue.summary }}</span>
              <span class="jira-status" :class="`cat-${issue.statusCategory}`">{{ issue.status }}</span>
            </li>
          </ul>
          <p v-if="foreignHost" class="foreign small" data-testid="item-jira-foreign">
            <AppIcon name="warning" :size="13" /> Ссылка ведёт на {{ foreignHost }}, а подключена Jira {{ jira.state.baseUrl }} — данные задачи не загружаются.
            Если это та же Jira, администратор добавит адрес в «Интеграции → Jira → Другие адреса этой Jira».
          </p>
        </div>
      </section>

      <section v-if="podeluTasks?.state.enabled && rowPodeluTasks.length" class="sec" data-testid="item-podelu">
        <div class="sec-head"><span class="sec-title">Задачи в «По Делу»</span><span class="sec-count">{{ rowPodeluTasks.length }}</span></div>
        <div class="sec-card">
          <PodeluTaskList :tasks="rowPodeluTasks" :today="editor.state.today" compact />
        </div>
      </section>

      <p v-if="item.updatedAt" class="updated muted small">
        <AppIcon name="clock" :size="12" /> Изменено {{ formatDateTime(item.updatedAt) }}<template v-if="updatedBy"> · {{ updatedBy }}</template>
      </p>
    </div>

    <ItemComments v-else-if="tab === 'comments'" class="panel-body" :item="item" />
    <ItemHistory v-else class="panel-body" :item="item" />
  </aside>
</template>

<style scoped>
.panel { width: 420px; max-width: 100%; border-left: 1px solid var(--color-border); background: var(--color-surface); display: flex; flex-direction: column; min-height: 0; box-shadow: -8px 0 24px rgba(20, 25, 40, 0.06); z-index: 10; animation: slide-in 0.18s cubic-bezier(0.32, 0.72, 0, 1); }
@keyframes slide-in { from { transform: translateX(16px); opacity: 0; } to { transform: none; opacity: 1; } }

/* Шапка (тип, статус, действия), название и вкладки не прокручиваются вместе с полями */
.panel-head { display: flex; align-items: center; gap: 6px; padding: 12px 12px 4px 18px; }
.type-badge { display: inline-flex; align-items: center; gap: 5px; height: 24px; padding: 0 9px 0 7px; border-radius: 999px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; background: var(--color-primary-soft); color: var(--color-primary-dark); white-space: nowrap; }
.type-badge.t-milestone { background: #dcf5f1; color: #0f766e; }
.type-badge.t-group { background: #eef1f7; color: #475569; }
.status-pill { display: inline-flex; align-items: center; gap: 6px; height: 24px; padding: 0 10px; border-radius: 999px; background: color-mix(in srgb, var(--c) 13%, white); color: var(--t); font-size: 11.5px; font-weight: 600; white-space: nowrap; min-width: 0; overflow: hidden; }
.status-pill-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--c); flex-shrink: 0; }
.title-wrap { padding: 6px 18px 12px; }
.title-input { display: block; width: calc(100% + 8px); margin-left: -8px; padding: 4px 8px; border: 1px solid transparent; border-radius: 8px; font-family: inherit; font-size: 18px; font-weight: 700; line-height: 1.3; letter-spacing: -0.01em; color: var(--color-text); background: transparent; resize: none; field-sizing: content; min-height: 34px; transition: border-color 0.15s, box-shadow 0.15s, background 0.15s; }
.title-input::placeholder { color: #b0b7c6; }
.title-input:hover:not([readonly]) { border-color: var(--color-border); background: var(--color-surface-2); }
.title-input:focus { outline: none; border-color: var(--color-primary); box-shadow: var(--focus-ring); background: #fff; }
.title-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 14px; margin-top: 4px; font-size: 12px; color: var(--color-text-muted); }
.title-meta:empty { display: none; }
.title-meta > span { display: inline-flex; align-items: center; gap: 5px; min-width: 0; max-width: 100%; }
.crumb { overflow: hidden; }
.tabs-wrap { padding: 0 18px 12px; border-bottom: 1px solid var(--color-border); }

.panel-body { flex: 1; overflow-y: auto; padding: 16px 18px 24px; display: flex; flex-direction: column; gap: 20px; }

/* Разделы: заголовок прописными, поля — в мягкой карточке */
.sec { display: flex; flex-direction: column; gap: 8px; }
.sec-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-height: 26px; }
.sec-title { font-size: 11px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--color-text-muted); }
.sec-card { display: flex; flex-direction: column; gap: 14px; padding: 14px; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-surface-2); }

.done-card { padding: 12px; border-radius: 12px; background: var(--color-success-soft); border: 1px solid #c4ead5; }
.done-card .field-label { display: inline-flex; align-items: center; gap: 6px; color: #1f7a4c; }

.facts { display: flex; flex-wrap: wrap; gap: 6px; }
.fact { display: inline-flex; align-items: center; gap: 5px; height: 24px; padding: 0 9px; border-radius: 999px; background: var(--color-surface); box-shadow: inset 0 0 0 1px var(--color-border); font-size: 11.5px; color: var(--color-text-secondary); white-space: nowrap; }
.fact.is-strong { color: var(--color-text); font-weight: 600; }
.list-search { margin-bottom: 2px; }
.hints { list-style: none; margin: 0; padding: 8px 10px; background: #eef3ff; border-radius: 8px; display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: #2b4db5; }
.hints li { display: flex; align-items: center; gap: 6px; }

/* Прогресс: ползунок с заливкой и число с «%» */
.progress-input { display: flex; align-items: center; gap: 12px; }
.range { flex: 1; min-width: 0; height: 6px; margin: 0; border-radius: 999px; appearance: none; -webkit-appearance: none; cursor: pointer; background: linear-gradient(to right, var(--color-primary) var(--p), #dfe4ee var(--p)); }
.range::-webkit-slider-thumb { -webkit-appearance: none; appearance: none; width: 18px; height: 18px; border-radius: 50%; background: #fff; border: 2px solid var(--color-primary); box-shadow: 0 1px 3px rgba(20, 25, 40, 0.2); transition: transform 0.12s; }
.range::-moz-range-thumb { width: 14px; height: 14px; border-radius: 50%; background: #fff; border: 2px solid var(--color-primary); box-shadow: 0 1px 3px rgba(20, 25, 40, 0.2); }
.range:hover:not(:disabled)::-webkit-slider-thumb { transform: scale(1.1); }
.range:focus-visible { outline: none; }
.range:focus-visible::-webkit-slider-thumb { box-shadow: var(--focus-ring); }
.range:disabled { cursor: not-allowed; background: linear-gradient(to right, #9aa6c4 var(--p), #e6e9f0 var(--p)); }
.range:disabled::-webkit-slider-thumb { border-color: #9aa6c4; }
.number-suffix { position: relative; flex-shrink: 0; width: 72px; }
.number-suffix .input { padding-right: 22px; text-align: right; }
.suffix { position: absolute; right: 9px; top: 50%; transform: translateY(-50%); font-size: 12px; color: var(--color-text-muted); pointer-events: none; }
.offset-input { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.offset-input .input { width: 96px; flex-shrink: 0; }
.offset-pill { display: inline-flex; align-items: center; min-height: 24px; padding: 2px 10px; border-radius: 999px; font-size: 11.5px; font-weight: 600; }
.offset-pill.is-late { background: var(--color-danger-soft); color: #c62933; }
.offset-pill.is-early { background: var(--color-success-soft); color: #1f7a4c; }

/* Исполнитель: аватар на цвете исполнителя внутри списка */
.with-avatar { position: relative; display: block; }
.with-avatar .select { padding-left: 40px; }
.avatar-adorn { position: absolute; left: 8px; top: 50%; transform: translateY(-50%); pointer-events: none; }
.avatar-empty { width: 22px; height: 22px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; background: #eef1f7; color: var(--color-text-muted); }

/* Сводка группы */
.span-card { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 12px; padding: 14px; background: var(--color-surface-2); border: 1px solid var(--color-border); border-radius: 12px; }
.span-cell { display: flex; flex-direction: column; gap: 2px; }
.span-cell strong { font-size: 13px; }
.span-progress { grid-column: 1 / -1; height: 6px; border-radius: 999px; background: #e3e7ef; overflow: hidden; }
.span-progress span { display: block; height: 100%; border-radius: inherit; background: var(--color-success); }
.empty-note { margin: 0; padding: 12px 14px; border: 1px dashed var(--color-border-strong); border-radius: 12px; }
.late { color: #c62933; }
.early { color: #1f7a4c; }
/* Палитра в карточке компактнее — 13 образцов умещаются в одну строку */
.sec-card .color-picker { --swatch-size: 22px; gap: 5px; }
.with-action { display: flex; gap: 6px; }
.with-action .input { flex: 1; min-width: 0; }
.updated { display: flex; align-items: center; gap: 6px; margin: -4px 0 0; }

/* Ноутбук и планшет: карточка открывается поверх диаграммы, а не сжимает её */
@media (max-width: 1279px) {
  .panel { position: absolute; inset: 0 0 0 auto; width: min(440px, 100%); box-shadow: -12px 0 32px rgba(20, 25, 40, 0.18); }
}
/* Телефон: карточка — на весь экран поверх панели инструментов */
@media (max-width: 768px) {
  .panel { position: fixed; inset: 0; z-index: 150; width: 100%; border-left: 0; padding-top: env(safe-area-inset-top); }
  .panel-head { padding: 6px 8px 2px 16px; gap: 2px; }
  .title-wrap { padding: 4px 16px 10px; }
  .tabs-wrap { padding: 0 16px 10px; }
  .panel-body { padding: 14px 16px calc(24px + env(safe-area-inset-bottom)); }
  .sec-card { padding: 12px; }
  .number-suffix { width: 84px; }
  .range::-webkit-slider-thumb { width: 24px; height: 24px; }
}
.sec-count { margin-left: 6px; font-size: 11px; font-weight: 700; padding: 0 7px; border-radius: 999px; background: #dcfff1; color: #216e4e; }
.suggest { list-style: none; margin: 4px 0 0; padding: 4px; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface); box-shadow: var(--shadow-2, 0 8px 24px rgba(15, 23, 42, 0.12)); max-height: 240px; overflow: auto; }
.suggest li { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 8px; align-items: center; padding: 6px 8px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.suggest li:hover { background: var(--color-hover); }
.s-key { font-weight: 700; font-size: 12px; color: #0052cc; }
.foreign { display: flex; gap: 6px; align-items: flex-start; margin: 8px 0 0; color: #8a5a00; background: var(--color-warning-soft); border-radius: var(--radius-sm); padding: 6px 8px; line-height: 1.45; }
.foreign :deep(svg) { flex-shrink: 0; margin-top: 2px; }
</style>
