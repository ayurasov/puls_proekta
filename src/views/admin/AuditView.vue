<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { api, downloadFrom } from '../../api/client'
import { useUiStore } from '../../stores/ui'
import { countLabel, formatDateTime } from '../../utils/format'
import AppIcon from '../../components/common/AppIcon.vue'
import HelpLink from '../../components/common/HelpLink.vue'

const ui = useUiStore()
const filters = reactive({ user: '', section: '', project: '', action: '', from: '', to: '', q: '' })
const facets = ref({ users: [], sections: [], projects: [], actions: [], userLabels: {}, sectionLabels: {} })
const rows = ref([])
const total = ref(0)
const nextBefore = ref(null)
const loading = ref(true)
const expanded = ref(null)
const ACTIONS = { create: 'создание', update: 'изменение', delete: 'удаление', restore: 'восстановление', purge: 'удаление навсегда', archive: 'в архив', unarchive: 'из архива', snapshot: 'версия' }

async function load(more = false) {
  loading.value = true
  try {
    const res = await api.get('/audit', { ...filters, before: more ? nextBefore.value : null, limit: 100 })
    rows.value = more ? [...rows.value, ...res.rows] : res.rows
    total.value = res.total
    nextBefore.value = res.hasMore ? res.nextBefore : null
  } catch (err) { ui.error(err) } finally { loading.value = false }
}
onMounted(async () => {
  try { facets.value = await api.get('/audit/facets') } catch { /* фильтры без подсказок */ }
  load()
})
let timer = null
watch(filters, () => { clearTimeout(timer); timer = setTimeout(() => load(), 250) })

function reset() {
  Object.keys(filters).forEach((k) => { filters[k] = '' })
}
async function exportCsv() {
  try { await downloadFrom('/audit/export.csv', filters, 'audit.csv') } catch (err) { ui.error(err) }
}
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Реестр изменений</h1>
        <div class="page-subtitle">{{ countLabel(total, 'запись', 'записи', 'записей') }}. Изменения планов — с содержанием правки; подробная история — в «Истории» плана.</div>
      </div>
      <HelpLink topic="admin-audit" />
      <div class="spacer" />
      <button class="btn" type="button" @click="exportCsv"><AppIcon name="download" :size="14" /> Выгрузить CSV</button>
    </div>
    <div class="card filters">
      <input v-model="filters.q" class="input input-sm" type="search" placeholder="Поиск по тексту" />
      <select v-model="filters.user" class="select input-sm"><option value="">Все авторы</option><option v-for="u in facets.users" :key="u" :value="u">{{ facets.userLabels?.[u] || u }}</option></select>
      <select v-model="filters.section" class="select input-sm"><option value="">Все разделы</option><option v-for="s in facets.sections" :key="s" :value="s">{{ facets.sectionLabels?.[s] || s }}</option></select>
      <select v-model="filters.project" class="select input-sm"><option value="">Все проекты</option><option v-for="p in facets.projects" :key="p" :value="p">{{ p }}</option></select>
      <select v-model="filters.action" class="select input-sm"><option value="">Все действия</option><option v-for="a in facets.actions" :key="a" :value="a">{{ ACTIONS[a] || a }}</option></select>
      <label class="date"><span class="muted small">с</span><input v-model="filters.from" class="input input-sm" type="date" /></label>
      <label class="date"><span class="muted small">по</span><input v-model="filters.to" class="input input-sm" type="date" /></label>
      <button class="btn btn-ghost btn-sm" type="button" @click="reset">Сбросить</button>
    </div>
    <div class="card table-wrap">
      <table class="table audit-table">
        <colgroup>
          <col class="w-date" /><col class="w-user" /><col class="w-project" /><col class="w-section" /><col class="w-action" />
          <col class="w-entity" /><col class="w-field" /><col class="w-value" /><col class="w-value" />
        </colgroup>
        <thead><tr><th>Дата и время</th><th>Автор</th><th>Проект</th><th>Раздел</th><th>Действие</th><th>Объект</th><th>Поле</th><th>Было</th><th>Стало</th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id" :class="{ open: expanded === r.id }" @click="expanded = expanded === r.id ? null : r.id">
            <td class="small">{{ formatDateTime(r.createdAt) }}</td>
            <td class="small" :title="r.userLogin ? '@' + r.userLogin : ''">{{ r.userName }}</td>
            <td class="small"><div class="clip">{{ r.projectTitle || '—' }}</div></td>
            <td class="small">{{ r.sectionLabel || r.section }}</td>
            <td class="small">{{ r.actionLabel }}</td>
            <td class="small"><div class="clip">{{ r.entityLabel || r.entity }}</div></td>
            <td class="small"><div class="clip">{{ r.fieldLabel || r.field || '—' }}</div></td>
            <td class="small muted"><div class="clip">{{ (r.oldLabel ?? r.oldValue) || '—' }}</div></td>
            <td class="small"><div class="clip">{{ (r.newLabel ?? r.newValue) || '—' }}</div></td>
          </tr>
          <tr v-if="!loading && !rows.length"><td colspan="9" class="muted">Записей нет</td></tr>
        </tbody>
      </table>
      <div v-if="nextBefore" class="more"><button class="btn btn-sm" type="button" :disabled="loading" @click="load(true)">Показать ещё</button></div>
    </div>
  </div>
</template>

<style scoped>
.filters { display: flex; flex-wrap: wrap; gap: 8px; padding: 10px 12px; margin-bottom: 12px; align-items: center; }
.filters .input, .filters .select { width: auto; min-width: 150px; }
.date { display: flex; align-items: center; gap: 4px; }
/* Таблица по ширине страницы: длинные значения переносятся (свёрнутая строка — до трёх строк) */
.audit-table { table-layout: fixed; min-width: 900px; }
.audit-table th { white-space: normal; }
.audit-table td { vertical-align: top; overflow-wrap: anywhere; }
.w-date { width: 118px; } .w-user { width: 12%; } .w-project { width: 12%; } .w-section { width: 92px; }
.w-action { width: 96px; } .w-entity { width: 16%; } .w-field { width: 9%; } .w-value { width: 15%; }
.clip { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
tr.open .clip { display: block; -webkit-line-clamp: none; }
tr.open td { background: #fafbff; }
@media (max-width: 768px) {
  .filters .input, .filters .select { flex: 1 1 100%; }
  .date { flex: 1 1 40%; }
  .date .input { flex: 1; min-width: 0; }
  .audit-table { min-width: 1040px; }
}
tbody tr { cursor: pointer; }
.more { padding: 10px; text-align: center; }
</style>
