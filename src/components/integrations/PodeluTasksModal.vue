<script setup>
import { computed, ref } from 'vue'
import BaseModal from '../common/BaseModal.vue'
import HelpLink from '../common/HelpLink.vue'
import AppIcon from '../common/AppIcon.vue'
import SegmentedControl from '../common/SegmentedControl.vue'
import PodeluTaskList from './PodeluTaskList.vue'
import { groupByItem } from '../../integrations/podeluTasks'

/**
 * Задачи «По Делу», которые ссылаются на проект: сначала связанные с проектом целиком, затем по строкам плана.
 * Щелчок по строке — переход к ней на диаграмме; по задаче — открыть её в «По Делу».
 */
const props = defineProps({
  tasks: { type: Object, required: true }, // createPodeluTasks()
  items: { type: Object, default: () => ({}) }, // строки открытого плана: { ключ: строка }
  planId: { type: Number, default: null },
  projectTitle: { type: String, default: '' },
})
const emit = defineEmits(['close', 'reveal'])
const filter = ref('open')
const FILTERS = computed(() => [
  { value: 'open', label: 'Открытые', badge: props.tasks.state.tasks.filter((t) => !t.done).length },
  { value: 'all', label: 'Все', badge: props.tasks.state.tasks.length },
])
const visible = computed(() => props.tasks.state.tasks.filter((t) => filter.value === 'all' || !t.done))
const groups = computed(() => groupByItem(visible.value, props.items))
const today = new Date().toISOString().slice(0, 10)
function reveal(group) {
  emit('reveal', { planId: group.planId, itemKey: group.itemKey })
  emit('close')
}
</script>

<template>
  <BaseModal :title="`Задачи в «По Делу» · ${projectTitle}`" size="wide" @close="emit('close')">
    <template #header-actions><HelpLink topic="podelu-tasks" /></template>
    <div class="top">
      <p class="muted small">Задачи сотрудников в «По Делу», связанные с этим проектом или строками его планов. Связь задают в карточке задачи
        «По Делу» — раздел «Пульс проекта».</p>
      <div class="actions">
        <SegmentedControl v-model="filter" :options="FILTERS" size="sm" aria-label="Какие задачи показать" />
        <button class="btn btn-sm btn-ghost btn-icon" type="button" title="Обновить" :disabled="tasks.state.loading" @click="tasks.load()"><AppIcon name="refresh" :size="14" /></button>
      </div>
    </div>
    <p v-if="tasks.state.error" class="error small" role="alert"><AppIcon name="warning" :size="13" /> {{ tasks.state.error }}</p>
    <div v-if="tasks.state.loading && !tasks.state.loaded" class="muted empty">Загружаем задачи из «По Делу»…</div>
    <div v-else-if="!visible.length" class="empty" data-testid="podelu-tasks-empty">
      <AppIcon name="checklist" :size="22" />
      <strong>{{ tasks.state.tasks.length ? 'Открытых задач нет' : 'Пока ни одна задача «По Делу» не ссылается на проект' }}</strong>
      <span class="muted small">{{ tasks.state.tasks.length ? 'Все связанные задачи выполнены — переключитесь на «Все».' : 'В «По Делу» откройте задачу и в разделе «Пульс проекта» выберите этот проект или строку плана.' }}</span>
    </div>
    <div v-else class="groups scroll-thin" data-testid="podelu-tasks-list">
      <section v-for="group in groups" :key="`${group.planId}:${group.itemKey}`" class="group">
        <header class="group-head">
          <template v-if="group.itemKey">
            <AppIcon name="gantt" :size="14" class="muted" />
            <button v-if="items[group.itemKey] || group.planId !== planId" class="row-link ellipsis" type="button" :title="`Перейти к строке «${group.itemName}»`" @click="reveal(group)">{{ group.itemName }}</button>
            <span v-else class="ellipsis">{{ group.itemName }} <span class="muted small">(строка удалена)</span></span>
          </template>
          <template v-else><AppIcon name="folder" :size="14" class="muted" /> <span>Проект целиком</span></template>
          <span class="count">{{ group.tasks.length }}</span>
        </header>
        <PodeluTaskList :tasks="group.tasks" :today="today" />
      </section>
    </div>
    <p v-if="tasks.state.truncated" class="muted small">Показаны первые 200 задач.</p>
    <template #footer>
      <button class="btn" type="button" @click="emit('close')">Закрыть</button>
    </template>
  </BaseModal>
</template>

<style scoped>
.top { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; flex-wrap: wrap; }
.top p { margin: 0; line-height: 1.5; flex: 1 1 320px; }
.actions { display: flex; align-items: center; gap: 6px; }
.error { color: var(--color-danger); display: flex; align-items: center; gap: 6px; }
.empty { display: flex; flex-direction: column; align-items: center; gap: 6px; text-align: center; padding: 28px 16px; margin-top: 12px; border: 1px dashed var(--color-border); border-radius: var(--radius-md); color: var(--color-text-secondary); }
.groups { margin-top: 12px; max-height: 58vh; overflow: auto; display: flex; flex-direction: column; gap: 12px; }
.group { border: 1px solid var(--color-border); border-radius: var(--radius-md); padding: 4px 12px 2px; }
.group-head { display: flex; align-items: center; gap: 8px; padding: 8px 0 6px; font-weight: 600; font-size: 13px; border-bottom: 1px solid var(--color-border); }
.row-link { border: 0; background: none; padding: 0; font: inherit; color: var(--color-primary-dark); text-align: left; min-width: 0; }
.row-link:hover { text-decoration: underline; }
.count { margin-left: auto; font-size: 11px; font-weight: 700; padding: 1px 8px; border-radius: 999px; background: #eef1f7; color: var(--color-text-secondary); }
</style>
