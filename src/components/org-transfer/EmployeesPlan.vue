<script setup>
import { computed, ref } from 'vue'
import {
  CHOICE_LABEL, ROLE_OPTIONS, customValueFor, decisionFor, needsAttention, visibleChoices, withBulkChoice,
  withFieldChoice, withIdentity,
} from '../../utils/employeeImport'

/**
 * План загрузки сотрудников podelu.employees — из файла или из «По Делу» (правила одни, ADR-0008, ADR-0009):
 * новые сотрудники, изменения, коллизии и ошибки. Решения администратора уходят наверх событием decide
 * (новые решения, пересчитать сразу или с задержкой); кнопки применения — в слоте.
 */
const props = defineProps({
  plan: { type: Object, required: true },
  decisions: { type: Object, required: true },
  busy: { type: Boolean, default: false },
  source: { type: String, default: 'file' }, // file | podelu
  lineLabel: { type: String, default: 'запись №' },
  photoUrl: { type: Function, default: () => '' },
})
const emit = defineEmits(['decide'])

const TEXT = {
  file: { incoming: 'В файле', choice: 'Файл', bulk: 'взять из файла', photo: 'Фото из файла' },
  podelu: { incoming: 'В «По Делу»', choice: '«По Делу»', bulk: 'взять из «По Делу»', photo: 'Фото из «По Делу»' },
}
const text = computed(() => TEXT[props.source] || TEXT.file)
const choiceLabel = computed(() => ({ ...CHOICE_LABEL, incoming: text.value.choice }))
const filter = ref('attention')

function choose(record, conflict, choice) {
  const value = choice === 'custom' ? customValueFor(props.decisions, record, conflict) : undefined
  emit('decide', withFieldChoice(props.decisions, record.key, conflict.field, choice, value), false)
}
function setCustom(record, conflict, value) {
  emit('decide', withFieldChoice(props.decisions, record.key, conflict.field, 'custom', value), false)
}
function setIdentity(record, identity) {
  emit('decide', withIdentity(props.decisions, record.key, identity), true)
}
function bulk(choice) {
  emit('decide', withBulkChoice(props.decisions, props.plan, choice), true)
}

const records = computed(() => {
  const list = props.plan.records || []
  if (filter.value === 'attention') return list.filter(needsAttention)
  if (filter.value === 'create') return list.filter((r) => r.action === 'create')
  return list
})
const chips = computed(() => {
  const s = props.plan.summary
  if (!s) return []
  return [
    { label: 'Записей', value: s.records },
    { label: 'Новых', value: s.create },
    { label: 'Обновятся', value: s.update },
    { label: 'Без изменений', value: s.unchanged },
    ...(s.skip ? [{ label: 'Пропущено', value: s.skip, info: true }] : []),
    { label: 'Коллизий', value: s.conflicts, info: s.conflicts > 0 },
    ...(s.unresolved ? [{ label: 'Нужно решение', value: s.unresolved, warn: true }] : []),
    { label: 'Ошибок', value: s.errors, warn: s.errors > 0 },
    ...(s.createDepartments ? [{ label: 'Новых подразделений', value: s.createDepartments }] : []),
    ...(s.avatars ? [{ label: 'Фото', value: s.avatars }] : []),
  ]
})
const ACTION = {
  create: { label: 'Новый', cls: 'badge-success' },
  update: { label: 'Обновится', cls: 'badge-primary' },
  unchanged: { label: 'Без изменений', cls: '' },
  skip: { label: 'Пропущен', cls: '' },
}
function badge(record) {
  if (record.unresolved) return { label: 'Нужно решение', cls: 'badge-warning' }
  if (record.errors.length) return { label: 'Ошибка', cls: 'badge-danger' }
  return ACTION[record.action] || ACTION.unchanged
}
function display(value) {
  if (Array.isArray(value)) return value.length ? value.join('; ') : '—'
  return value === '' || value == null ? '—' : value
}
</script>

