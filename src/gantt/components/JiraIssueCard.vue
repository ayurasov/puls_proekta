<script setup>
import { computed, inject, ref } from 'vue'
import HelpLink from '../../components/common/HelpLink.vue'
import AppIcon from '../../components/common/AppIcon.vue'
import { STATUS_BY_KEY } from '../domain/colors'
import { formatDay } from '../domain/dates'
import { formatRelative } from '../../utils/format'

/** Задача Jira в карточке строки: статус, исполнитель, сроки, трудозатраты и что отличается от строки. */
const props = defineProps({ item: { type: Object, required: true } })
const editor = inject('gantt')
const jira = inject('jira')
const refreshing = ref(false)

const key = computed(() => jira.keyFor(props.item))
const issue = computed(() => jira.issueFor(props.item))
const failure = computed(() => (key.value ? jira.state.failed[key.value] : null))
const fetchedAt = computed(() => (key.value && jira.state.fetchedAtByKey[key.value]) || jira.state.fetchedAt)
const changes = computed(() => (issue.value ? jira.changesFor(props.item) : []))
const hours = (h) => `${String(h).replace('.', ',')} ч`
const known = (h) => h !== null && h !== undefined
/** «40 ч из 160 ч · осталось 120 ч» — только то, что есть в Jira. */
const effort = computed(() => {
  const i = issue.value
  if (!i) return ''
  const parts = []
  if (known(i.spentHours) && known(i.estimateHours)) parts.push(`${hours(i.spentHours)} из ${hours(i.estimateHours)}`)
  else if (known(i.spentHours)) parts.push(`затрачено ${hours(i.spentHours)}`)
  else if (known(i.estimateHours)) parts.push(`оценка ${hours(i.estimateHours)}`)
  if (known(i.remainingHours)) parts.push(`осталось ${hours(i.remainingHours)}`)
  return parts.join(' · ')
})

function describe(change) {
  if (change.field === 'status') return `${STATUS_BY_KEY[change.from]?.label} → ${STATUS_BY_KEY[change.to]?.label}`
  if (change.field === 'dates') return `${formatDay(change.from[0])} — ${formatDay(change.from[1])} → ${formatDay(change.to[0])} — ${formatDay(change.to[1])}`
  if (change.field === 'date') return `${formatDay(change.from)} → ${formatDay(change.to)}`
  if (change.field === 'progress') return `${change.from}% → ${change.to}%`
  return `«${change.from}» → «${change.to}»`
}
function applyOne(change) {
  jira.apply([{ key: props.item.key, fields: change.fields }], `${change.label} из Jira`)
}
function applyAll() {
  jira.apply(changes.value.map((c) => ({ key: props.item.key, fields: c.fields })), `Данные из Jira: «${props.item.name}»`)
}
async function refresh() {
  refreshing.value = true
  try { await jira.fetchKeys([key.value], { refresh: true }) } finally { refreshing.value = false }
}
</script>

