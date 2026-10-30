<script setup>
import { computed, ref } from 'vue'

/** План быстрого импорта CSV и синхронизации с AD: сводка, новые подразделения, строки сотрудников. */
const props = defineProps({ plan: { type: Object, required: true } })

const ACTION = {
  create: { label: 'Новый', cls: 'badge-success' },
  update: { label: 'Изменения', cls: 'badge-primary' },
  unchanged: { label: 'Без изменений', cls: '' },
  error: { label: 'Ошибка', cls: 'badge-danger' },
}
const FIELD = {
  department: 'Подразделение', position: 'Должность', name: 'ФИО', manager: 'Руководитель', head: 'Руководит',
  isActive: 'Доступ', reportsToNone: 'Первое лицо',
}
const filter = ref('changed')
const rows = computed(() => {
  const users = props.plan.users || []
  if (filter.value === 'changed') return users.filter((u) => u.action !== 'unchanged')
  if (filter.value === 'errors') return users.filter((u) => u.action === 'error')
  return users
})
const chips = computed(() => {
  const s = props.plan.summary
  return [
    { label: 'Строк', value: s.rows },
    { label: 'Новых подразделений', value: s.createDepartments },
    { label: 'Новых сотрудников', value: s.createUsers },
    { label: 'С изменениями', value: s.updateUsers },
    { label: 'Без изменений', value: s.unchanged },
    ...(s.deactivateUsers ? [{ label: 'Отключить', value: s.deactivateUsers, warn: true }] : []),
    { label: 'Ошибок', value: s.errors, warn: s.errors > 0 },
  ]
})
</script>

<template>
  <section class="card plan-card" data-testid="import-plan">
    <div class="chips">
      <span v-for="chip in chips" :key="chip.label" class="chip" :class="{ warn: chip.warn }">{{ chip.label }}: <b>{{ chip.value }}</b></span>
    </div>
    <div v-if="plan.cycles?.length" class="status error" role="alert">Цикл подчинения: {{ plan.cycles.join(', ') }}</div>
    <div v-if="plan.departments.length" class="plan-block">
      <h4 class="section-title">Новые подразделения</h4>
      <ul class="list"><li v-for="d in plan.departments" :key="d.key">{{ d.path }}</li></ul>
    </div>
    <div v-if="plan.deactivate?.length" class="plan-block">
      <h4 class="section-title">Будут отключены</h4>
      <p>{{ plan.deactivate.map((u) => u.name).join(', ') }}</p>
    </div>
    <div class="plan-block">
      <div class="plan-head">
        <h4 class="section-title">Сотрудники</h4>
        <select v-model="filter" class="select input-sm filter" aria-label="Какие строки показать" data-testid="import-filter">
          <option value="changed">Только изменения и ошибки</option>
          <option value="errors">Только ошибки</option>
          <option value="all">Все строки</option>
        </select>
      </div>
      <div class="table-wrap">
        <table class="table plan-table">
          <thead><tr><th>Стр.</th><th>Сотрудник</th><th>Подразделение</th><th>Руководитель</th><th>Что изменится</th></tr></thead>
          <tbody>
            <tr v-for="u in rows" :key="u.line" :class="{ 'row-error': u.action === 'error' }">
              <td class="muted num">{{ u.line }}</td>
              <td><strong>{{ u.name || u.email || u.login }}</strong><small>{{ u.email || u.login }}</small><span class="badge" :class="ACTION[u.action].cls">{{ ACTION[u.action].label }}</span></td>
              <td>{{ u.department || '—' }}</td>
              <td>{{ u.manager || '—' }}<small v-if="u.manager">{{ u.managerExplicit ? 'явно' : 'по структуре' }}</small></td>
              <td>
                <ul v-if="u.errors.length" class="errors"><li v-for="e in u.errors" :key="e">{{ e }}</li></ul>
                <ul v-else-if="u.changes.length" class="changes"><li v-for="c in u.changes" :key="c.field"><b>{{ FIELD[c.field] || c.field }}:</b> {{ c.old || '—' }} → {{ c.new || '—' }}</li></ul>
                <span v-else-if="u.action === 'create'" class="muted">будет создан</span>
                <span v-else class="muted">—</span>
              </td>
            </tr>
            <tr v-if="!rows.length"><td colspan="5" class="muted">Нет строк по выбранному отбору</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>

<style scoped src="./transfer.css"></style>
<style scoped>
.plan-table td { vertical-align: top; }
.plan-table td small { display: block; color: var(--color-text-muted); font-size: 11px; }
.plan-table .badge { margin-top: 3px; }
.plan-table .num { width: 40px; }
.row-error td { background: #fffafa; }
@media (max-width: 720px) {
  .plan-table th:nth-child(3), .plan-table td:nth-child(3) { display: none; }
}
</style>
