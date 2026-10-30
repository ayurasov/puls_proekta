<script setup>
import { computed, ref } from 'vue'

/**
 * План загрузки структуры podelu.org-structure — из файла или из «По Делу» (правила одни, ADR-0008, ADR-0009):
 * сводка, настройки, подразделения, руководители. Кнопки применения — в слоте.
 */
const props = defineProps({
  plan: { type: Object, required: true },
  busy: { type: Boolean, default: false },
  source: { type: String, default: 'file' }, // file | podelu
})

const TEXT = {
  file: {
    total: 'Подразделений в файле', incoming: 'Из файла', missing: 'Нет в файле (останутся)',
    missingTitle: 'Есть в системе, нет в файле — останутся',
    pending: 'Назначатся при загрузке сотрудников (вкладка «Сотрудники», столбец «Руководит»)',
  },
  podelu: {
    total: 'Подразделений в «По Делу»', incoming: 'Из «По Делу»', missing: 'Нет в «По Делу» (останутся)',
    missingTitle: 'Есть в системе, нет в «По Делу» — останутся',
    pending: 'Назначатся вместе с сотрудниками из «По Делу» (ниже, «Руководит»)',
  },
}
const text = computed(() => TEXT[props.source] || TEXT.file)
const showUnchanged = ref(false)

const chips = computed(() => {
  const s = props.plan.summary
  if (!s) return []
  return [
    { label: text.value.total, value: s.departments },
    { label: 'Новых', value: s.create },
    { label: 'С изменениями', value: s.update },
    { label: 'Без изменений', value: s.unchanged },
    { label: 'Изменений в настройках', value: s.settings },
    { label: 'Назначить руководителями', value: s.addHeads },
    ...(s.pendingHeads ? [{ label: 'Руководителей нет в системе', value: s.pendingHeads, info: true }] : []),
    ...(s.missingInFile ? [{ label: text.value.missing, value: s.missingInFile, info: true }] : []),
    ...(s.removedInPodelu ? [{ label: 'Удалены в «По Делу» (останутся)', value: s.removedInPodelu, info: true }] : []),
    { label: 'Ошибок', value: s.errors, warn: s.errors > 0 },
  ]
})
const departments = computed(() => (props.plan.departments || []).filter((d) => showUnchanged.value || d.action !== 'unchanged'))
const pendingHeads = computed(() => (props.plan.heads || []).filter((h) => h.action === 'pending'))
const addedHeads = computed(() => (props.plan.heads || []).filter((h) => h.action === 'add'))
const FIELD = { kind: 'тип', sortOrder: 'порядок', name: 'переименовано', parent: 'перенесено' }
const ACTION = { create: ['новое', 'badge-success'], update: ['изменится', 'badge-primary'], unchanged: ['без изменений', ''] }
// Подразделение, связанное с «По Делу», переименовано или перенесено там — меняется на месте (ADR-0009)
function badge(d) {
  const fields = new Set((d.changes || []).map((c) => c.field))
  if (fields.has('parent')) return ['перенесено', 'badge-primary']
  if (fields.has('name')) return ['переименовано', 'badge-primary']
  return ACTION[d.action]
}
</script>

<template>
  <section class="card plan-card" data-testid="structure-plan">
    <div class="chips">
      <span v-for="chip in chips" :key="chip.label" class="chip" :class="{ warn: chip.warn, info: chip.info }">{{ chip.label }}: <b>{{ chip.value }}</b></span>
      <span v-if="busy" class="muted small">Проверка…</span>
    </div>
    <ul v-if="plan.errors?.length" class="errors" data-testid="structure-errors"><li v-for="e in plan.errors" :key="e">{{ e }}</li></ul>
    <ul v-if="plan.notes?.length" class="notes"><li v-for="note in plan.notes" :key="note">{{ note }}</li></ul>

    <div v-if="plan.settings?.length" class="plan-block">
      <h4 class="section-title">Настройки структуры</h4>
      <table class="table">
        <thead><tr><th>Настройка</th><th>Сейчас</th><th>{{ text.incoming }}</th></tr></thead>
        <tbody><tr v-for="item in plan.settings" :key="item.key"><td>{{ item.label }}</td><td class="muted">{{ item.current }}</td><td>{{ item.incoming }}</td></tr></tbody>
      </table>
    </div>

    <div class="plan-block">
      <div class="plan-head">
        <h4 class="section-title">Подразделения</h4>
        <label class="checkbox small"><input v-model="showUnchanged" type="checkbox" /> Показать без изменений</label>
      </div>
      <ul class="list" data-testid="structure-departments">
        <li v-for="d in departments" :key="d.key">
          <span class="badge badge-action" :class="badge(d)[1]">{{ badge(d)[0] }}</span>
          {{ d.path }}
          <span v-for="c in d.changes" :key="c.field" class="muted small"> · {{ FIELD[c.field] || c.field }}: {{ c.old }} → {{ c.new }}</span>
        </li>
        <li v-if="!departments.length" class="muted">Изменений в подразделениях нет</li>
      </ul>
    </div>

    <div v-if="addedHeads.length" class="plan-block">
      <h4 class="section-title">Будут назначены руководителями</h4>
      <p>{{ addedHeads.map((h) => `${h.name} — ${h.path}`).join('; ') }}</p>
    </div>
    <div v-if="pendingHeads.length" class="plan-block">
      <h4 class="section-title">Руководители, которых нет в системе</h4>
      <p class="hint">{{ text.pending }}: {{ pendingHeads.map((h) => `${h.email} — ${h.path}`).join('; ') }}</p>
    </div>
    <div v-if="plan.missingInFile?.length" class="plan-block">
      <h4 class="section-title">{{ text.missingTitle }}</h4>
      <p class="hint">{{ plan.missingInFile.join('; ') }}</p>
    </div>
    <div v-if="plan.removedInPodelu?.length" class="plan-block" data-testid="structure-removed">
      <h4 class="section-title">Удалены в «По Делу» — в «Пульсе» останутся</h4>
      <p class="hint">{{ plan.removedInPodelu.join('; ') }}. Сотрудников и правила доступа перенесите вручную, затем подразделение можно удалить.</p>
    </div>

    <slot />
  </section>
</template>

<style scoped src="./transfer.css"></style>