<template>
  <div v-if="key" class="jira-card" data-testid="jira-card">
    <div v-if="issue === undefined && failure" class="failure small" role="status">
      <AppIcon name="warning" :size="13" /> <span>Задачу {{ key }} не удалось получить: {{ failure }}</span>
    </div>
    <div v-else-if="issue === undefined" class="muted small">Загружаем задачу {{ key }} из Jira…</div>
    <div v-else-if="issue === null" class="muted small">
      <AppIcon name="warning" :size="13" /> Задачи {{ key }} нет в Jira или учётной записи подключения она недоступна.
    </div>
    <template v-else>
      <div class="head">
        <a class="key" :href="issue.url" target="_blank" rel="noopener noreferrer">{{ issue.key }}</a>
        <span v-if="issue.type" class="muted small">{{ issue.type }}</span>
        <span class="spacer" />
        <span class="jira-status" :class="`cat-${issue.statusCategory}`">{{ issue.status }}</span>
      </div>
      <div class="summary">{{ issue.summary }}</div>
      <dl class="facts">
        <dt>Исполнитель</dt><dd>{{ issue.assignee || 'не назначен' }}</dd>
        <template v-if="issue.start"><dt>Начало</dt><dd>{{ formatDay(issue.start) }}</dd></template>
        <dt>Срок</dt><dd>{{ issue.due ? formatDay(issue.due) : 'не указан' }}</dd>
        <template v-if="issue.resolved"><dt>Решена</dt><dd>{{ formatDay(issue.resolved) }}</dd></template>
        <template v-if="effort">
          <dt>Трудозатраты</dt>
          <dd>{{ effort }}</dd>
        </template>
      </dl>
      <div v-if="issue.progress !== null" class="progress" :title="`Прогресс по трудозатратам: ${issue.progress}%`"><span :style="{ width: issue.progress + '%' }" /></div>

      <div v-if="changes.length" class="changes">
        <div class="changes-head">
          <span class="small"><b>В Jira иначе</b></span>
          <button v-if="editor.canEdit" class="btn btn-sm" type="button" data-testid="jira-apply-all" @click="applyAll">Перенести всё</button>
        </div>
        <div v-for="change in changes" :key="change.field" class="change">
          <span class="muted small label">{{ change.label }}</span>
          <span class="small value">{{ describe(change) }}<span v-if="change.note" class="muted"> · {{ change.note }}</span></span>
          <button v-if="editor.canEdit" class="link small" type="button" @click="applyOne(change)">Перенести</button>
        </div>
      </div>
      <div v-else-if="!changes.length" class="muted small synced"><AppIcon name="check" :size="12" /> Совпадает со строкой</div>
    </template>
    <div class="foot muted small">
      <span v-if="fetchedAt && issue !== undefined">Данные Jira {{ formatRelative(fetchedAt) }}</span>
      <span class="spacer" />
      <HelpLink topic="jira" :size="13" />
      <button class="link small" type="button" :disabled="refreshing" @click="refresh"><AppIcon name="refresh" :size="12" /> {{ refreshing ? 'Обновляем…' : failure ? 'Повторить' : 'Обновить' }}</button>
    </div>
  </div>
</template>

<style scoped>
.jira-card { border: 1px solid #d9e3fb; border-radius: 12px; padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; background: var(--color-surface); box-shadow: var(--shadow-1); }
.head { display: flex; align-items: center; gap: 8px; }
.key { display: inline-flex; align-items: center; gap: 6px; padding: 2px 8px 2px 3px; border-radius: 999px; background: #e9f2ff; font-weight: 700; font-size: 12.5px; color: #0052cc; text-decoration: none; }
.key:hover { text-decoration: underline; }
.key::before { content: 'J' / ''; width: 18px; height: 18px; border-radius: 50%; background: #0052cc; color: #fff; font-size: 10px; font-weight: 800; display: inline-flex; align-items: center; justify-content: center; }
.summary { font-size: 13.5px; font-weight: 600; line-height: 1.4; }
.facts { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin: 0; font-size: 12.5px; }
.facts dt { color: var(--color-text-muted); }
.facts dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.progress { height: 6px; border-radius: 999px; background: #e6e9f0; overflow: hidden; }
.progress span { display: block; height: 100%; border-radius: inherit; background: #0052cc; }
.changes { padding: 10px 12px; border-radius: 10px; background: var(--color-warning-soft); border: 1px solid #f3dfb8; display: flex; flex-direction: column; gap: 6px; }
.changes-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: #8a5a10; }
.change { display: grid; grid-template-columns: 74px 1fr auto; gap: 8px; align-items: baseline; }
.change .value { overflow-wrap: anywhere; }
.synced { align-self: flex-start; display: inline-flex; align-items: center; gap: 5px; padding: 2px 10px 2px 8px; border-radius: 999px; background: var(--color-success-soft); color: #1f7a4c; font-weight: 600; }
.failure { display: flex; align-items: flex-start; gap: 6px; color: #b42318; line-height: 1.4; }
.failure :deep(svg) { flex-shrink: 0; margin-top: 2px; }
.foot { display: flex; align-items: center; gap: 6px; padding-top: 8px; border-top: 1px solid #eef0f5; }
.link { border: 0; background: none; color: var(--color-primary-dark); padding: 0; display: inline-flex; align-items: center; gap: 3px; }
.link:hover:not(:disabled) { text-decoration: underline; }
.link:disabled { opacity: 0.6; }
/* Телефон: цели касания не меньше 40 px */
@media (max-width: 768px) {
  .link { min-height: 40px; }
}
</style>
