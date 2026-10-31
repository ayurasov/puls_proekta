<script setup>
import AppIcon from '../common/AppIcon.vue'
import { formatDay } from '../../gantt/domain/dates'
import { isOverdue } from '../../integrations/podeluTasks'

/** Список задач «По Делу»: статус, название (ссылка в «По Делу»), исполнитель и срок. */
defineProps({
  tasks: { type: Array, required: true },
  today: { type: String, default: () => new Date().toISOString().slice(0, 10) },
  compact: { type: Boolean, default: false },
})
</script>

<template>
  <ul class="podelu-tasks" :class="{ compact }">
    <li v-for="task in tasks" :key="task.id" class="task" :class="{ done: task.done }">
      <span class="status" :class="task.done ? 'is-done' : 'is-open'" :title="task.statusLabel">{{ task.statusLabel || (task.done ? 'Выполнена' : 'Открыта') }}</span>
      <a v-if="task.url" class="title ellipsis" :href="task.url" target="_blank" rel="noopener noreferrer" :title="`${task.title} — открыть в «По Делу»`">{{ task.title }}</a>
      <span v-else class="title ellipsis">{{ task.title }}</span>
      <span class="who ellipsis">{{ task.assignee?.name || 'без исполнителя' }}</span>
      <span class="due" :class="{ overdue: isOverdue(task, today) }">
        <AppIcon v-if="isOverdue(task, today)" name="alarm" :size="12" />
        {{ task.due ? formatDay(task.due) : 'без срока' }}
      </span>
    </li>
  </ul>
</template>

<style scoped>
.podelu-tasks { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.task { display: grid; grid-template-columns: auto minmax(0, 1fr) minmax(0, 150px) 92px; gap: 10px; align-items: center; padding: 8px 4px; border-bottom: 1px solid #f0f2f6; font-size: 13px; }
.task:last-child { border-bottom: 0; }
.compact .task { grid-template-columns: auto minmax(0, 1fr) 84px; padding: 6px 0; }
.compact .who { display: none; }
.status { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 999px; white-space: nowrap; max-width: 130px; overflow: hidden; text-overflow: ellipsis; }
.is-open { background: #e9f2ff; color: #0055cc; }
.is-done { background: #dcfff1; color: #216e4e; }
.title { color: var(--color-text); text-decoration: none; font-weight: 500; }
a.title:hover { color: var(--color-primary); text-decoration: underline; }
.done .title { color: var(--color-text-muted); text-decoration: line-through; text-decoration-color: rgba(107, 114, 128, 0.5); }
.who { color: var(--color-text-secondary); font-size: 12.5px; }
.due { text-align: right; color: var(--color-text-secondary); font-size: 12.5px; white-space: nowrap; display: inline-flex; align-items: center; justify-content: flex-end; gap: 4px; font-variant-numeric: tabular-nums; }
.due.overdue { color: var(--color-danger); font-weight: 600; }
@media (max-width: 640px) {
  .task { grid-template-columns: auto minmax(0, 1fr); row-gap: 2px; }
  .who, .due { grid-column: 2; text-align: left; justify-content: flex-start; }
}
</style>
