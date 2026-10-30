<script setup>
import { computed, ref } from 'vue'
import { api, downloadFrom } from '../../api/client'
import { useUiStore } from '../../stores/ui'
import AppIcon from '../common/AppIcon.vue'

/**
 * Файл структуры podelu.org-structure — общий с «По Делу»: дерево подразделений с типами, порядком
 * и руководителями (по email), типы подразделений и глубина вложенности. Загрузка — сначала план
 * без записи, затем применение одной транзакцией; подразделения сопоставляются по пути от корня.
 */
const ui = useUiStore()
const bundle = ref(null)
const fileName = ref('')
const applySettings = ref(true)
const plan = ref(null)
const busy = ref(false)
const error = ref('')
const applied = ref(false)
const showUnchanged = ref(false)

async function download() {
  error.value = ''
  busy.value = true
  try { await downloadFrom('/org/export', null, 'puls-structure.json') } catch (err) { error.value = err.message || 'Не удалось выгрузить структуру' } finally { busy.value = false }
}

async function readFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  error.value = ''
  plan.value = null
  applied.value = false
  try {
    bundle.value = JSON.parse(await file.text())
    fileName.value = file.name
  } catch (err) {
    bundle.value = null
    fileName.value = ''
    error.value = err instanceof SyntaxError ? 'Файл не является JSON — выберите выгрузку структуры' : (err?.message || 'Не удалось прочитать файл')
    return
  }
  await run(false)
}

async function run(apply) {
  if (!bundle.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await api.post('/org/import/structure', { bundle: bundle.value, applySettings: applySettings.value, apply })
    plan.value = result
    applied.value = Boolean(result.applied)
    if (applied.value) ui.success('Структура загружена')
  } catch (err) {
    if (err?.payload?.summary) plan.value = err.payload
    error.value = err?.message || 'Не удалось загрузить структуру'
  } finally {
    busy.value = false
  }
}

const chips = computed(() => {
  const s = plan.value?.summary
  if (!s) return []
  return [
    { label: 'Подразделений в файле', value: s.departments },
    { label: 'Новых', value: s.create },
    { label: 'С изменениями', value: s.update },
    { label: 'Без изменений', value: s.unchanged },
    { label: 'Изменений в настройках', value: s.settings },
    { label: 'Назначить руководителями', value: s.addHeads },
    ...(s.pendingHeads ? [{ label: 'Руководителей нет в системе', value: s.pendingHeads, info: true }] : []),
    ...(s.missingInFile ? [{ label: 'Нет в файле (останутся)', value: s.missingInFile, info: true }] : []),
    { label: 'Ошибок', value: s.errors, warn: s.errors > 0 },
  ]
})
const departments = computed(() => (plan.value?.departments || []).filter((d) => showUnchanged.value || d.action !== 'unchanged'))
const pendingHeads = computed(() => (plan.value?.heads || []).filter((h) => h.action === 'pending'))
const addedHeads = computed(() => (plan.value?.heads || []).filter((h) => h.action === 'add'))
const FIELD = { kind: 'тип', sortOrder: 'порядок' }
const ACTION = { create: ['новое', 'badge-success'], update: ['изменится', 'badge-primary'], unchanged: ['без изменений', ''] }
</script>

<template>
  <section class="card transfer-card" data-testid="structure-transfer">
    <div class="transfer-block">
      <div class="grow">
        <h3>Выгрузить структуру</h3>
        <p class="hint">Файл JSON: подразделения с типами, порядком и руководителями (по email), типы подразделений и глубина вложенности. Тот же формат загружает «По Делу». Сотрудники выгружаются отдельно — на вкладке «Сотрудники».</p>
      </div>
      <button type="button" class="btn btn-sm" :disabled="busy" data-testid="structure-export" @click="download"><AppIcon name="download" :size="13" /> Скачать файл структуры</button>
    </div>
    <div class="transfer-block">
      <div class="grow">
        <h3>Загрузить структуру</h3>
        <p class="hint">Подходит файл «Пульса» и «По Делу». Подразделения сопоставляются по пути от корня: новые создаются, у существующих меняются тип и порядок. Подразделения, которых нет в файле, не удаляются, руководители только добавляются. Сначала — проверка без записи, затем применение одной транзакцией.</p>
        <label class="checkbox"><input v-model="applySettings" type="checkbox" data-testid="structure-settings" @change="plan && run(false)" /> Перенести и настройки структуры (типы подразделений и глубину вложенности)</label>
        <div class="file-row">
          <label class="btn btn-sm file-btn"><AppIcon name="upload" :size="13" /> Выбрать файл<input type="file" accept=".json,application/json" data-testid="structure-file" @change="readFile" /></label>
          <span v-if="fileName" class="muted small">{{ fileName }}</span>
          <button v-if="bundle" type="button" class="btn btn-sm btn-ghost" :disabled="busy" data-testid="structure-check" @click="run(false)">Проверить</button>
        </div>
      </div>
    </div>
  </section>

  <div v-if="error" class="status error" role="alert"><AppIcon name="warning" :size="14" /><span>{{ error }}</span></div>
  <div v-if="applied" class="status ok" role="status" data-testid="structure-applied"><AppIcon name="check" :size="14" /><span>Структура загружена.</span></div>

  <section v-if="plan" class="card plan-card" data-testid="structure-plan">
    <div class="chips">
      <span v-for="chip in chips" :key="chip.label" class="chip" :class="{ warn: chip.warn, info: chip.info }">{{ chip.label }}: <b>{{ chip.value }}</b></span>
      <span v-if="busy" class="muted small">Проверка…</span>
    </div>
    <ul v-if="plan.errors?.length" class="errors" data-testid="structure-errors"><li v-for="e in plan.errors" :key="e">{{ e }}</li></ul>
    <ul v-if="plan.notes?.length" class="notes"><li v-for="note in plan.notes" :key="note">{{ note }}</li></ul>

    <div v-if="plan.settings?.length" class="plan-block">
      <h4 class="section-title">Настройки структуры</h4>
      <table class="table">
        <thead><tr><th>Настройка</th><th>Сейчас</th><th>Из файла</th></tr></thead>
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
          <span class="badge badge-action" :class="ACTION[d.action][1]">{{ ACTION[d.action][0] }}</span>
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
      <p class="hint">Назначатся при загрузке сотрудников (вкладка «Сотрудники», столбец «Руководит»): {{ pendingHeads.map((h) => `${h.email} — ${h.path}`).join('; ') }}</p>
    </div>
    <div v-if="plan.missingInFile?.length" class="plan-block">
      <h4 class="section-title">Есть в системе, нет в файле — останутся</h4>
      <p class="hint">{{ plan.missingInFile.join('; ') }}</p>
    </div>

    <div class="actions">
      <button type="button" class="btn btn-primary" :disabled="busy || !plan.canApply || applied" data-testid="structure-apply" @click="run(true)">Применить</button>
      <span v-if="!plan.canApply" class="muted small">Исправьте ошибки в файле и выберите его снова</span>
    </div>
  </section>
</template>

<style scoped src="./transfer.css"></style>
