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
const changes = computed(() => (issue.value ? jira.changesFor(props.item) : []))
const hours = (h) => (h === null || h === undefined ? '—' : `${String(h).replace('.', ',')} ч`)

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
    <div v-if="issue === undefined" class="muted small">Загружаем задачу {{ key }} из Jira…</div>
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
        <template v-if="issue.estimateHours !== null || issue.spentHours !== null">
          <dt>Трудозатраты</dt>
          <dd>{{ hours(issue.spentHours) }} из {{ hours(issue.estimateHours) }}<span v-if="issue.remainingHours !== null" class="muted"> · осталось {{ hours(issue.remainingHours) }}</span></dd>
        </template>
      </dl>
      <div v-if="issue.progress !== null" class="progress" :title="`Прогресс по трудозатратам: ${issue.progress}%`"><span :style="{ width: issue.progress + '%' }" /></div>

      <div v-if="changes.length && editor.canEdit" class="changes">
        <div class="changes-head">
          <span class="small"><b>В Jira иначе</b></span>
          <button class="btn btn-sm" type="button" data-testid="jira-apply-all" @click="applyAll">Перенести всё</button>
        </div>
        <div v-for="change in changes" :key="change.field" class="change">
          <span class="muted small label">{{ change.label }}</span>
          <span class="small ellipsis" :title="describe(change)">{{ describe(change) }}</span>
          <button class="link small" type="button" @click="applyOne(change)">Перенести</button>
        </div>
      </div>
      <div v-else-if="!changes.length" class="muted small synced"><AppIcon name="check" :size="12" /> Совпадает со строкой</div>
    </template>
    <div class="foot muted small">
      <span v-if="jira.state.fetchedAt">Данные Jira {{ formatRelative(jira.state.fetchedAt) }}</span>
      <span class="spacer" />
      <HelpLink topic="jira" :size="13" />
      <button class="link small" type="button" :disabled="refreshing" @click="refresh"><AppIcon name="refresh" :size="12" /> {{ refreshing ? 'Обновляем…' : 'Обновить' }}</button>
    </div>
  </div>
</template>

<style scoped>
.jira-card { border: 1px solid var(--color-border); border-radius: var(--radius-md); padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; background: var(--color-surface-2); }
.head { display: flex; align-items: center; gap: 8px; }
.key { font-weight: 700; font-size: 13px; color: #0052cc; text-decoration: none; }
.key:hover { text-decoration: underline; }
.summary { font-size: 13px; line-height: 1.4; }
.facts { display: grid; grid-template-columns: auto 1fr; gap: 3px 12px; margin: 0; font-size: 12.5px; }
.facts dt { color: var(--color-text-muted); }
.facts dd { margin: 0; }
.progress { height: 5px; border-radius: 3px; background: #e6e9f0; overflow: hidden; }
.progress span { display: block; height: 100%; background: #0052cc; }
.changes { border-top: 1px dashed var(--color-border); padding-top: 8px; display: flex; flex-direction: column; gap: 4px; }
.changes-head { display: flex; align-items: center; justify-content: space-between; }
.change { display: grid; grid-template-columns: 74px 1fr auto; gap: 8px; align-items: center; }
.synced { display: flex; align-items: center; gap: 4px; color: #1f7a4c; }
.foot { display: flex; align-items: center; gap: 6px; }
.link { border: 0; background: none; color: var(--color-primary-dark); padding: 0; display: inline-flex; align-items: center; gap: 3px; }
.link:hover:not(:disabled) { text-decoration: underline; }
.link:disabled { opacity: 0.6; }
</style>
