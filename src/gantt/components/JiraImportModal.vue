<script setup>
import { computed, inject, ref } from 'vue'
import { api } from '../../api/client'
import BaseModal from '../../components/common/BaseModal.vue'
import AppIcon from '../../components/common/AppIcon.vue'
import { addDays, diffDays, formatDay } from '../domain/dates'
import { STATUS_FROM_CATEGORY } from '../jira'

/** Добавить строки из задач Jira: по ключам или запросу JQL; уже привязанные к плану задачи не повторяются. */
const emit = defineEmits(['close'])
const editor = inject('gantt')
const jira = inject('jira')
const mode = ref('keys')
const keys = ref('')
const jql = ref('')
const busy = ref(false)
const error = ref('')
const result = ref(null)
const picked = ref(new Set())

const groups = computed(() => editor.itemList.filter((i) => i.type === 'group'))
const selectedItem = computed(() => editor.state.items[editor.state.view.selectedKey] || null)
const parentKey = ref(selectedItem.value?.type === 'group' ? selectedItem.value.key : (selectedItem.value?.parentKey || ''))
const linked = computed(() => new Set(jira.linkedItems().map((item) => jira.keyFor(item))))

async function search() {
  error.value = ''
  busy.value = true
  try {
    const body = mode.value === 'keys' ? { keys: keys.value } : { jql: jql.value }
    result.value = await api.post('/jira/search', body)
    picked.value = new Set(result.value.issues.filter((i) => !linked.value.has(i.key)).map((i) => i.key))
  } catch (err) {
    error.value = err.message
    result.value = null
  } finally { busy.value = false }
}
function toggle(key) {
  const next = new Set(picked.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  picked.value = next
}
const chosen = computed(() => (result.value?.issues || []).filter((i) => picked.value.has(i.key)))

/** Сроки строки: из Jira, если есть; без начала — пять дней до срока, без срока — пять дней с сегодня. */
function datesFor(issue) {
  const start = issue.start || (issue.due ? addDays(issue.due, -4) : editor.state.today)
  const end = issue.due && issue.due >= start ? issue.due : addDays(start, 4)
  return { start, end }
}
function add() {
  const list = chosen.value.map((issue) => {
    const { start, end } = datesFor(issue)
    const status = STATUS_FROM_CATEGORY[issue.statusCategory] || 'new'
    const data = { name: issue.summary || issue.key, jiraUrl: issue.url, start, end, status }
    if (status === 'done') {
      const done = issue.resolved || end
      Object.assign(data, { progress: 100, completedDate: done, offset: diffDays(start, done) - diffDays(start, end) })
    }
    else if (issue.progress !== null) data.progress = issue.progress
    return { type: 'task', data }
  })
  const keys = editor.createItems(list, { parentKey: parentKey.value || null, label: `Задачи из Jira (${list.length})` })
  if (!keys.length) return
  for (const issue of chosen.value) jira.state.issues[issue.key] = issue
  editor.revealItem(keys[keys.length - 1])
  editor.select(keys[0])
  emit('close')
}
</script>

<template>
  <BaseModal title="Задачи из Jira" size="wide" @close="emit('close')">
    <div class="segmented">
      <button type="button" :class="{ 'is-active': mode === 'keys' }" @click="mode = 'keys'">По ключам</button>
      <button type="button" :class="{ 'is-active': mode === 'jql' }" @click="mode = 'jql'">Запрос JQL</button>
    </div>
    <form class="query" @submit.prevent="search">
      <label v-if="mode === 'keys'" class="field">
        <span class="field-label">Ключи задач</span>
        <textarea v-model="keys" class="input" rows="3" placeholder="ABC-101, ABC-102&#10;XYZ-7" data-testid="jira-import-keys" />
        <span class="field-hint">Через запятую, пробел или с новой строки — можно вставить список из Jira.</span>
      </label>
      <label v-else class="field">
        <span class="field-label">Запрос JQL</span>
        <textarea v-model="jql" class="input mono" rows="3" placeholder="project = ABC AND fixVersion = &quot;Релиз 2&quot; ORDER BY rank" />
        <span class="field-hint">Не больше 200 задач. Строка запроса — как в поиске Jira.</span>
      </label>
      <div class="actions">
        <button class="btn" type="submit" :disabled="busy || !(mode === 'keys' ? keys.trim() : jql.trim())" data-testid="jira-import-search">
          <AppIcon name="search" :size="14" /> {{ busy ? 'Ищем…' : 'Найти' }}
        </button>
      </div>
    </form>
    <p v-if="error" class="error small"><AppIcon name="warning" :size="13" /> {{ error }}</p>

    <template v-if="result">
      <p v-if="!result.issues.length" class="muted">Ничего не найдено.</p>
      <div v-else class="list scroll-thin" data-testid="jira-import-list">
        <label v-for="issue in result.issues" :key="issue.key" class="issue" :class="{ linked: linked.has(issue.key) }">
          <input type="checkbox" :checked="picked.has(issue.key)" @change="toggle(issue.key)" />
          <span class="key">{{ issue.key }}</span>
          <span class="summary ellipsis" :title="issue.summary">{{ issue.summary }}</span>
          <span class="jira-status" :class="`cat-${issue.statusCategory}`">{{ issue.status }}</span>
          <span class="muted small due">{{ issue.due ? formatDay(issue.due) : 'без срока' }}</span>
          <span v-if="linked.has(issue.key)" class="badge">уже в плане</span>
        </label>
      </div>
      <p v-if="result.missing.length || result.invalid.length" class="muted small">
        <template v-if="result.missing.length">Не найдены или недоступны: {{ result.missing.join(', ') }}. </template>
        <template v-if="result.invalid.length">Не похожи на ключ задачи: {{ result.invalid.join(', ') }}.</template>
      </p>
      <p v-if="result.truncated" class="muted small">Показаны первые 200 задач — уточните запрос.</p>
      <label v-if="result.issues.length" class="field place">
        <span class="field-label">Куда добавить</span>
        <select v-model="parentKey" class="select">
          <option value="">В конец плана</option>
          <option v-for="g in groups" :key="g.key" :value="g.key">В группу «{{ g.name }}»</option>
        </select>
      </label>
    </template>

    <template #footer>
      <button class="btn" type="button" @click="emit('close')">Отмена</button>
      <button class="btn btn-primary" type="button" :disabled="!chosen.length" data-testid="jira-import-add" @click="add">
        Добавить<span v-if="chosen.length"> ({{ chosen.length }})</span>
      </button>
    </template>
  </BaseModal>
</template>

<style scoped>
.query { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
.query textarea { resize: vertical; min-height: 64px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }
.actions { display: flex; justify-content: flex-end; }
.error { color: var(--color-danger); display: flex; align-items: center; gap: 6px; }
.list { max-height: 42vh; overflow: auto; border: 1px solid var(--color-border); border-radius: var(--radius-md); margin-top: 8px; }
.issue { display: grid; grid-template-columns: 18px 84px minmax(0, 1fr) auto 78px auto; align-items: center; gap: 8px; padding: 6px 12px; border-bottom: 1px solid #f0f2f6; font-size: 13px; cursor: pointer; }
.issue:last-child { border-bottom: 0; }
.issue:hover { background: var(--color-hover); }
.issue.linked { color: var(--color-text-muted); }
.key { font-weight: 700; font-size: 12px; color: #0052cc; }
.due { text-align: right; }
.place { margin-top: 12px; max-width: 360px; }
</style>
