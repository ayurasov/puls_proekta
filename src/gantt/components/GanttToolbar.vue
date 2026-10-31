<script setup>
import { computed, inject, onBeforeUnmount, ref } from 'vue'
import AppIcon from '../../components/common/AppIcon.vue'
import HelpLink from '../../components/common/HelpLink.vue'
import PopupMenu from '../../components/common/PopupMenu.vue'
import GanttFilters from './GanttFilters.vue'
import { SCALES, nextZoom } from '../domain/layout'
import { clearFilters, filterChips, toggleFilter } from '../domain/filters'
import { exportPlan } from '../export'
import { useUiStore } from '../../stores/ui'

const emit = defineEmits(['open-modal', 'scroll-today', 'fit'])
const editor = inject('gantt')
const actions = inject('ganttActions')
const jira = inject('jira')
const ui = useUiStore()
const state = editor.state
const view = state.view
const menu = ref(null)
const exporting = ref(false)
// Телефон: поиск и фильтры — отдельной строкой над прокручиваемой панелью (одна копия кнопок в разметке)
const phoneQuery = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(max-width: 768px)') : null
const isPhone = ref(Boolean(phoneQuery?.matches))
const onPhoneChange = (event) => { isPhone.value = event.matches }
phoneQuery?.addEventListener?.('change', onPhoneChange)
onBeforeUnmount(() => phoneQuery?.removeEventListener?.('change', onPhoneChange))

function open(type, event) {
  menu.value = menu.value?.type === type ? null : { type, anchor: event.currentTarget }
}

function addRow(type) {
  menu.value = null
  if (type === 'note') {
    actions.openNoteEditor({ date: editor.state.today >= editor.range.start && editor.state.today <= editor.range.end ? editor.state.today : editor.range.start, rowKey: null })
    return
  }
  const after = view.selectedKey && state.items[view.selectedKey] ? view.selectedKey : null
  let parentKey
  if (after && state.items[after].type === 'group' && type !== 'group') parentKey = after
  const key = editor.createItem(type, {}, { afterKey: parentKey ? null : after, parentKey })
  editor.revealItem(key)
  editor.select(key, { openPanel: true })
}

const shortcutsButton = ref(null)
/** Подсказка по клавишам — кнопка «?» на панели и клавиша «?» в диаграмме. */
function showShortcuts() {
  menu.value = menu.value?.type === 'keys' ? null : { type: 'keys', anchor: shortcutsButton.value }
}
const SHORTCUTS = [
  ['Insert', 'Новая задача после выбранной строки'],
  ['Enter', 'Открыть карточку выбранной строки'],
  ['F2', 'Переименовать строку (или двойной щелчок)'],
  ['Delete', 'Удалить строку (отмена — в уведомлении)'],
  ['Ctrl+D', 'Дублировать строку'],
  ['Ctrl+Z', 'Отменить'],
  ['Ctrl+Y', 'Повторить (или Ctrl+Shift+Z)'],
  ['↑ ↓', 'Соседняя строка'],
  ['Esc', 'Закрыть карточку, снять выбор'],
  ['Ctrl + колесо', 'Масштаб шкалы'],
  ['Ctrl + перетаскивание', 'Копия задачи со сдвигом'],
  ['?', 'Эта подсказка'],
]

// Insert в рабочей области добавляет задачу так же, как кнопка «Задача»
defineExpose({ addRow, showShortcuts })

// Строка отметок под панелью: каждая отметка снимается щелчком (условия Jira — пока Jira подключена)
const chips = computed(() => filterChips(view, { assignees: editor.assigneeList }).filter((c) => c.group !== 'jira' || jira.state.connected))
const activeFilterCount = computed(() => chips.value.length)

function setScale(scale) {
  view.scale = scale
  editor.saveView()
}
function zoom(direction) {
  view.zoom = nextZoom(view.zoom, direction)
  editor.saveView()
}
function setZoomPercent(event) {
  const value = parseInt(String(event.target.value).replace('%', ''), 10)
  if (value >= 2 && value <= 600) {
    view.zoom = value / 100
    editor.saveView()
  }
  event.target.value = Math.round(view.zoom * 100) + '%'
}

async function runExport(format) {
  menu.value = null
  exporting.value = true
  try {
    await exportPlan(editor, format)
  } catch (err) {
    ui.error(err.message || 'Не удалось выгрузить план')
  } finally {
    exporting.value = false
  }
}

