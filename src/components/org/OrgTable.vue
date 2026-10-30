<script setup>
import { computed, ref } from 'vue'
import { saveBlob } from '../../api/client'
import AppIcon from '../common/AppIcon.vue'
import { countLabel } from '../../utils/format'
import { TABLE_COLUMNS, filterRows, sortRows, tableCsv } from '../../utils/orgViews'

/**
 * Таблица подразделений (как в todo-planner): путь, тип, руководитель, численность своя и всей
 * ветки, вложенные. Сортировка по любой колонке, фильтр по типу и поиску, выгрузка того, что на экране, в CSV.
 */
const props = defineProps({
  rows: { type: Array, required: true },
  kinds: { type: Array, default: () => [] },
  kindColor: { type: Function, required: true },
  needle: { type: String, default: '' },
  selectedId: { type: String, default: null },
})
const emit = defineEmits(['select', 'select-person'])

const sortKey = ref('path')
const sortDir = ref('asc')
const kindFilter = ref('')

const visible = computed(() => {
  const rows = filterRows(props.rows, props.needle).filter((row) => !kindFilter.value || row.kind === kindFilter.value)
  return sortRows(rows, sortKey.value, sortDir.value)
})
const staff = computed(() => visible.value.reduce((sum, row) => sum + row.employeeCount, 0))
// Отступ по уровню — только в порядке структуры: при другой сортировке и фильтре он сбивает
const indent = computed(() => sortKey.value === 'path' && sortDir.value === 'asc' && !props.needle.trim() && !kindFilter.value)

function sortBy(column) {
  if (sortKey.value === column.key) sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  else { sortKey.value = column.key; sortDir.value = column.num ? 'desc' : 'asc' }
}
function ariaSort(column) {
  if (sortKey.value !== column.key) return 'none'
  return sortDir.value === 'asc' ? 'ascending' : 'descending'
}

function exportCsv() {
  const now = new Date()
  const day = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
  saveBlob(new Blob([tableCsv(visible.value)], { type: 'text/csv;charset=utf-8' }), `puls-structure-${day}.csv`)
}
</script>

<template>
  <div class="org-table" data-testid="org-table">
    <div class="table-tools">
      <select v-model="kindFilter" class="select input-sm kind-filter" aria-label="Фильтр по типу подразделения">
        <option value="">Все типы</option>
        <option v-for="kind in kinds" :key="kind.value" :value="kind.value">{{ kind.label }}</option>
      </select>
      <span class="summary" aria-live="polite">{{ countLabel(visible.length, 'подразделение', 'подразделения', 'подразделений') }} ·
        {{ countLabel(staff, 'сотрудник', 'сотрудника', 'сотрудников') }}</span>
      <button type="button" class="btn btn-sm export" :disabled="!visible.length" data-testid="org-export-csv" @click="exportCsv">
        <AppIcon name="download" :size="13" /> Выгрузить CSV
      </button>
    </div>
    <div class="card scroll scroll-thin">
      <table class="table">
        <thead>
          <tr>
            <th v-for="col in TABLE_COLUMNS" :key="col.key" :class="{ num: col.num }" :aria-sort="ariaSort(col)" scope="col">
              <button type="button" class="sort" :title="col.hint" @click="sortBy(col)">
                {{ col.label }}
                <AppIcon :name="sortKey === col.key && sortDir === 'desc' ? 'chevronDown' : 'chevronUp'" :size="11" :class="{ idle: sortKey !== col.key }" />
              </button>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in visible" :key="row.id" :class="{ selected: row.id === selectedId }" @click="emit('select', row.id)">
            <td class="path-cell" :style="{ '--level': indent ? Math.min(row.level - 1, 8) : 0 }">
              <div class="path-box">
                <button type="button" class="row-name" :aria-current="row.id === selectedId ? 'true' : undefined" @click.stop="emit('select', row.id)">{{ row.name }}</button>
                <small v-if="row.parentPath" :title="row.parentPath">{{ row.parentPath }}</small>
              </div>
            </td>
            <td><span class="kind" :style="{ '--k': kindColor(row.kind) }">{{ row.kindLabel }}</span></td>
            <td class="head-cell">
              <button v-if="row.headId" type="button" class="link" @click.stop="emit('select-person', row.headId, row.id)">{{ row.headName }}</button>
              <span v-else class="muted">—</span>
              <span v-if="row.headCount > 1" class="muted small" :title="row.headNames"> +{{ row.headCount - 1 }}</span>
            </td>
            <td class="num">{{ row.employeeCount }}</td>
            <td class="num">{{ row.branchEmployeeCount }}</td>
            <td class="num">{{ row.childCount || '' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!visible.length" class="muted empty">Ничего не найдено</p>
    </div>
  </div>
</template>

<style scoped>
.org-table { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.table-tools { display: flex; gap: 8px 10px; align-items: center; flex-wrap: wrap; }
.kind-filter { width: auto; min-width: 150px; }
.summary { font-size: 12.5px; color: var(--color-text-muted); }
.export { margin-left: auto; }
.scroll { overflow: auto; max-height: calc(100vh - 230px); min-height: 240px; }
.table { font-size: 13px; }
.table th { padding: 0; }
.table th.num { text-align: right; }
.sort { display: inline-flex; align-items: center; gap: 4px; width: 100%; padding: 9px 8px; border: 0; background: none; font: inherit; font-weight: 600; color: var(--color-text-secondary); text-align: inherit; white-space: nowrap; }
.num .sort { justify-content: flex-end; }
.sort:hover { color: var(--color-text); }
.sort .idle { opacity: 0.25; }
.table td { padding: 7px 8px; vertical-align: top; }
.table tbody tr { cursor: pointer; }
.table tbody tr.selected td { background: var(--color-primary-soft); }
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.path-cell { padding-left: calc(8px + var(--level) * 12px) !important; }
/* Ширину задаёт блок, а не ячейка (её таблица не ограничивает): длинный путь не растягивает колонку,
   а отступ уровня берётся из той же ширины */
.path-box { min-width: 160px; max-width: calc(280px - var(--level) * 12px); }
.row-name { display: block; border: 0; background: none; padding: 0; font: inherit; font-weight: 600; color: var(--color-text); text-align: left; }
.row-name:hover { color: var(--color-primary-dark); text-decoration: underline; }
.path-cell small { display: block; color: var(--color-text-muted); font-size: 11.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.head-cell { min-width: 120px; }
.kind { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em; color: color-mix(in srgb, var(--k) 80%, black); background: color-mix(in srgb, var(--k) 12%, white); padding: 1px 6px; border-radius: 4px; white-space: nowrap; }
.link { border: 0; background: none; padding: 0; color: var(--color-primary-dark); font: inherit; text-align: left; }
.link:hover { text-decoration: underline; }
.empty { padding: 20px; text-align: center; margin: 0; }
@media (max-width: 900px) {
  .scroll { max-height: 70vh; }
}
@media (max-width: 768px) {
  .export { margin-left: 0; }
  .kind-filter { flex: 1 1 160px; }
  .row-name { min-height: 32px; }
}
</style>
