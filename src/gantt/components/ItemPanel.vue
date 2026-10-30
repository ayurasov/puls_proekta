<script setup>
import { computed, inject, reactive, ref, watch } from 'vue'
import AppIcon from '../../components/common/AppIcon.vue'
import HelpLink from '../../components/common/HelpLink.vue'
import ColorPicker from './ColorPicker.vue'
import ItemComments from './ItemComments.vue'
import ItemHistory from './ItemHistory.vue'
import JiraIssueCard from './JiraIssueCard.vue'
import { issueKeyFromUrl, jiraLinkFromInput } from '../jira'
import { STATUSES, DEPENDENCY_TYPES } from '../domain/colors'
import { addDays, diffDays, formatDay } from '../domain/dates'
import { resolveEnd } from '../domain/calendar'
import { dependencyConstraints, dependentKeys } from '../domain/dependencies'
import { descendantKeys, groupSpan } from '../domain/tree'
import { useDialogs } from '../../composables/useDialogs'
import { useUiStore } from '../../stores/ui'
import { formatDateTime } from '../../utils/format'

const props = defineProps({ item: { type: Object, required: true } })
const emit = defineEmits(['close'])
const editor = inject('gantt')
const actions = inject('ganttActions')
const jira = inject('jira')
const { prompt } = useDialogs()
const ui = useUiStore()
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
      const key = issueKeyFromUrl(link.url, jira.state.baseUrl)
      if (key) jira.fetchKeys([key])
    }
    return
  }
  if ((item.value[field] || '') !== value) save({ [field]: value || null })
}