<template>
  <section class="card plan-card" data-testid="employees-plan">
    <div class="chips">
      <span v-for="chip in chips" :key="chip.label" class="chip" :class="{ warn: chip.warn, info: chip.info }">{{ chip.label }}: <b>{{ chip.value }}</b></span>
      <span v-if="busy" class="muted small">Пересчёт…</span>
    </div>
    <ul v-if="plan.notes?.length" class="notes"><li v-for="note in plan.notes" :key="note">{{ note }}</li></ul>
    <div v-if="plan.departments?.length" class="plan-block">
      <h4 class="section-title">Новые подразделения</h4>
      <p>{{ plan.departments.map((d) => d.path).join('; ') }}</p>
    </div>

    <div class="plan-head">
      <select v-model="filter" class="select input-sm filter" aria-label="Какие записи показать" data-testid="employees-filter">
        <option value="attention">Требуют внимания</option>
        <option value="create">Новые сотрудники</option>
        <option value="all">Все записи</option>
      </select>
      <div v-if="plan.summary.conflicts" class="bulk">
        <span class="muted small">Во всех коллизиях:</span>
        <button type="button" class="btn btn-ghost btn-sm" data-testid="bulk-current" @click="bulk('current')">оставить значения системы</button>
        <button type="button" class="btn btn-ghost btn-sm" data-testid="bulk-incoming" @click="bulk('incoming')">{{ text.bulk }}</button>
      </div>
    </div>

    <div class="records">
      <article v-for="record in records" :key="record.key + record.line" class="record" :class="{ 'record-error': record.errors.length || record.unresolved }" :data-record="record.key">
        <header class="record-head">
          <div>
            <strong>{{ record.name || record.email || record.login }}</strong>
            <small>{{ record.email || 'без email' }}<template v-if="record.login"> · {{ record.login }}</template> · {{ lineLabel }} {{ record.line }}</small>
          </div>
          <span class="badge" :class="badge(record).cls">{{ badge(record).label }}</span>
        </header>

        <div v-if="record.identity" class="identity" data-testid="identity-conflict">
          <p>Сотрудника с таким email нет, но логин занят: <strong>{{ record.identity.name }}</strong> ({{ record.identity.email }}, логин {{ record.identity.login }}). Это тот же человек?</p>
          <div class="segmented">
            <button type="button" :class="{ 'is-active': decisions[record.key]?.identity === 'same' }" data-testid="identity-same" @click="setIdentity(record, 'same')">Да — обновить его</button>
            <button type="button" :class="{ 'is-active': decisions[record.key]?.identity === 'new' }" data-testid="identity-new" @click="setIdentity(record, 'new')">Нет — новый сотрудник</button>
          </div>
        </div>

        <table v-if="record.conflicts.length" class="conflicts">
          <thead><tr><th>Поле</th><th>В системе</th><th>{{ text.incoming }}</th><th>Решение</th></tr></thead>
          <tbody>
            <tr v-for="conflict in record.conflicts" :key="conflict.field" :data-field="conflict.field">
              <td class="field-name">{{ conflict.label }}<small v-if="conflict.note">{{ conflict.note }}</small></td>
              <td :class="{ chosen: decisionFor(decisions, record, conflict) === 'current' }">
                <img v-if="conflict.kind === 'avatar' && record.userId" class="photo" :src="`/api/users/${record.userId}/avatar`" alt="Фото в системе" />
                <template v-else>{{ display(conflict.current) }}</template>
              </td>
              <td :class="{ chosen: decisionFor(decisions, record, conflict) === 'incoming' }">
                <img v-if="conflict.kind === 'avatar' && photoUrl(record)" class="photo" :src="photoUrl(record)" :alt="text.photo" />
                <template v-else>{{ display(conflict.incoming) }}</template>
              </td>
              <td class="decision">
                <div class="segmented" role="radiogroup" :aria-label="`Решение: ${conflict.label}`">
                  <button v-for="choice in visibleChoices(conflict)" :key="choice" type="button" :class="{ 'is-active': decisionFor(decisions, record, conflict) === choice }" :data-choice="choice" @click="choose(record, conflict, choice)">{{ choiceLabel[choice] }}</button>
                </div>
                <small v-if="decisionFor(decisions, record, conflict) === 'merge'" class="muted">{{ display(conflict.merged) }}</small>
                <template v-if="decisionFor(decisions, record, conflict) === 'custom'">
                  <select v-if="conflict.kind === 'role'" class="select input-sm" :value="customValueFor(decisions, record, conflict)" @change="setCustom(record, conflict, $event.target.value)">
                    <option v-for="option in ROLE_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
                  </select>
                  <input v-else class="input input-sm" type="text" :value="customValueFor(decisions, record, conflict)" :aria-label="`Своё значение: ${conflict.label}`" data-testid="custom-value" @change="setCustom(record, conflict, $event.target.value)" />
                  <small v-if="conflict.kind === 'list'" class="muted">Пути через «|», основное — с пометкой (основной)</small>
                </template>
              </td>
            </tr>
          </tbody>
        </table>

        <ul v-if="record.changes.length" class="changes">
          <li v-for="change in record.changes" :key="change.field"><b>{{ change.label }}:</b> {{ display(change.new) }} <span class="muted">(в системе пусто)</span></li>
        </ul>
        <ul v-if="record.errors.length" class="errors"><li v-for="e in record.errors" :key="e">{{ e }}</li></ul>
        <ul v-if="record.warnings.length" class="warnings"><li v-for="w in record.warnings" :key="w">{{ w }}</li></ul>
      </article>
      <p v-if="!records.length" class="muted small">Нет записей по выбранному отбору</p>
    </div>

    <slot />
  </section>
</template>

<style scoped src="./transfer.css"></style>
<style scoped>
.bulk { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.records { display: flex; flex-direction: column; gap: 10px; }
.record { border: 1px solid var(--color-border); border-radius: var(--radius-md); padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
.record-error { border-color: #f3b8bb; background: #fffafa; }
.record-head { display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; }
.record-head small { display: block; color: var(--color-text-muted); font-size: 11.5px; }
.identity { padding: 8px 10px; border-radius: var(--radius-sm); background: var(--color-warning-soft); font-size: 13px; display: flex; flex-direction: column; gap: 6px; }
.identity p { margin: 0; }
.identity .segmented { align-self: flex-start; }
.conflicts { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.conflicts th { text-align: left; font-size: 11px; font-weight: 600; color: var(--color-text-muted); padding: 4px 6px; border-bottom: 1px solid var(--color-border); }
.conflicts td { padding: 6px; border-bottom: 1px solid #eef1f6; vertical-align: top; overflow-wrap: anywhere; }
.conflicts td.chosen { background: #f1f5ff; font-weight: 600; }
.field-name small { display: block; font-weight: 400; color: #a8670f; font-size: 11px; }
.decision { display: flex; flex-direction: column; gap: 4px; min-width: 220px; }
.decision .segmented { align-self: flex-start; flex-wrap: wrap; }
.photo { width: 40px; height: 40px; border-radius: 50%; object-fit: cover; border: 1px solid var(--color-border); }
@media (max-width: 720px) {
  .conflicts thead { display: none; }
  .conflicts tr { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; padding: 6px 0; border-bottom: 1px solid #eef1f6; }
  .conflicts td { border: 0; padding: 2px 4px; }
  .conflicts td.field-name, .conflicts td.decision { grid-column: 1 / -1; }
}
</style>