const columnLabels = computed(() => ({
  start: 'Начало', end: 'Окончание', duration: 'Длительность', assignee: 'Исполнитель', progress: 'Прогресс',
  ...(jira.state.connected ? { jira: 'Задача Jira' } : {}),
}))
const jiraLinked = computed(() => (jira.state.connected ? jira.linkedItems().length : 0))
const JIRA_AUTO_LABELS = { status: 'статус', dates: 'сроки', progress: 'прогресс', name: 'название' }
const jiraAutoLabel = computed(() => jira.autoFields().map((f) => JIRA_AUTO_LABELS[f] || f).join(', '))
// Число на кнопке J — строки, где в Jira иной статус, сроки или прогресс (одно лишь название не считается)
const jiraDiffering = computed(() => (jira.state.loaded ? jira.linkedItems().filter((item) => jira.differs(item)).length : 0))
const jiraNew = computed(() => jira.state.source?.newCount || 0)
const jiraSource = computed(() => jira.sourceSetting())
const jiraForeign = computed(() => jira.state.foreign || [])
const jiraForeignRows = computed(() => jiraForeign.value.reduce((sum, f) => sum + f.rows, 0))
function jiraAction(name) {
  menu.value = null
  if (name === 'refresh') jira.load({ refresh: true })
  else emit('open-modal', name)
}

function toggleColumn(column) {
  view.columns[column] = !view.columns[column]
  editor.saveView()
}
</script>

