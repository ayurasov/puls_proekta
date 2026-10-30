<script setup>
import { computed, inject, nextTick, ref } from 'vue'
import AppIcon from '../../components/common/AppIcon.vue'
import { STATUSES, resolveColor } from '../domain/colors'
import {
  DATE_OPTIONS, JIRA_CATEGORY_OPTIONS, JIRA_OPTIONS, NONE, SET_GROUPS, TRAIT_OPTIONS, applyFilters, clearFilters, emptyFilters,
  filterChips, filterCounts, parseSavedFilters, periodRange, removeSavedFilter, sameFilters, toggleFilter, upsertSavedFilter,
} from '../domain/filters'
import { localStorageGet, localStorageSet, useUiStore } from '../../stores/ui'
import { useAuthStore } from '../../stores/auth'

/**
 * Содержимое окна «Фильтры» (логика — domain/filters.js): сверху «Только мои задачи», слева — группы
 * условий, справа — пункты выбранной группы с числами задач. «Сохранённые» — свои сочетания отметок
 * под именем: у каждого сотрудника и плана свои, хранятся в браузере. На телефоне группы — строкой
 * вкладок над пунктами.
 */
const editor = inject('gantt')
const jira = inject('jira')
const auth = useAuthStore()
const ui = useUiStore()
const view = editor.state.view

const counts = computed(() => filterCounts(editor.itemList, view, editor.filterContext))
const mineAvailable = computed(() => editor.filterContext.mine.size > 0)
// Категории статусов Jira — когда данные задач Jira загружены
const jiraStatuses = computed(() => jira.state.loaded && jira.linkedItems().some((item) => jira.issueFor(item)))

const groups = computed(() => [
  { key: 'statuses', label: 'Статус', options: STATUSES.map((s) => ({ value: s.key, label: s.label, color: s.color })) },
  {
    key: 'assignees', label: 'Исполнитель',
    options: [...editor.assigneeList.map((a) => ({ value: a.key, label: a.name, color: resolveColor(a.color).color })), { value: NONE, label: 'Без исполнителя' }],
  },
  { key: 'categories', label: 'Категория', options: [...editor.categories.map((c) => ({ value: c, label: c })), { value: NONE, label: 'Без категории' }] },
  { key: 'dates', label: 'Сроки', options: DATE_OPTIONS },
  ...(jira.state.connected ? [{
    key: 'jira', label: 'Jira',
    options: [...JIRA_OPTIONS, ...(jiraStatuses.value ? JIRA_CATEGORY_OPTIONS.map((o, i) => ({ ...o, section: i === 0 ? 'Статус в Jira' : '' })) : [])],
  }] : []),
  { key: 'traits', label: 'Тип и прочее', options: TRAIT_OPTIONS },
])
// Открывается группа, где уже что-то отмечено, иначе «Статус»
const current = ref(SET_GROUPS.find((g) => view[g].size && groups.value.some((x) => x.key === g)) || 'statuses')
// Группа, которой больше нет (Jira отключили), — показывается первая
const group = computed(() => (current.value === 'saved' ? null : groups.value.find((g) => g.key === current.value) || groups.value[0]))
const activeCount = computed(() => filterChips(view).filter((c) => c.group !== 'jira' || jira.state.connected).length)
const periodSet = computed(() => Boolean(periodRange('period', null, view.period)))

function setPeriod(edge, value) {
  view.period = { ...view.period, [edge]: value }
  if (value) view.dates.add('period')
}

// ---------- сохранённые фильтры ----------
const storageKey = `puls.filters.${auth.user?.id || 0}.${editor.state.plan?.key || editor.state.planId}`
const saved = ref(parseSavedFilters(localStorageGet(storageKey)))
const newName = ref('')
const nameInput = ref(null)
const savedList = computed(() => saved.value.map((entry) => {
  const filters = applyFilters(emptyFilters(), entry.filters)
  return {
    ...entry,
    summary: filterChips(filters, { assignees: editor.assigneeList }).map((c) => c.label).join(', '),
    current: sameFilters(view, filters),
  }
}))

function persist(list) {
  saved.value = list
  localStorageSet(storageKey, JSON.stringify(list))
}
async function startSave() {
  current.value = 'saved'
  await nextTick()
  nameInput.value?.focus()
}
function saveCurrent() {
  const name = newName.value.trim()
  if (!name || !activeCount.value) return
  const replaced = saved.value.some((e) => e.name.toLowerCase() === name.toLowerCase())
  persist(upsertSavedFilter(saved.value, name, view))
  newName.value = ''
  ui.success(replaced ? `Фильтр «${name}» обновлён` : `Фильтр «${name}» сохранён`)
}
function deleteSaved(entry) {
  const before = saved.value
  persist(removeSavedFilter(before, entry.name))
  ui.toast({ title: `Фильтр «${entry.name}» удалён`, kind: 'success', action: { label: 'Отменить', handler: () => persist(before) } })
}
</script>