const typeLabel = computed(() => ({ task: 'Задача', milestone: 'Веха', group: 'Группа' })[item.value.type])
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
function setDuration(value, type = item.value.durationType) {
  const n = Math.max(1, Math.min(5000, parseInt(value, 10) || 1))
  save({ duration: n, durationType: type, end: resolveEnd(editor.calendar, item.value.start, 'duration', item.value.end, n, type) }, 'Длительность')
}
function setProgress(value) {
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
    </div>

    <div class="tabs segmented">
      <button type="button" :class="{ 'is-active': tab === 'main' }" @click="tab = 'main'">Основное</button>
      <button v-if="item.type === 'task'" type="button" :class="{ 'is-active': tab === 'comments' }" @click="tab = 'comments'">Комментарии<span v-if="item.commentCount" class="tab-count">{{ item.commentCount }}</span></button>
      <button type="button" :class="{ 'is-active': tab === 'history' }" @click="tab = 'history'">История</button>
    </div>

    <div v-if="tab === 'main'" class="panel-body scroll-thin">
      <!-- Задача -->
      <template v-if="item.type === 'task'">
        <div class="field">
          <span class="field-label">Статус</span>
          <div class="segmented statuses" role="radiogroup" aria-label="Статус">
            <button v-for="s in STATUSES" :key="s.key" type="button" class="status-option" :class="{ 'is-active': (item.status || 'new') === s.key }"
                    role="radio" :aria-checked="(item.status || 'new') === s.key" :style="{ '--c': s.color }" :disabled="readonly" @click="setStatus(s.key)">
              <span class="status-dot" :style="{ background: s.color }" /> {{ s.label }}
            </button>
          </div>
        </div>
        <label v-if="item.status === 'done'" class="field">
          <span class="field-label">Дата завершения</span>
          <input class="input" type="date" :value="item.completedDate || item.end" :min="item.start" :disabled="readonly" @change="setCompletedDate($event.target.value)" />
          <span class="field-hint">Сдвиг считается от планового окончания: {{ item.offset > 0 ? '+' : '' }}{{ item.offset || 0 }} дн.</span>
        </label>
        <JiraIssueCard v-if="jira.keyFor(item)" :item="item" />

        <div class="field">
          <div class="field-head">
            <span class="field-label">Сроки</span>
            <div class="segmented mini" role="group" aria-label="Окончание задаётся">
              <button type="button" :class="{ 'is-active': item.endMode !== 'duration' }" :disabled="readonly" title="Окончание — датой" @click="setEndMode('date')">По дате</button>
              <button type="button" :class="{ 'is-active': item.endMode === 'duration' }" :disabled="readonly" title="Окончание считается от начала по числу дней" @click="setEndMode('duration')">По длительности</button>
            </div>
          </div>
          <div class="form-grid">
            <label class="field">
              <span class="field-sublabel">Начало</span>
              <input class="input" type="date" :value="item.start" :min="constraints?.minStart || undefined" :max="constraints?.maxStart || undefined" :disabled="readonly" data-testid="panel-start" @change="setStart($event.target.value)" />
            </label>
            <label v-if="item.endMode !== 'duration'" class="field">
              <span class="field-sublabel">Окончание</span>
              <input class="input" type="date" :value="item.end" :min="item.start" :max="constraints?.maxEnd || undefined" :disabled="readonly" data-testid="panel-end" @change="setEnd($event.target.value)" />
            </label>
            <label v-else class="field">
              <span class="field-sublabel">Дней</span>
              <input class="input" type="number" min="1" max="5000" :value="item.duration || duration" :disabled="readonly" @change="setDuration($event.target.value)" />
            </label>
          </div>
          <label v-if="item.endMode === 'duration'" class="field">
            <span class="field-sublabel">Тип дней</span>
            <select class="select" :value="item.durationType || 'calendar'" :disabled="readonly" @change="setDuration(item.duration || duration, $event.target.value)">
              <option value="calendar">Календарные</option>
              <option value="working">Рабочие (производственный календарь)</option>
            </select>
          </label>
          <span class="field-hint">
            <template v-if="item.endMode === 'duration'">Окончание: {{ formatDay(item.end) }} · </template>
            {{ duration }} кал. дн. · {{ workingDays }} раб. дн.
          </span>
        </div>
        <ul v-if="constraints?.hints.length" class="hints">
          <li v-for="(hint, i) in constraints.hints" :key="i"><AppIcon name="link" :size="12" /> {{ hintText(hint) }}</li>
        </ul>

        <label class="field">
          <span class="field-label">Прогресс</span>
          <div class="progress-input">
            <input type="range" min="0" max="100" step="5" :value="item.progress || 0" :disabled="readonly" @change="setProgress($event.target.value)" />
            <input class="input input-sm" type="number" min="0" max="100" :value="item.progress || 0" :disabled="readonly" @change="setProgress($event.target.value)" />
            <span class="muted small">%</span>
          </div>
        </label>
        <label class="field">
          <span class="field-label">Временной сдвиг, дн.</span>
          <div class="offset-input">
            <input class="input" type="number" :value="item.offset || 0" :disabled="readonly" @change="setOffset($event.target.value)" @keydown.esc.stop="$event.target.value = item.offset || 0; $event.target.blur()" />
            <span class="field-hint">{{ item.offset ? `${item.offset > 0 ? 'просрочка' : 'ранняя сдача'} · фактически до ${formatDay(addDays(item.end, item.offset))}` : '+ просрочка, − ранняя сдача' }}</span>
          </div>
        </label>

        <label class="field">
          <span class="field-label">Исполнитель</span>
          <select class="select" :value="item.assigneeKey || ''" :disabled="readonly" @change="save({ assigneeKey: $event.target.value || null }, 'Исполнитель')">
            <option value="">— не назначен —</option>
            <option v-for="a in editor.assigneeList" :key="a.key" :value="a.key">{{ a.name }}{{ a.role ? ' · ' + a.role : '' }}</option>
          </select>
        </label>

        <div class="section-label">Связь</div>
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
      </template>

      <!-- Веха -->
      <template v-if="item.type === 'milestone'">
        <div class="form-grid">
          <label class="field">
            <span class="field-label">Дата</span>
            <input class="input" type="date" :value="item.date" :disabled="readonly" @change="$event.target.value && save({ date: $event.target.value }, 'Дата вехи')" />
          </label>
          <label class="field">
            <span class="field-label">Линия</span>
            <select class="select" :value="item.lineStyle || 'dashed'" :disabled="readonly" @change="save({ lineStyle: $event.target.value }, 'Стиль линии')">
              <option value="dashed">Пунктирная</option>
              <option value="solid">Сплошная</option>
              <option value="dotted">Точечная</option>
            </select>
          </label>
        </div>
        <label class="field">
          <span class="field-label">Подпись над линией</span>
          <input v-model="drafts.comment" class="input" :readonly="readonly" maxlength="500" placeholder="По умолчанию — название вехи"
                 @focus="focused = 'comment'" @blur="commitText('comment')" @keydown.enter.prevent="$event.target.blur()" @keydown.esc.stop.prevent="cancelText('comment', $event)" />
        </label>
        <label class="field">
          <span class="field-label">Исполнитель</span>
          <select class="select" :value="item.assigneeKey || ''" :disabled="readonly" @change="save({ assigneeKey: $event.target.value || null }, 'Исполнитель')">
            <option value="">— не назначен —</option>
            <option v-for="a in editor.assigneeList" :key="a.key" :value="a.key">{{ a.name }}</option>
          </select>
        </label>
      </template>

      <!-- Группа -->
      <template v-if="item.type === 'group' || span">
        <div v-if="span" class="span-card">
          <div><span class="muted small">Начало</span><strong>{{ formatDay(span.start) }}</strong></div>
          <div><span class="muted small">Окончание</span><strong>{{ formatDay(span.end) }}</strong></div>
          <div v-if="span.offset"><span class="muted small">Со сдвигами</span><strong :class="span.offset > 0 ? 'late' : 'early'">{{ formatDay(span.shiftedEnd) }} ({{ span.offset > 0 ? '+' : '' }}{{ span.offset }} дн.)</strong></div>
          <div><span class="muted small">Задач</span><strong>{{ span.done }} / {{ span.count }} · {{ span.progress }}%</strong></div>
        </div>
        <p v-else-if="item.type === 'group'" class="muted small">В группе пока нет задач — сроки появятся автоматически.</p>
      </template>

      <div class="field">
        <span class="field-label">Входит в</span>
        <input v-if="!readonly && parentOptions.length > LONG_LIST" v-model="parentQuery" class="input input-sm list-search" type="search" placeholder="Найти группу или задачу…" aria-label="Поиск родителя" />
        <select class="select" :value="item.parentKey || ''" :disabled="readonly" aria-label="Входит в" @change="setParent($event.target.value); parentQuery = ''">
          <option value="">— верхний уровень —</option>
          <option v-for="o in shownParentOptions" :key="o.key" :value="o.key">{{ o.label }}</option>
        </select>
      </div>

      <template v-if="item.type === 'task'">
        <div class="form-grid">
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
        <label class="field">
          <span class="field-label">Задача в Jira</span>
          <span class="with-action">
            <input v-model="drafts.jiraUrl" class="input" :readonly="readonly" maxlength="2000" data-testid="item-jira"
                   :placeholder="jira.state.connected ? 'ABC-123 или ссылка на задачу' : 'https://jira.example.com/browse/PROJ-123'"
                   @focus="focused = 'jiraUrl'" @blur="commitText('jiraUrl')" @keydown.enter.prevent="$event.target.blur()" @keydown.esc.stop.prevent="cancelText('jiraUrl', $event)" />
            <a v-if="item.jiraUrl" class="btn btn-icon" :href="item.jiraUrl" target="_blank" rel="noopener noreferrer" title="Открыть в Jira"><AppIcon name="external" :size="15" /></a>
          </span>
        </label>
      </template>

      <div class="field">
        <span class="field-label">{{ item.type === 'milestone' ? 'Цвет линии' : 'Цвет' }}</span>
        <ColorPicker
          :model-value="item.type === 'milestone' ? (item.lineColor || item.color) : item.color" :disabled="readonly"
          @update:model-value="(c) => save(item.type === 'milestone' ? { lineColor: c, color: c } : { color: c }, 'Цвет')"
        />
      </div>

      <p v-if="item.updatedAt" class="updated muted small">
        Изменено {{ formatDateTime(item.updatedAt) }}<template v-if="updatedBy"> · {{ updatedBy }}</template>
      </p>
    </div>

    <ItemComments v-else-if="tab === 'comments'" class="panel-body" :item="item" />
    <ItemHistory v-else class="panel-body" :item="item" />
  </aside>
</template>

<style scoped>
.panel { width: 400px; max-width: 100%; border-left: 1px solid var(--color-border); background: var(--color-surface); display: flex; flex-direction: column; min-height: 0; box-shadow: -8px 0 24px rgba(20, 25, 40, 0.06); z-index: 10; animation: slide-in 0.16s ease; }
@keyframes slide-in { from { transform: translateX(16px); opacity: 0; } to { transform: none; opacity: 1; } }
.panel-head { display: flex; align-items: center; gap: 4px; padding: 10px 12px 4px 16px; }
.type-badge { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--color-text-muted); }
.type-badge.t-milestone { color: #0f766e; }
.type-badge.t-group { color: #64748b; }
.title-wrap { padding: 0 16px 8px; }
.title-input { width: 100%; border: 1px solid transparent; border-radius: var(--radius-sm); font-size: 16px; font-weight: 700; padding: 4px 6px; margin-left: -6px; resize: none; field-sizing: content; min-height: 32px; font-family: inherit; color: var(--color-text); background: transparent; line-height: 1.35; }
.title-input:hover:not([readonly]) { border-color: var(--color-border); }
.title-input:focus { outline: none; border-color: var(--color-primary); box-shadow: var(--focus-ring); background: #fff; }
.tabs { margin: 0 16px 6px; align-self: flex-start; }
.tab-count { margin-left: 5px; background: var(--color-primary); color: #fff; border-radius: 999px; padding: 0 6px; font-size: 10px; }
.panel-body { flex: 1; overflow-y: auto; padding: 10px 16px 18px; display: flex; flex-direction: column; gap: 12px; }
.statuses { display: flex; width: 100%; flex-wrap: nowrap; overflow-x: auto; scrollbar-width: none; }
.statuses .status-option { flex: 1 1 auto; display: inline-flex; align-items: center; justify-content: center; gap: 5px; padding: 4px 6px; font-size: 12px; white-space: nowrap; }
.statuses .status-option.is-active { background: color-mix(in srgb, var(--c) 14%, white); color: var(--color-text); box-shadow: inset 0 0 0 1px var(--c); }
.statuses .status-option.is-active:disabled { background: #eef1f7; box-shadow: inset 0 0 0 1px var(--color-border-strong); color: var(--color-text-secondary); }
.field-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.field-sublabel { font-size: 11.5px; color: var(--color-text-secondary); }
.list-search { margin-bottom: 2px; }
.segmented.mini { align-self: flex-start; }
.segmented.mini button { padding: 4px 8px; }
.hints { list-style: none; margin: -4px 0 0; padding: 8px 10px; background: #f3f6ff; border-radius: var(--radius-sm); display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: #2b4db5; }
.hints li { display: flex; align-items: center; gap: 6px; }
.progress-input { display: flex; align-items: center; gap: 8px; }
.progress-input input[type=range] { flex: 1; accent-color: var(--color-primary); }
.progress-input .input { width: 64px; flex-shrink: 0; }
.offset-input { display: flex; align-items: center; gap: 10px; }
.offset-input .input { width: 90px; flex-shrink: 0; }
.section-label { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-text-muted); margin-top: 4px; }
.span-card { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px; padding: 10px 12px; background: var(--color-surface-2); border: 1px solid var(--color-border); border-radius: var(--radius-md); }
.span-card div { display: flex; flex-direction: column; gap: 2px; }
.span-card strong { font-size: 13px; }
.late { color: #c62933; }
.early { color: #1f7a4c; }
.with-action { display: flex; gap: 6px; }
.updated { margin: 6px 0 0; }
/* Ноутбук и планшет: карточка открывается поверх диаграммы, а не сжимает её */
@media (max-width: 1279px) {
  .panel { position: absolute; inset: 0 0 0 auto; width: min(420px, 100%); box-shadow: -12px 0 32px rgba(20, 25, 40, 0.18); }
}
/* Телефон: карточка — на весь экран поверх панели инструментов */
@media (max-width: 768px) {
  .panel { position: fixed; inset: 0; z-index: 150; width: 100%; border-left: 0; padding-top: env(safe-area-inset-top); }
  .panel-head { padding: 6px 8px 4px 16px; }
  .tabs { align-self: stretch; overflow-x: auto; }
  .tabs button { flex: 1; white-space: nowrap; }
}
</style>