<template>
  <div class="toolbar-wrap">
  <div v-if="isPhone" class="mobile-search">
    <label class="search">
      <AppIcon name="search" :size="14" class="search-icon" />
      <input v-model="view.search" class="input input-sm" type="search" placeholder="Поиск по названию" data-testid="search" />
    </label>
    <button class="btn btn-sm" :class="{ 'is-active': activeFilterCount }" type="button" @click="open('filters', $event)">
      <AppIcon name="filter" :size="14" /> Фильтры<span v-if="activeFilterCount" class="count">{{ activeFilterCount }}</span>
    </button>
  </div>
  <div class="toolbar">
    <div class="group">
      <div v-if="editor.canEdit" class="split">
        <button class="btn btn-primary btn-sm" type="button" title="Новая задача после выбранной строки (Insert)" data-testid="add-task" @click="addRow('task')">
          <AppIcon name="plus" :size="14" /> Задача
        </button>
        <button class="btn btn-primary btn-sm split-caret" type="button" title="Добавить…" @click="open('add', $event)">
          <AppIcon name="chevronDown" :size="13" />
        </button>
      </div>
      <button v-if="editor.canEdit" class="btn btn-ghost btn-icon btn-sm" type="button" :disabled="!state.undo.length" :title="state.undo.length ? `Отменить: ${state.undo[state.undo.length - 1].label} (Ctrl+Z)` : 'Отменить (Ctrl+Z)'" data-testid="undo" @click="editor.undo()">
        <AppIcon name="undo" />
      </button>
      <button v-if="editor.canEdit" class="btn btn-ghost btn-icon btn-sm" type="button" :disabled="!state.redo.length" title="Повторить (Ctrl+Shift+Z)" @click="editor.redo()">
        <AppIcon name="redo" />
      </button>
    </div>

    <div v-if="!isPhone" class="group grow">
      <label class="search">
        <AppIcon name="search" :size="14" class="search-icon" />
        <input v-model="view.search" class="input input-sm" type="search" placeholder="Поиск по названию" data-testid="search" />
      </label>
      <button class="btn btn-sm" :class="{ 'is-active': activeFilterCount }" type="button" @click="open('filters', $event)">
        <AppIcon name="filter" :size="14" /> Фильтры<span v-if="activeFilterCount" class="count">{{ activeFilterCount }}</span>
      </button>
    </div>

    <div class="group">
      <div class="segmented" role="group" aria-label="Шкала">
        <button v-for="scale in SCALES" :key="scale.key" type="button" :class="{ 'is-active': view.scale === scale.key }" @click="setScale(scale.key)">{{ scale.label }}</button>
      </div>
      <div class="zoom">
        <button class="btn btn-ghost btn-icon btn-sm" type="button" title="Уменьшить (Ctrl + колесо)" @click="zoom(-1)"><AppIcon name="minus" :size="14" /></button>
        <input class="zoom-input" :value="Math.round(view.zoom * 100) + '%'" aria-label="Масштаб" @change="setZoomPercent" @keydown.enter="$event.target.blur()" />
        <button class="btn btn-ghost btn-icon btn-sm" type="button" title="Увеличить (Ctrl + колесо)" @click="zoom(1)"><AppIcon name="plus" :size="14" /></button>
      </div>
      <button class="btn btn-ghost btn-sm" type="button" title="Весь период на ширину экрана" @click="emit('fit')"><AppIcon name="fit" :size="14" /></button>
      <button class="btn btn-ghost btn-sm" type="button" title="Показать сегодня" @click="emit('scroll-today')"><AppIcon name="today" :size="14" /><span class="today-label">Сегодня</span></button>
      <button class="btn btn-ghost btn-icon btn-sm" type="button" title="Вид" @click="open('view', $event)"><AppIcon name="layers" /></button>
      <button ref="shortcutsButton" class="btn btn-ghost btn-icon btn-sm" type="button" title="Клавиши (?)" aria-label="Сочетания клавиш" data-testid="shortcuts" @click="showShortcuts">
        <AppIcon name="keyboard" />
      </button>
      <HelpLink topic="plan" />
    </div>

    <!-- Действия с планом целиком — в строке вкладок планов, рядом с выбранным планом -->
    <Teleport defer to="#plan-actions-slot">
      <button class="btn btn-ghost btn-icon btn-sm" type="button" title="Исполнители" @click="emit('open-modal', 'assignees')"><AppIcon name="users" /></button>
      <button class="btn btn-ghost btn-icon btn-sm" type="button" title="Статистика" @click="emit('open-modal', 'stats')"><AppIcon name="chart" /></button>
      <button class="btn btn-ghost btn-icon btn-sm" type="button" title="История и сохранённые версии" data-testid="history" @click="emit('open-modal', 'history')"><AppIcon name="history" /></button>
      <button class="btn btn-ghost btn-icon btn-sm" type="button" title="Настройки графика" @click="emit('open-modal', 'settings')"><AppIcon name="settings" /></button>
      <button v-if="jira.state.connected" class="btn btn-ghost btn-sm jira-btn" :class="{ 'has-diff': jiraDiffering, 'has-new': jiraNew }" type="button"
              :title="[jiraDiffering ? `Jira: в ${jiraDiffering} строках данные расходятся` : 'Jira', jiraNew ? `новых задач в Jira: ${jiraNew}` : ''].filter(Boolean).join(' · ')"
              data-testid="jira-menu" @click="open('jira', $event)">
        <span class="jira-mark" aria-hidden="true">J</span><span v-if="jiraDiffering" class="count">{{ jiraDiffering }}</span><span v-if="jiraNew" class="new-dot" aria-hidden="true" />
      </button>
      <button class="btn btn-sm" type="button" :disabled="exporting" title="Экспорт" @click="open('export', $event)">
        <AppIcon name="download" :size="14" /> <span class="export-label">{{ exporting ? 'Готовим…' : 'Экспорт' }}</span>
      </button>
    </Teleport>

    <PopupMenu v-if="menu?.type === 'add'" :anchor="menu.anchor" @close="menu = null">
      <button class="menu-item" type="button" @click="addRow('task')"><AppIcon name="gantt" /> Задача</button>
      <button class="menu-item" type="button" @click="addRow('milestone')"><AppIcon name="diamond" /> Веха</button>
      <button class="menu-item" type="button" @click="addRow('group')"><AppIcon name="folder" /> Группа</button>
      <button class="menu-item" type="button" @click="addRow('note')"><AppIcon name="note" /> Пометка на шкале</button>
      <template v-if="jira.state.connected">
        <div class="menu-separator" />
        <button class="menu-item" type="button" @click="jiraAction('jira-import')"><span class="jira-mark small" aria-hidden="true">J</span> Задачи из Jira…</button>
      </template>
    </PopupMenu>

    <PopupMenu v-if="menu?.type === 'jira'" :anchor="menu.anchor" align="end" @close="menu = null">
      <div class="menu-label">Jira · строк с задачами: {{ jiraLinked }}</div>
      <button v-if="jiraNew && editor.canEdit" class="menu-item menu-item-accent" type="button" data-testid="jira-new-open" @click="jiraAction('jira-import')">
        <AppIcon name="plus" /> Новые задачи в {{ jiraSource.project || 'источнике' }}<span class="menu-hint menu-hint-accent">{{ jiraNew }}</span>
      </button>
      <button class="menu-item" type="button" :disabled="jira.state.loading || !jiraLinked" @click="jiraAction('refresh')">
        <AppIcon name="refresh" /> {{ jira.state.loading ? 'Обновляем…' : 'Обновить из Jira' }}
      </button>
      <button class="menu-item" type="button" :disabled="!jiraLinked && !editor.canEdit" data-testid="jira-sync-open" @click="jiraAction('jira-sync')">
        <AppIcon name="checklist" /> Сверка и настройки…<span v-if="jiraDiffering" class="menu-hint">расходится: {{ jiraDiffering }}</span>
      </button>
      <template v-if="editor.canEdit">
        <div class="menu-separator" />
        <button class="menu-item" type="button" data-testid="jira-import-open" @click="jiraAction('jira-import')"><AppIcon name="search" /> Задачи из Jira…</button>
        <button class="menu-item" type="button" data-testid="jira-versions-open" @click="jiraAction('jira-versions')"><AppIcon name="diamond" /> Релизы Jira → вехи…</button>
      </template>
      <div v-if="jiraSource.project || jiraSource.jql" class="menu-note small" data-testid="jira-source-note">Источник задач: {{ jiraSource.project || 'запрос JQL' }}{{ jiraSource.project && jiraSource.jql ? ' + JQL' : '' }}</div>
      <div v-if="jira.autoFields().length" class="menu-note small" data-testid="jira-auto-note">Автоперенос: {{ jiraAutoLabel }}</div>
      <div v-if="jiraForeignRows" class="menu-note small menu-warn" data-testid="jira-foreign-note">
        {{ jiraForeignRows }} {{ jiraForeignRows === 1 ? 'строка ссылается' : 'строк ссылаются' }} на задачи по адресу {{ jiraForeign.map((f) => f.host).join(', ') }} — это не подключённая Jira, статусы не загружаются.
        Если это та же Jira, администратор добавит адрес в «Интеграции → Jira → Другие адреса».
      </div>
      <div v-if="jira.state.error" class="menu-note small menu-error">{{ jira.state.error }}</div>
      <div v-if="jira.state.source?.error" class="menu-note small menu-warn">Источник задач: {{ jira.state.source.error }}</div>
    </PopupMenu>

    <PopupMenu v-if="menu?.type === 'filters'" :anchor="menu.anchor" :min-width="660" @close="menu = null">
      <GanttFilters />
    </PopupMenu>

    <PopupMenu v-if="menu?.type === 'view'" :anchor="menu.anchor" align="end" @close="menu = null">
      <div class="menu-label">Колонки слева</div>
      <label v-for="(label, column) in columnLabels" :key="column" class="menu-item">
        <input type="checkbox" :checked="view.columns[column]" @change="toggleColumn(column)" /> {{ label }}
      </label>
      <div class="menu-separator" />
      <button class="menu-item" type="button" @click="editor.collapseAll(false); menu = null"><AppIcon name="expandAll" /> Развернуть все группы</button>
      <button class="menu-item" type="button" @click="editor.collapseAll(true); menu = null"><AppIcon name="collapseAll" /> Свернуть все группы</button>
    </PopupMenu>

    <PopupMenu v-if="menu?.type === 'keys'" :anchor="menu.anchor" align="end" :min-width="440" @close="menu = null">
      <div class="menu-label">Клавиши и мышь</div>
      <dl class="shortcuts">
        <template v-for="[keys, text] in SHORTCUTS" :key="keys">
          <dt><kbd>{{ keys }}</kbd></dt><dd>{{ text }}</dd>
        </template>
      </dl>
    </PopupMenu>

    <PopupMenu v-if="menu?.type === 'export'" :anchor="menu.anchor" align="end" @close="menu = null">
      <button class="menu-item" type="button" @click="runExport('png')"><AppIcon name="image" /> Изображение PNG</button>
      <button class="menu-item" type="button" @click="runExport('pdf')"><AppIcon name="file" /> Документ PDF</button>
      <button class="menu-item" type="button" @click="runExport('svg')"><AppIcon name="image" /> Векторное SVG</button>
      <div class="menu-separator" />
      <button class="menu-item" type="button" @click="runExport('csv')"><AppIcon name="table" /> Таблица для Excel (CSV)</button>
      <button class="menu-item" type="button" @click="runExport('json')"><AppIcon name="download" /> JSON (открывается и в 1.0)</button>
    </PopupMenu>
  </div>
  <div v-if="activeFilterCount" class="filter-bar" data-testid="filter-bar">
    <span class="muted small">Фильтры:</span>
    <button v-for="chip in chips" :key="chip.group + chip.value" class="chip" type="button" title="Убрать фильтр" @click="toggleFilter(view, chip.group, chip.value)">
      {{ chip.label }} <AppIcon name="close" :size="11" />
    </button>
    <button class="btn btn-ghost btn-sm" type="button" @click="clearFilters(view)">Сбросить</button>
  </div>
  </div>
