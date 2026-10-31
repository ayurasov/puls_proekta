<script setup>
import { computed, inject, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../../api/client'
import BaseModal from '../../components/common/BaseModal.vue'
import HelpLink from '../../components/common/HelpLink.vue'
import AppIcon from '../../components/common/AppIcon.vue'
import SegmentedControl from '../../components/common/SegmentedControl.vue'
import { formatDay } from '../domain/dates'
import { buildImportRows, currentSprint, milestoneFromVersion } from '../jira'
import { localStorageGet, localStorageSet } from '../../stores/ui'

/**
 * «Задачи из Jira»: поиск в проекте Jira по фильтрам (без знания JQL), по ключам или запросом JQL; релизы проекта —
 * вехами. Задачи можно разложить по эпикам или спринтам, связи «блокирует» — сделать связями плана. Уже
 * привязанные к плану задачи не повторяются. Всё добавляется одной отменяемой правкой.
 */
const props = defineProps({ initialMode: { type: String, default: '' } })
const emit = defineEmits(['close'])
const editor = inject('gantt')
const jira = inject('jira')

const MODES = [
  { value: 'project', label: 'Поиск в проекте', icon: 'search' },
  { value: 'keys', label: 'По ключам' },
  { value: 'jql', label: 'JQL' },
  { value: 'versions', label: 'Релизы → вехи', icon: 'flag' },
]
const CATEGORIES = [
  { value: 'new', label: 'К выполнению' },
  { value: 'indeterminate', label: 'В работе' },
  { value: 'done', label: 'Готово' },
]
const GROUPS = [
  { value: 'none', label: 'Без групп' },
  { value: 'epics', label: 'По эпикам' },
  { value: 'sprints', label: 'По спринтам' },
]
const ORDERS = [
  { value: 'rank', label: 'Как в Jira (ранг)' },
  { value: 'due', label: 'По сроку' },
  { value: 'updated', label: 'Недавно изменённые' },
  { value: 'key', label: 'По ключу' },
]
const LAST_PROJECT = 'puls.jiraImport.project'

const source = jira.sourceSetting()
const mode = ref(props.initialMode || 'project')
const projects = ref(null)
const projectsError = ref('')
const project = ref(source.project || localStorageGet(LAST_PROJECT) || '')
const meta = ref(null)
const metaLoading = ref(false)
const filters = reactive({ text: '', types: [], categories: ['new', 'indeterminate'], version: '', sprint: '', epic: '', unassigned: false, order: 'rank' })
const keys = ref('')
const jql = ref('')
const busy = ref(false)
const error = ref('')
const result = ref(null)
const picked = ref(new Set())
const grouping = ref('none')
const withLinks = ref(true)
const makeSource = ref(!source.project && !source.jql)
const pickedVersions = ref(new Set())

const groups = computed(() => editor.itemList.filter((i) => i.type === 'group'))
const selectedItem = computed(() => editor.state.items[editor.state.view.selectedKey] || null)
// По умолчанию — группа выбранной строки (ближайшая вверх по дереву): в списке «Куда добавить» только группы
function groupOf(item) {
  let current = item
  while (current && current.type !== 'group') current = editor.state.items[current.parentKey]
  return current?.key || ''
}
const parentKey = ref(props.initialMode === 'versions' ? '' : groupOf(selectedItem.value))
// Предложить автоперенос, если он в плане ещё не включён: дальше статус, сроки и прогресс строк обновляются сами
const autoOn = computed(() => jira.autoFields().length > 0)
const enableAuto = ref(false)
const linkedRows = computed(() => new Map(jira.linkedItems().map((item) => [jira.keyFor(item), item])))
const linked = computed(() => new Set(linkedRows.value.keys()))
const projectName = computed(() => projects.value?.find((p) => p.key === project.value)?.name || project.value)

onMounted(async () => {
  try {
    projects.value = (await api.get('/jira/projects', { planId: editor.state.plan.id })).projects
    if (!project.value && projects.value.length === 1) project.value = projects.value[0].key
    if (project.value && !projects.value.some((p) => p.key === project.value)) project.value = source.project || ''
  } catch (err) {
    projects.value = []
    projectsError.value = err.message
  }
  if (project.value) loadMeta()
})

async function loadMeta() {
  meta.value = null
  if (!project.value) return
  metaLoading.value = true
  try {
    meta.value = await api.get(`/jira/projects/${encodeURIComponent(project.value)}/meta`, { planId: editor.state.plan.id })
  } catch (err) {
    meta.value = { types: [], statuses: [], versions: [], sprints: [], epics: [], warnings: [err.message] }
  } finally { metaLoading.value = false }
}

watch(project, (value) => {
  if (value) localStorageSet(LAST_PROJECT, value)
  Object.assign(filters, { types: [], version: '', sprint: '', epic: '' })
  pickedVersions.value = new Set()
  result.value = null
  loadMeta()
  if (mode.value === 'project') scheduleSearch(0)
})

// Типы без эпика: эпик в плане — группа («По эпикам»), а не задача
const typeOptions = computed(() => (meta.value?.types || []).filter((t) => !/^(epic|эпик)$/i.test(t)))
function toggleIn(list, value) {
  const index = list.indexOf(value)
  if (index >= 0) list.splice(index, 1)
  else list.push(value)
}

let timer = null
function scheduleSearch(delay = 400) {
  clearTimeout(timer)
  timer = setTimeout(() => { if (mode.value === 'project' && project.value) search() }, delay)
}
watch(() => [filters.types.join(), filters.categories.join(), filters.version, filters.sprint, filters.epic, filters.unassigned, filters.order], () => scheduleSearch(150))
watch(() => filters.text, () => scheduleSearch(500))
watch(grouping, () => { if (result.value) search() })
onBeforeUnmount(() => clearTimeout(timer))
onMounted(() => { if (mode.value === 'project' && project.value) scheduleSearch(0) })
watch(mode, (value) => {
  result.value = null
  error.value = ''
  // Вехи релизов — по умолчанию в конец плана, а не в группу выбранной строки
  if (value === 'versions') parentKey.value = ''
  if (value === 'project' && project.value) scheduleSearch(0)
})

let searchSeq = 0
async function search() {
  error.value = ''
  busy.value = true
  const seq = ++searchSeq
  try {
    const body = { planId: editor.state.plan.id, extended: true, ...(grouping.value === 'epics' ? { epics: true } : {}) }
    if (mode.value === 'keys') body.keys = keys.value
    else if (mode.value === 'jql') body.jql = jql.value
    else body.filter = { project: project.value, ...filters, onlyOpen: false }
    const res = await api.post('/jira/search', body)
    if (seq !== searchSeq) return
    result.value = res
    picked.value = new Set(res.issues.filter((i) => !linked.value.has(i.key) && !(grouping.value !== 'epics' && i.isEpic)).map((i) => i.key))
  } catch (err) {
    if (seq !== searchSeq) return
    error.value = err.message
    result.value = null
  } finally { if (seq === searchSeq) busy.value = false }
}

function toggle(key) {
  const next = new Set(picked.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  picked.value = next
}
const available = computed(() => (result.value?.issues || []).filter((i) => !linked.value.has(i.key)))
const allPicked = computed(() => available.value.length > 0 && available.value.every((i) => picked.value.has(i.key)))
function toggleAll() {
  picked.value = allPicked.value ? new Set() : new Set(available.value.map((i) => i.key))
}
const chosen = computed(() => (result.value?.issues || []).filter((i) => picked.value.has(i.key)))
// Ключи, по которым Jira ничего не вернула (не ответила): не «не найдены», а «не получены»
const notReceived = computed(() => {
  if (!result.value || mode.value !== 'keys' || !result.value.error) return []
  const known = new Set([...result.value.issues.map((i) => i.key), ...result.value.missing])
  return keys.value.toUpperCase().split(/[\s,;]+/).filter((k) => /^[A-Z][A-Z0-9_]*-\d+$/.test(k) && !known.has(k))
})
const linksInResult = computed(() => chosen.value.filter((i) => (i.links?.blockedBy || []).some((k) => picked.value.has(k) || linked.value.has(k))).length)
const jqlLink = computed(() => (result.value?.jql && jira.state.baseUrl ? `${jira.state.baseUrl}/issues/?jql=${encodeURIComponent(result.value.jql)}` : null))

function epicGroups() {
  return new Map(groups.value.filter((g) => jira.keyFor(g)).map((g) => [jira.keyFor(g), g.key]))
}
function rowsToAdd() {
  return buildImportRows(chosen.value, {
    group: grouping.value, epics: result.value?.epics || {}, epicGroups: epicGroups(),
    groupsByName: new Map(groups.value.map((g) => [g.name, g.key])), links: withLinks.value,
    linkedRows: new Map([...linkedRows.value].filter(([, item]) => item.type === 'task').map(([key, item]) => [key, { key: item.key, end: item.end }])),
    today: editor.state.today,
  })
}
const groupCount = computed(() => {
  if (grouping.value === 'epics') return new Set(chosen.value.map((i) => (i.isEpic ? i.key : i.epic)).filter(Boolean)).size
  if (grouping.value === 'sprints') return new Set(chosen.value.map((i) => currentSprint(i)?.id).filter(Boolean)).size
  return 0
})
const taskCount = computed(() => chosen.value.filter((i) => !(grouping.value === 'epics' && i.isEpic)).length)

// ---------- релизы → вехи ----------
const versionMilestones = computed(() => new Set(editor.itemList.filter((i) => i.type === 'milestone' && i.jiraUrl).map((i) => i.jiraUrl)))
const versions = computed(() => (meta.value?.versions || []).map((v) => ({ ...v, inPlan: versionMilestones.value.has(v.url) })))
function toggleVersion(id) {
  const next = new Set(pickedVersions.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  pickedVersions.value = next
}
const chosenVersions = computed(() => versions.value.filter((v) => pickedVersions.value.has(v.id) && v.release && !v.inPlan))

function rememberSource() {
  if (makeSource.value && mode.value === 'project' && project.value && !source.project && !source.jql) jira.setSource({ project: project.value, jql: '' })
}

function add() {
  if (mode.value === 'versions') {
    const list = chosenVersions.value.map(milestoneFromVersion)
    const created = editor.createItems(list, { parentKey: parentKey.value || null, label: `Релизы из Jira (${list.length})` })
    if (!created.length) return
    editor.revealItem(created[created.length - 1])
    emit('close')
    return
  }
  const list = rowsToAdd()
  const created = editor.createItems(list, { parentKey: parentKey.value || null, label: `Задачи из Jira (${taskCount.value})` })
  if (!created.length) return
  for (const issue of chosen.value) jira.state.issues[issue.key] = issue
  if (enableAuto.value && !autoOn.value) jira.setAutoFields(['status', 'dates', 'progress'])
  rememberSource()
  editor.revealItem(created[created.length - 1])
  editor.select(created[0])
  emit('close')
}
const canAdd = computed(() => (mode.value === 'versions' ? chosenVersions.value.length > 0 : chosen.value.length > 0))
const addLabel = computed(() => (mode.value === 'versions'
  ? `Добавить вехи${chosenVersions.value.length ? ` (${chosenVersions.value.length})` : ''}`
  : `Добавить${chosen.value.length ? ` (${chosen.value.length})` : ''}`))
const sprintLabel = (s) => `${s.name}${s.state === 'active' ? ' · идёт' : s.state === 'closed' ? ' · завершён' : ''}${s.start && s.end ? ` · ${formatDay(s.start)}–${formatDay(s.end)}` : ''}`
</script>

<template>
  <BaseModal title="Задачи из Jira" size="wide" @close="emit('close')">
    <template #header-actions><HelpLink topic="jira-import" /></template>
    <SegmentedControl v-model="mode" :options="MODES" kind="tabs" class="modes" aria-label="Как найти задачи" data-testid="jira-import-mode" />

    <!-- Поиск в проекте: проект, слова, типы, статусы, релиз, спринт, эпик -->
    <section v-if="mode === 'project' || mode === 'versions'" class="filters">
      <div class="filters-row">
        <label class="field project-field">
          <span class="field-label">Проект Jira</span>
          <select v-model="project" class="select" :disabled="!projects" data-testid="jira-import-project">
            <option value="" disabled>{{ projects ? 'Выберите проект' : 'Загружаем проекты…' }}</option>
            <option v-for="p in projects || []" :key="p.key" :value="p.key">{{ p.name }} · {{ p.key }}</option>
          </select>
        </label>
        <label v-if="mode === 'project'" class="field text-field">
          <span class="field-label">Слова из названия или ключ</span>
          <span class="input-with-icon">
            <AppIcon name="search" :size="14" class="input-icon" />
            <input v-model="filters.text" class="input" :placeholder="`Например, «личный кабинет» или ${project || 'ABC'}-12`" data-testid="jira-import-text" />
          </span>
        </label>
      </div>
      <p v-if="projectsError" class="error small"><AppIcon name="warning" :size="13" /> {{ projectsError }}</p>
      <p v-else-if="projects && !projects.length" class="notice small">
        <AppIcon name="warning" :size="13" /> Учётная запись подключения не видит ни одного проекта Jira. Попросите администратора выдать ей право
        «Просмотр проектов» или проверьте подключение («Интеграции → Jira → Диагностика»).
      </p>

      <template v-if="mode === 'project' && project">
        <div class="chip-row" role="group" aria-label="Статус в Jira">
          <span class="chip-caption">Статус</span>
          <button v-for="c in CATEGORIES" :key="c.value" type="button" class="chip" :class="[`cat-${c.value}`, { on: filters.categories.includes(c.value) }]"
                  :aria-pressed="filters.categories.includes(c.value)" @click="toggleIn(filters.categories, c.value)">{{ c.label }}</button>
        </div>
        <div v-if="typeOptions.length" class="chip-row" role="group" aria-label="Тип задачи">
          <span class="chip-caption">Тип</span>
          <button v-for="t in typeOptions" :key="t" type="button" class="chip" :class="{ on: filters.types.includes(t) }" :aria-pressed="filters.types.includes(t)" @click="toggleIn(filters.types, t)">{{ t }}</button>
          <span v-if="!filters.types.length" class="muted small">все типы</span>
        </div>
        <div class="selects">
          <label class="field">
            <span class="field-label">Релиз</span>
            <select v-model="filters.version" class="select select-sm" :disabled="!meta?.versions?.length">
              <option value="">Любой</option>
              <option v-for="v in meta?.versions || []" :key="v.id" :value="v.id">{{ v.name }}{{ v.released ? ' · выпущен' : '' }}</option>
            </select>
          </label>
          <label class="field">
            <span class="field-label">Спринт</span>
            <select v-model="filters.sprint" class="select select-sm" :disabled="!meta?.sprints?.length">
              <option value="">Любой</option>
              <option value="open">Текущие и будущие</option>
              <option v-for="s in meta?.sprints || []" :key="s.id" :value="String(s.id)">{{ sprintLabel(s) }}</option>
            </select>
          </label>
          <label class="field">
            <span class="field-label">Эпик</span>
            <select v-model="filters.epic" class="select select-sm" :disabled="!meta?.epics?.length">
              <option value="">Любой</option>
              <option v-for="e in meta?.epics || []" :key="e.key" :value="e.key">{{ e.summary }} · {{ e.key }}</option>
            </select>
          </label>
          <label class="field">
            <span class="field-label">Порядок</span>
            <select v-model="filters.order" class="select select-sm">
              <option v-for="o in ORDERS" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
          </label>
          <label class="check small"><input v-model="filters.unassigned" type="checkbox" /> Без исполнителя</label>
        </div>
        <p v-if="metaLoading" class="muted small">Загружаем типы, релизы и спринты проекта…</p>
        <p v-for="w in meta?.warnings || []" :key="w" class="muted small">{{ w }}</p>
      </template>
    </section>

    <form v-else class="query" @submit.prevent="search">
      <label v-if="mode === 'keys'" class="field">
        <span class="field-label">Ключи задач</span>
        <textarea v-model="keys" class="input" rows="3" placeholder="ABC-101, ABC-102&#10;XYZ-7" data-testid="jira-import-keys" />
        <span class="field-hint">Через запятую, пробел или с новой строки — можно вставить список из Jira.</span>
      </label>
      <label v-else class="field">
        <span class="field-label">Запрос JQL</span>
        <textarea v-model="jql" class="input mono" rows="3" placeholder="project = ABC AND fixVersion = &quot;Релиз 2&quot; ORDER BY rank" data-testid="jira-import-jql" />
        <span class="field-hint">Не больше 200 задач. Строка запроса — как в поиске Jira.</span>
      </label>
      <div class="actions">
        <button class="btn" type="submit" :disabled="busy || !(mode === 'keys' ? keys.trim() : jql.trim())" data-testid="jira-import-search">
          <AppIcon name="search" :size="14" /> {{ busy ? 'Ищем…' : 'Найти' }}
        </button>
      </div>
    </form>
    <p v-if="error" class="error small" role="alert"><AppIcon name="warning" :size="13" /> {{ error }}</p>

    <!-- Релизы проекта → вехи плана -->
    <template v-if="mode === 'versions' && project">
      <p v-if="metaLoading" class="muted small">Загружаем релизы…</p>
      <p v-else-if="!versions.length" class="empty">В проекте {{ projectName }} нет релизов.</p>
      <div v-else class="list scroll-thin" data-testid="jira-import-versions">
        <label v-for="v in versions" :key="v.id" class="version" :class="{ disabled: v.inPlan || !v.release }">
          <input type="checkbox" :checked="pickedVersions.has(v.id) && !v.inPlan" :disabled="v.inPlan || !v.release" @change="toggleVersion(v.id)" />
          <span class="version-mark" aria-hidden="true">◆</span>
          <span class="summary-cell">
            <span class="summary ellipsis">{{ v.name }}</span>
            <span v-if="v.description" class="muted small ellipsis">{{ v.description }}</span>
          </span>
          <span class="badge" :class="v.released ? 'badge-success' : 'badge-primary'">{{ v.released ? 'выпущен' : 'не выпущен' }}</span>
          <span class="due small">{{ v.release ? formatDay(v.release) : 'без даты выпуска' }}</span>
          <span v-if="v.inPlan" class="badge">уже в плане</span>
        </label>
      </div>
      <p class="muted small">Каждый выбранный релиз станет вехой на дату выпуска со ссылкой на релиз в Jira. Релизы без даты выпуска добавить нельзя.</p>
    </template>

    <!-- Найденные задачи -->
    <template v-if="mode !== 'versions' && result">
      <div class="result-head">
        <label v-if="available.length" class="check small select-all"><input type="checkbox" :checked="allPicked" @change="toggleAll" /> Выбрать все</label>
        <span class="muted small">Найдено: {{ result.issues.length }}{{ result.truncated ? '+' : '' }} · выбрано: {{ chosen.length }}</span>
        <span class="spacer" />
        <a v-if="jqlLink" class="small link" :href="jqlLink" target="_blank" rel="noopener noreferrer" :title="result.jql"><AppIcon name="external" :size="12" /> Открыть поиск в Jira</a>
      </div>
      <p v-if="!result.issues.length" class="empty">Ничего не найдено — измените условия.</p>
      <div v-else class="list scroll-thin" data-testid="jira-import-list">
        <label v-for="issue in result.issues" :key="issue.key" class="issue" :class="{ linked: linked.has(issue.key) }">
          <input type="checkbox" :checked="picked.has(issue.key)" :disabled="linked.has(issue.key)" @change="toggle(issue.key)" />
          <span class="key">{{ issue.key }}</span>
          <span class="summary-cell">
            <span class="summary ellipsis" :title="issue.summary">{{ issue.summary }}</span>
            <span class="tags">
              <span v-if="issue.type" class="tag">{{ issue.type }}</span>
              <span v-if="grouping === 'epics' && issue.isEpic" class="tag tag-epic">эпик → группа</span>
              <span v-else-if="issue.epic" class="tag tag-epic ellipsis" :title="result.epics?.[issue.epic]?.summary">{{ result.epics?.[issue.epic]?.summary || issue.epic }}</span>
              <span v-if="currentSprint(issue)" class="tag">{{ currentSprint(issue).name }}</span>
              <span v-if="issue.links?.blockedBy?.length" class="tag" :title="`Блокируется: ${issue.links.blockedBy.join(', ')}`">⛓ {{ issue.links.blockedBy.join(', ') }}</span>
            </span>
          </span>
          <span class="assignee small ellipsis">{{ issue.assignee || '—' }}</span>
          <span class="jira-status" :class="`cat-${issue.statusCategory}`">{{ issue.status }}</span>
          <span class="due small">{{ issue.due ? formatDay(issue.due) : 'без срока' }}</span>
          <span v-if="linked.has(issue.key)" class="badge">уже в плане</span>
        </label>
      </div>
      <p v-if="result.missing.length || result.invalid.length" class="muted small">
        <template v-if="result.missing.length">Не найдены или недоступны: {{ result.missing.join(', ') }}. </template>
        <template v-if="result.invalid.length">Не похожи на ключ задачи: {{ result.invalid.join(', ') }}.</template>
      </p>
      <p v-if="result.truncated" class="muted small">Показаны первые 200 задач — уточните условия.</p>
      <p v-if="notReceived.length" class="error small"><AppIcon name="warning" :size="13" /> Не удалось получить из Jira: {{ [...new Set(notReceived)].join(', ') }} — {{ result.error }}</p>
    </template>

    <!-- Как добавить -->
    <section v-if="(mode !== 'versions' && result?.issues.length) || (mode === 'versions' && versions.length)" class="options">
      <div v-if="mode !== 'versions'" class="option-row">
        <span class="option-label">Группы</span>
        <SegmentedControl v-model="grouping" :options="GROUPS" size="sm" aria-label="Группы" data-testid="jira-import-grouping" />
        <span class="muted small">{{ grouping === 'epics' ? 'эпик станет группой плана' : grouping === 'sprints' ? 'спринт станет группой, его сроки — сроками задач без срока' : '' }}</span>
      </div>
      <label v-if="mode !== 'versions'" class="check">
        <input v-model="withLinks" type="checkbox" data-testid="jira-import-links" />
        Связи «блокирует» — связями плана (окончание → начало)<span v-if="withLinks && linksInResult" class="muted small"> · связей: {{ linksInResult }}</span>
      </label>
      <label class="field place">
        <span class="field-label">Куда добавить</span>
        <select v-model="parentKey" class="select">
          <option value="">В конец плана</option>
          <option v-for="g in groups" :key="g.key" :value="g.key">В группу «{{ g.name }}»</option>
        </select>
      </label>
      <label v-if="mode !== 'versions' && !autoOn" class="check">
        <input v-model="enableAuto" type="checkbox" data-testid="jira-import-auto" />
        Дальше переносить из Jira автоматически статус, сроки и прогресс
      </label>
      <p v-else-if="mode !== 'versions'" class="muted small">В плане включён автоперенос из Jira — новые строки будут обновляться сами.</p>
      <label v-if="mode === 'project' && project && !source.project && !source.jql" class="check">
        <input v-model="makeSource" type="checkbox" data-testid="jira-import-source" />
        Сделать {{ projectName }} источником задач плана — план подскажет о новых задачах проекта
      </label>
    </section>

    <template #footer>
      <button class="btn" type="button" @click="emit('close')">Отмена</button>
      <span v-if="groupCount" class="muted small foot-note">Групп: {{ groupCount }}</span>
      <span v-else class="foot-note" />
      <button class="btn btn-primary" type="button" :disabled="!canAdd" data-testid="jira-import-add" @click="add">{{ addLabel }}</button>
    </template>
  </BaseModal>
</template>

<style scoped>
.modes { margin-bottom: 14px; max-width: 100%; overflow-x: auto; scrollbar-width: none; }
.modes::-webkit-scrollbar { display: none; }
.filters { display: flex; flex-direction: column; gap: 10px; padding: 12px 14px; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface-2, #f8fafc); }
.filters-row { display: grid; grid-template-columns: minmax(200px, 1fr) minmax(240px, 1.4fr); gap: 12px; }
.input-with-icon { position: relative; display: block; }
.input-with-icon .input { padding-left: 30px; width: 100%; }
.input-icon { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); color: var(--color-text-muted); pointer-events: none; }
.chip-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.chip-caption { font-size: 12px; font-weight: 600; color: var(--color-text-secondary); width: 52px; }
.chip { border: 1px solid var(--color-border); background: var(--color-surface); color: var(--color-text-secondary); border-radius: 999px; padding: 3px 11px; font-size: 12.5px; min-height: 28px; transition: background 0.12s, border-color 0.12s, color 0.12s; }
.chip:hover { border-color: var(--color-border-strong); color: var(--color-text); }
.chip.on { background: var(--color-primary-soft); border-color: #b9cbff; color: var(--color-primary-dark); font-weight: 600; }
.chip.cat-new.on { background: #eef1f7; border-color: #c4cad6; color: #42526e; }
.chip.cat-indeterminate.on { background: #e9f2ff; border-color: #9fc2ff; color: #0055cc; }
.chip.cat-done.on { background: #dcfff1; border-color: #8fdcb9; color: #216e4e; }
.selects { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-end; }
.selects .field { min-width: 150px; flex: 1 1 150px; }
.select-sm { min-height: 30px; font-size: 12.5px; }
.check { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; cursor: pointer; }
.query { display: flex; flex-direction: column; gap: 8px; }
.query textarea { resize: vertical; min-height: 64px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }
.actions { display: flex; justify-content: flex-end; }
.error { color: var(--color-danger); display: flex; align-items: center; gap: 6px; margin: 8px 0 0; }
.notice { display: flex; align-items: flex-start; gap: 6px; padding: 8px 10px; border-radius: var(--radius-sm); background: var(--color-warning-soft); color: #8a5a00; margin: 0; }
.result-head { display: flex; align-items: center; gap: 6px 12px; margin: 14px 0 6px; flex-wrap: wrap; }
.select-all { white-space: nowrap; }
.spacer { flex: 1; }
.link { color: var(--color-primary); text-decoration: none; display: inline-flex; align-items: center; gap: 4px; }
.link:hover { text-decoration: underline; }
.empty { padding: 18px; text-align: center; color: var(--color-text-muted); border: 1px dashed var(--color-border); border-radius: var(--radius-md); margin-top: 10px; }
.list { max-height: 38vh; overflow: auto; border: 1px solid var(--color-border); border-radius: var(--radius-md); }
.issue { display: grid; grid-template-columns: 18px 80px minmax(0, 1fr) 120px auto 78px auto; align-items: center; gap: 10px; padding: 7px 12px; border-bottom: 1px solid #f0f2f6; font-size: 13px; cursor: pointer; }
.issue:last-child, .version:last-child { border-bottom: 0; }
.issue:hover, .version:hover { background: var(--color-hover); }
.issue.linked { color: var(--color-text-muted); cursor: default; }
.key { font-weight: 700; font-size: 12px; color: #0052cc; }
.summary-cell { display: flex; flex-direction: column; min-width: 0; gap: 2px; }
.summary { font-weight: 500; }
.tags { display: flex; gap: 4px; flex-wrap: wrap; min-width: 0; }
.tag { font-size: 11px; color: var(--color-text-secondary); background: #f1f3f8; border-radius: 4px; padding: 0 6px; line-height: 18px; max-width: 220px; }
.tag-epic { background: #f3f0ff; color: #5e4db2; }
.assignee { color: var(--color-text-secondary); }
.due { text-align: right; color: var(--color-text-secondary); white-space: nowrap; }
.version { display: grid; grid-template-columns: 18px 16px minmax(0, 1fr) auto 120px auto; align-items: center; gap: 10px; padding: 8px 12px; border-bottom: 1px solid #f0f2f6; font-size: 13px; cursor: pointer; }
.version.disabled { color: var(--color-text-muted); cursor: default; }
.version-mark { color: #0d9488; font-size: 12px; }
.options { display: flex; flex-direction: column; gap: 10px; margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--color-border); }
.option-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.option-label { font-size: 12px; font-weight: 600; color: var(--color-text-secondary); }
.place { max-width: 360px; }
.foot-note { margin-right: auto; }
/* Телефон: фильтры в столбик, строка задачи в две строки */
@media (max-width: 768px) {
  .filters-row { grid-template-columns: 1fr; }
  .chip-caption { width: 100%; }
  .issue {
    grid-template-columns: 18px auto minmax(0, 1fr) auto;
    grid-template-areas: "check key status due" "check summary summary summary" "check assignee badge badge";
    row-gap: 2px;
  }
  .issue > input { grid-area: check; }
  .issue .key { grid-area: key; }
  .issue .summary-cell { grid-area: summary; }
  .issue .assignee { grid-area: assignee; }
  .issue .jira-status { grid-area: status; justify-self: start; }
  .issue .due { grid-area: due; }
  .issue .badge { grid-area: badge; justify-self: start; }
  .version { grid-template-columns: 18px 16px minmax(0, 1fr) auto; }
  .version .due, .version .badge:last-child { grid-column: 3 / -1; text-align: left; }
}
</style>
