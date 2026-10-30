<script setup>
import { computed } from 'vue'
import AppIcon from '../common/AppIcon.vue'
import UserAvatar from '../common/UserAvatar.vue'
import { countLabel, pluralize } from '../../utils/format'

/**
 * Карточка подразделения на оргсхеме (как в todo-planner): тип цветом, название, основной руководитель
 * (и сколько у него подчинённых во всей ветке), численность своя и всей ветки, раскрытие вложенных.
 * Клавиатура: Tab — на название, Enter — открыть карточку справа; отдельные кнопки — руководитель и раскрытие.
 */
const props = defineProps({
  dep: { type: Object, required: true },
  head: { type: Object, default: null },
  headTeam: { type: Number, default: 0 },
  extraHeads: { type: Number, default: 0 },
  kindLabel: { type: String, default: '' },
  kindColor: { type: String, default: '#64748b' },
  showKind: { type: Boolean, default: true },
  expanded: { type: Boolean, default: false },
  onPath: { type: Boolean, default: false },
  selected: { type: Boolean, default: false },
  mine: { type: Boolean, default: false },
})
const emit = defineEmits(['select', 'toggle', 'select-person'])
const children = computed(() => props.dep.childCount || 0)
</script>

<template>
  <div
    class="unit-node" :class="{ 'is-path': onPath, 'is-selected': selected }" :style="{ '--k': kindColor }"
    :data-unit-id="dep.id" @click="emit('select', dep.id)"
  >
    <span v-if="mine" class="mine-badge">Вы здесь</span>
    <button type="button" class="card-main" :aria-current="selected ? 'true' : undefined" :title="dep.name" @click.stop="emit('select', dep.id)">
      <span v-if="showKind" class="kind">{{ kindLabel }}</span>
      <span class="name">{{ dep.name }}</span>
      <span class="sr-only">, {{ countLabel(dep.employeeCount, 'сотрудник', 'сотрудника', 'сотрудников') }}</span>
    </button>
    <button v-if="head" type="button" class="head" :title="`Руководитель: ${head.name}`" @click.stop="emit('select-person', head.id)">
      <UserAvatar :user="head" :size="28" />
      <span class="head-text">
        <span class="head-name">{{ head.name }}<span v-if="extraHeads" class="extra" :title="`Ещё руководителей: ${extraHeads}`">+{{ extraHeads }}</span></span>
        <small>{{ head.position || 'Руководитель' }}</small>
      </span>
      <span v-if="headTeam" class="team" :title="`Подчинённых во всей ветке: ${headTeam}`"><AppIcon name="users" :size="11" />{{ headTeam }}</span>
    </button>
    <p v-else class="no-head">Руководитель не назначен</p>
    <div class="stats" aria-hidden="true">
      <span class="stat"><b>{{ dep.employeeCount }}</b> {{ pluralize(dep.employeeCount, 'сотрудник', 'сотрудника', 'сотрудников') }}</span>
      <span v-if="children" class="stat stat-muted" title="Вместе с вложенными подразделениями">{{ dep.branchEmployeeCount }} в ветке</span>
    </div>
    <button
      v-if="children" type="button" class="toggle" :aria-expanded="expanded"
      :aria-label="`${expanded ? 'Свернуть' : 'Показать'} вложенные: ${dep.name}, ${countLabel(children, 'подразделение', 'подразделения', 'подразделений')}`"
      @click.stop="emit('toggle', dep.id)"
    >
      {{ countLabel(children, 'подразделение', 'подразделения', 'подразделений') }}<AppIcon :name="expanded ? 'chevronUp' : 'chevronDown'" :size="12" />
    </button>
  </div>
</template>

<style scoped>
.unit-node { position: relative; width: 236px; display: flex; flex-direction: column; background: var(--color-surface); border: 1.5px solid var(--color-border); border-radius: var(--radius-md); box-shadow: var(--shadow-1); text-align: left; cursor: pointer; transition: border-color 0.15s, box-shadow 0.15s; }
.unit-node::before { content: ''; position: absolute; inset: -1.5px -1.5px auto; height: 4px; border-radius: var(--radius-md) var(--radius-md) 0 0; background: var(--k); }
.unit-node:hover { box-shadow: var(--shadow-2); }
.unit-node.is-path { border-color: color-mix(in srgb, var(--color-primary) 45%, white); }
.unit-node.is-selected { border-color: var(--color-primary); box-shadow: 0 0 0 3px rgba(79, 124, 255, 0.22); }
.mine-badge { position: absolute; top: -9px; right: 10px; z-index: 1; padding: 1px 8px; border-radius: 999px; background: var(--color-primary-dark); color: #fff; font-size: 10px; font-weight: 700; letter-spacing: 0.03em; text-transform: uppercase; }
.card-main { display: flex; flex-direction: column; align-items: flex-start; gap: 3px; padding: 12px 12px 6px; border: 0; background: none; text-align: left; color: var(--color-text); border-radius: var(--radius-md) var(--radius-md) 0 0; }
.card-main:focus-visible { outline-offset: -3px; }
@supports selector(:has(a)) {
  .unit-node:has(.card-main:focus-visible) { outline: 2px solid var(--color-primary); outline-offset: 2px; }
  .card-main:focus-visible { outline: none; }
}
.kind { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em; color: color-mix(in srgb, var(--k) 80%, black); background: color-mix(in srgb, var(--k) 12%, white); padding: 1px 6px; border-radius: 4px; white-space: nowrap; }
.name { font-size: 13.5px; font-weight: 700; line-height: 1.3; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }
.head { display: flex; align-items: center; gap: 8px; margin: 0 6px; padding: 5px 6px; border: 0; border-radius: 8px; background: none; text-align: left; color: var(--color-text); min-width: 0; }
.head:hover { background: var(--color-hover); }
.head-text { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.head-name { font-size: 12.5px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.head-text small { font-size: 11px; color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.extra { margin-left: 4px; color: var(--color-text-muted); font-weight: 500; }
.team { display: inline-flex; align-items: center; gap: 3px; padding: 1px 7px; border-radius: 999px; background: #eef1f7; color: var(--color-text-secondary); font-size: 11px; font-weight: 600; flex-shrink: 0; }
.no-head { margin: 2px 12px 2px; font-size: 11.5px; color: #8a5300; }
.stats { display: flex; flex-wrap: wrap; gap: 6px; padding: 6px 12px 10px; }
.stat { padding: 1px 8px; border: 1px solid var(--color-border); border-radius: 999px; font-size: 11.5px; color: var(--color-text); white-space: nowrap; }
.stat-muted { color: var(--color-text-muted); }
.toggle { display: flex; align-items: center; justify-content: center; gap: 4px; margin-top: auto; padding: 7px; border: 0; border-top: 1px solid var(--color-border); border-radius: 0 0 var(--radius-md) var(--radius-md); background: var(--color-surface-2); color: var(--color-primary-dark); font-size: 12px; font-weight: 600; }
.toggle:hover { background: var(--color-primary-soft); }
.is-path .toggle, .is-selected .toggle { background: var(--color-primary-soft); }
@media (max-width: 768px) {
  .unit-node { width: 216px; }
  .toggle { min-height: 40px; }
  .head { min-height: 40px; }
}
</style>
