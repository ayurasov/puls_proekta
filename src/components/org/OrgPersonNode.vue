<script setup>
import AppIcon from '../common/AppIcon.vue'
import UserAvatar from '../common/UserAvatar.vue'
import { countLabel } from '../../utils/format'

/**
 * Карточка сотрудника на схеме «Люди» (как в todo-planner): должность, подразделение, прямые подчинённые
 * и вся ветка, раскрытие команды. Клавиатура: Tab — на имя, Enter — открыть карточку справа.
 */
defineProps({
  person: { type: Object, required: true },
  unitName: { type: String, default: '' },
  reports: { type: Number, default: 0 },
  branch: { type: Number, default: 0 },
  expanded: { type: Boolean, default: false },
  onPath: { type: Boolean, default: false },
  selected: { type: Boolean, default: false },
  me: { type: Boolean, default: false },
})
const emit = defineEmits(['select', 'toggle'])
</script>

<template>
  <div class="person-node" :class="{ 'is-path': onPath, 'is-selected': selected }" :data-person-id="person.id" @click="emit('select', person.id)">
    <span v-if="me" class="me-badge">Вы</span>
    <button type="button" class="card-main" :aria-current="selected ? 'true' : undefined" @click.stop="emit('select', person.id)">
      <UserAvatar :user="person" :size="36" />
      <span class="text">
        <strong>{{ person.name }}</strong>
        <small v-if="person.position">{{ person.position }}</small>
        <small v-if="unitName" class="unit">{{ unitName }}</small>
      </span>
    </button>
    <div v-if="reports" class="counts">
      <span title="Прямых подчинённых"><AppIcon name="users" :size="11" /> {{ reports }} прямых</span>
      <span v-if="branch > reports" title="Подчинённых во всей ветке">{{ branch }} всего</span>
    </div>
    <button
      v-if="reports" type="button" class="toggle" :aria-expanded="expanded"
      :aria-label="`${expanded ? 'Свернуть' : 'Показать'} подчинённых: ${person.name}, ${countLabel(reports, 'человек', 'человека', 'человек')}`"
      @click.stop="emit('toggle', person.id)"
    >
      Подчинённые: {{ reports }}<AppIcon :name="expanded ? 'chevronUp' : 'chevronDown'" :size="12" />
    </button>
  </div>
</template>

<style scoped>
.person-node { position: relative; width: 212px; display: flex; flex-direction: column; background: var(--color-surface); border: 1.5px solid var(--color-border); border-radius: var(--radius-md); box-shadow: var(--shadow-1); cursor: pointer; transition: border-color 0.15s, box-shadow 0.15s; }
.person-node:hover { box-shadow: var(--shadow-2); }
.person-node.is-path { border-color: color-mix(in srgb, var(--color-primary) 45%, white); }
.person-node.is-selected { border-color: var(--color-primary); box-shadow: 0 0 0 3px rgba(79, 124, 255, 0.22); }
.me-badge { position: absolute; top: -9px; right: 10px; z-index: 1; padding: 1px 8px; border-radius: 999px; background: var(--color-primary-dark); color: #fff; font-size: 10px; font-weight: 700; }
.card-main { display: flex; gap: 9px; align-items: center; padding: 10px 10px 8px; border: 0; background: none; text-align: left; color: var(--color-text); min-width: 0; border-radius: var(--radius-md); }
.card-main:focus-visible { outline-offset: -3px; }
@supports selector(:has(a)) {
  .person-node:has(.card-main:focus-visible) { outline: 2px solid var(--color-primary); outline-offset: 2px; }
  .card-main:focus-visible { outline: none; }
}
.text { display: flex; flex-direction: column; min-width: 0; }
.text strong { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.text small { font-size: 11px; color: var(--color-text-muted); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.text .unit { color: var(--color-primary-dark); -webkit-line-clamp: 1; }
.counts { display: flex; gap: 6px; flex-wrap: wrap; padding: 0 10px 8px; font-size: 11px; color: var(--color-text-secondary); }
.counts span { display: inline-flex; align-items: center; gap: 3px; padding: 1px 7px; border-radius: 999px; background: #eef1f7; }
.toggle { display: flex; align-items: center; justify-content: center; gap: 4px; margin-top: auto; padding: 6px; border: 0; border-top: 1px solid var(--color-border); border-radius: 0 0 var(--radius-md) var(--radius-md); background: var(--color-surface-2); color: var(--color-primary-dark); font-size: 11.5px; font-weight: 600; }
.toggle:hover { background: var(--color-primary-soft); }
.is-path .toggle, .is-selected .toggle { background: var(--color-primary-soft); }
@media (max-width: 768px) {
  .person-node { width: 200px; }
  .toggle { min-height: 40px; }
}
</style>