<template>
  <div class="filters">
    <label
      class="filter-option filter-mine" :class="{ 'is-off': !mineAvailable }"
      :title="mineAvailable ? 'Задачи, исполнитель которых — вы' : 'Вас нет среди исполнителей плана: исполнителя связывают с сотрудником в окне «Исполнители»'"
    >
      <input type="checkbox" :checked="view.mine" :disabled="!mineAvailable && !view.mine" data-testid="filter-mine" @change="toggleFilter(view, 'mine')" />
      <AppIcon name="users" :size="14" />
      <span class="ellipsis">Только мои задачи</span>
      <span class="muted small">{{ mineAvailable ? counts.mine : 'вас нет среди исполнителей' }}</span>
    </label>

    <div class="filters-body">
      <nav class="filter-nav" aria-label="Группы фильтров">
        <button
          v-for="g in groups" :key="g.key" class="menu-item filter-nav-item" :class="{ 'is-current': group?.key === g.key }" type="button"
          :aria-pressed="group?.key === g.key" :data-testid="`filter-group-${g.key}`" @click="current = g.key"
        >
          <span class="ellipsis">{{ g.label }}</span><span v-if="view[g.key].size" class="count">{{ view[g.key].size }}</span>
        </button>
        <div class="menu-separator nav-separator" />
        <button
          class="menu-item filter-nav-item" :class="{ 'is-current': current === 'saved' }" type="button" :aria-pressed="current === 'saved'"
          data-testid="filter-group-saved" @click="current = 'saved'"
        >
          <AppIcon name="star" :size="13" /><span class="ellipsis">Сохранённые</span><span v-if="saved.length" class="muted small saved-count">{{ saved.length }}</span>
        </button>
      </nav>

      <div v-if="group" class="filter-options scroll-thin">
        <template v-for="option in group.options" :key="option.value">
          <div v-if="option.section" class="menu-label">{{ option.section }}</div>
          <label class="filter-option" :title="option.hint || option.label" :data-testid="`filter-${group.key}-${option.value}`">
            <input type="checkbox" :checked="view[group.key].has(option.value)" @change="toggleFilter(view, group.key, option.value)" />
            <span v-if="option.color" class="status-dot" :style="{ background: option.color }" />
            <span class="ellipsis">{{ option.label }}</span>
            <span class="muted small">{{ option.value === 'period' && !periodSet ? '' : counts[group.key][option.value] || 0 }}</span>
          </label>
          <div v-if="group.key === 'dates' && option.value === 'period'" class="period">
            <input
              class="input input-sm" type="date" :value="view.period.from" aria-label="Период: с" title="С какого дня"
              data-testid="filter-period-from" @change="setPeriod('from', $event.target.value)"
            />
            <span class="muted">—</span>
            <input
              class="input input-sm" type="date" :value="view.period.to" aria-label="Период: по" title="По какой день"
              data-testid="filter-period-to" @change="setPeriod('to', $event.target.value)"
            />
          </div>
        </template>
      </div>

      <div v-else class="filter-options filter-saved scroll-thin">
        <p v-if="!saved.length" class="muted small saved-hint">
          Отметьте фильтры и сохраните сочетание под именем — потом оно включается одним щелчком.
          Список свой у каждого сотрудника и плана и хранится в этом браузере.
        </p>
        <div v-for="entry in savedList" :key="entry.name" class="saved-row" :class="{ 'is-current': entry.current }">
          <button class="saved-apply" type="button" :title="entry.summary" data-testid="saved-filter" @click="applyFilters(view, entry.filters)">
            <AppIcon :name="entry.current ? 'check' : 'filter'" :size="14" />
            <span class="saved-text">
              <span class="ellipsis saved-name">{{ entry.name }}</span>
              <span class="ellipsis muted small">{{ entry.summary || 'без условий' }}</span>
            </span>
          </button>
          <button
            class="btn btn-ghost btn-icon btn-sm" type="button" :title="`Удалить «${entry.name}»`" :aria-label="`Удалить «${entry.name}»`"
            data-testid="saved-filter-delete" @click="deleteSaved(entry)"
          >
            <AppIcon name="trash" :size="14" />
          </button>
        </div>
        <form class="saved-form" @submit.prevent="saveCurrent">
          <input
            ref="nameInput" v-model="newName" class="input input-sm" maxlength="80" placeholder="Название фильтра"
            aria-label="Название фильтра" data-testid="saved-filter-name"
          />
          <button class="btn btn-primary btn-sm" type="submit" :disabled="!newName.trim() || !activeCount" data-testid="saved-filter-save">Сохранить</button>
        </form>
        <p v-if="!activeCount" class="muted small saved-hint">Сначала отметьте фильтры: сохраняется текущее сочетание отметок.</p>
      </div>
    </div>

    <div class="filters-foot">
      <span class="muted small foot-hint">В группе — любое из отмеченного, между группами — все условия</span>
      <button class="btn btn-ghost btn-sm" type="button" :disabled="!activeCount" data-testid="filters-save" @click="startSave">
        <AppIcon name="star" :size="13" /> Сохранить…
      </button>
      <button class="btn btn-ghost btn-sm" type="button" :disabled="!activeCount" data-testid="filters-reset" @click="clearFilters(view)">Сбросить</button>
    </div>
  </div>