</template>

<style scoped>
.jira-btn { gap: 4px; }
.jira-btn.has-diff { color: #0052cc; }
.jira-btn { position: relative; }
.new-dot { position: absolute; top: 3px; right: 3px; width: 7px; height: 7px; border-radius: 50%; background: #f59e0b; box-shadow: 0 0 0 2px var(--color-surface); }
.menu-item-accent { color: #0052cc; font-weight: 600; }
.menu-hint-accent { background: #fff4d6; color: #8a5a00; border-radius: 999px; padding: 0 7px; font-weight: 700; }
.menu-warn { color: #a15c07; max-width: 320px; white-space: normal; line-height: 1.4; }
.menu-error { color: var(--color-danger); }
.jira-mark { display: inline-grid; place-items: center; width: 16px; height: 16px; border-radius: 4px; background: #0052cc; color: #fff; font-size: 11px; font-weight: 800; font-style: italic; line-height: 1; }
.menu-note { padding: 6px 10px; color: var(--color-text-muted); max-width: 320px; line-height: 1.4; }
.toolbar-wrap { flex-shrink: 0; background: var(--color-surface); border-bottom: 1px solid var(--color-border); container-type: inline-size; }
.toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 14px; padding: 8px 12px; }
.group { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.group.grow { flex: 1; min-width: 240px; flex-wrap: nowrap; }
.filter-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 4px 6px; padding: 0 12px 8px; }
.today-label { margin-left: 4px; }
.split { display: inline-flex; margin-right: 4px; }
.split .btn:first-child { border-top-right-radius: 0; border-bottom-right-radius: 0; }
.split-caret { border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: 1px solid rgba(255,255,255,.35); padding: 3px 6px; }
.search { position: relative; flex: 0 1 240px; min-width: 140px; }
.search .input { padding-left: 26px; }
.search-icon { position: absolute; left: 8px; top: 6px; color: var(--color-text-muted); }
.count { background: var(--color-primary); color: #fff; border-radius: 999px; padding: 0 6px; font-size: 10.5px; font-weight: 700; }
.chip { display: inline-flex; align-items: center; gap: 4px; border: 1px solid #c9d6ff; background: var(--color-primary-soft); color: var(--color-primary-dark); border-radius: 999px; font-size: 11.5px; padding: 2px 8px; }
.zoom { display: flex; align-items: center; }
.zoom-input { width: 52px; text-align: center; border: 1px solid var(--color-border); border-radius: var(--radius-sm); font-size: 12px; min-height: 26px; }
label.menu-item { cursor: pointer; }
.shortcuts { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px; margin: 4px 10px 8px; font-size: 12.5px; align-items: baseline; }
.shortcuts dt { white-space: nowrap; }
.shortcuts dd { margin: 0; color: var(--color-text-secondary); }
kbd { font-family: inherit; font-size: 11.5px; background: var(--color-surface-2); border: 1px solid var(--color-border-strong); border-bottom-width: 2px; border-radius: 5px; padding: 1px 6px; color: var(--color-text); }
@media (max-width: 768px) { .export-label { display: none; } }
/* Узкая рабочая область: «Сегодня» — только значком, чтобы панель оставалась в одну строку */
@container (max-width: 1000px) { .today-label { display: none; } }
/* Телефон: поиск и фильтры — первой строкой, остальные кнопки — одной прокручиваемой строкой */
@media (max-width: 768px) {
  .toolbar { gap: 6px 8px; padding: 6px 10px; flex-wrap: nowrap; overflow-x: auto; scrollbar-width: none; }
  .toolbar::-webkit-scrollbar { display: none; }
  .group { flex-wrap: nowrap; flex-shrink: 0; }
  .mobile-search { display: flex; gap: 6px; padding: 6px 10px 0; }
  .mobile-search .search { flex: 1 1 auto; }
  .zoom-input { min-height: 40px; }
  .split-caret { padding: 3px 10px; }
}

</style>