</template>

<style scoped>
.filters { display: flex; flex-direction: column; gap: 4px; padding: 2px; }
.filters-body { display: grid; grid-template-columns: 164px minmax(0, 1fr); gap: 6px; border-top: 1px solid var(--color-border); border-bottom: 1px solid var(--color-border); padding: 6px 0; }
.filter-nav { display: flex; flex-direction: column; gap: 1px; padding-right: 6px; border-right: 1px solid var(--color-border); min-width: 0; }
.filter-nav-item { gap: 6px; padding: 6px 8px; font-size: 12.5px; }
.filter-nav-item.is-current { background: var(--color-primary-soft); color: var(--color-primary-dark); font-weight: 600; }
.filter-nav-item .ellipsis { flex: 1; }
.count { background: var(--color-primary); color: #fff; border-radius: 999px; padding: 0 6px; font-size: 10.5px; font-weight: 700; }
.filter-options { height: 300px; overflow: auto; display: flex; flex-direction: column; }
.filter-option { display: flex; align-items: center; gap: 7px; padding: 5px 8px; border-radius: 6px; font-size: 12.5px; cursor: pointer; }
.filter-option:hover { background: var(--color-hover); }
.filter-option .ellipsis { flex: 1; }
.filter-mine { font-weight: 600; }
.filter-mine.is-off { cursor: default; color: var(--color-text-muted); font-weight: 400; }
.filter-mine.is-off:hover { background: transparent; }
.period { display: flex; align-items: center; gap: 6px; padding: 0 8px 6px 31px; }
.period .input { width: auto; flex: 1 1 0; min-width: 0; }
.saved-hint { margin: 4px 8px 8px; line-height: 1.45; }
.saved-row { display: flex; align-items: center; gap: 4px; border-radius: 6px; }
.saved-row:hover { background: var(--color-hover); }
.saved-row.is-current { background: var(--color-primary-soft); }
.saved-apply { flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; border: 0; background: transparent; padding: 6px 8px; text-align: left; color: var(--color-text); border-radius: 6px; }
.saved-row.is-current .saved-apply { color: var(--color-primary-dark); }
.saved-text { display: flex; flex-direction: column; min-width: 0; }
.saved-name { font-size: 12.5px; font-weight: 600; }
.saved-count { margin-left: auto; }
.saved-form { display: flex; gap: 6px; padding: 8px; margin-top: auto; }
.saved-form .input { flex: 1; min-width: 0; }
.filters-foot { display: flex; align-items: center; gap: 4px; padding: 2px 4px 2px 8px; }
.foot-hint { flex: 1; min-width: 0; }
/* Телефон и узкое окно: группы — строкой вкладок над пунктами */
@media (max-width: 640px) {
  .filters-body { grid-template-columns: minmax(0, 1fr); }
  .filter-nav { flex-direction: row; overflow-x: auto; padding: 0 0 6px; border-right: 0; border-bottom: 1px solid var(--color-border); scrollbar-width: none; }
  .filter-nav::-webkit-scrollbar { display: none; }
  .filter-nav-item { width: auto; flex-shrink: 0; border: 1px solid var(--color-border); border-radius: 999px; padding: 4px 10px; }
  .filter-nav-item.is-current { border-color: #c9d6ff; }
  .nav-separator { display: none; }
  .filter-options { height: auto; max-height: min(52vh, 380px); min-height: 200px; }
  .foot-hint { display: none; }
  .filters-foot { justify-content: flex-end; }
}
@media (max-width: 768px) {
  .filter-option { min-height: 40px; }
  .period .input { min-height: 36px; }
}
</style>
