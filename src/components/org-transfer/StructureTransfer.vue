<script setup>
import { ref } from 'vue'
import { useOrgStore } from '../../stores/org'
import { api, downloadFrom } from '../../api/client'
import { useUiStore } from '../../stores/ui'
import AppIcon from '../common/AppIcon.vue'
import StructurePlan from './StructurePlan.vue'

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
    if (applied.value) useOrgStore().loaded = false // структура на других страницах перечитается
    if (applied.value) ui.success('Структура загружена')
  } catch (err) {
    if (err?.payload?.summary) plan.value = err.payload
    error.value = err?.message || 'Не удалось загрузить структуру'
  } finally {
    busy.value = false
  }
}
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

  <StructurePlan v-if="plan" :plan="plan" :busy="busy">
    <div class="actions">
      <button type="button" class="btn btn-primary" :disabled="busy || !plan.canApply || applied" data-testid="structure-apply" @click="run(true)">Применить</button>
      <span v-if="!plan.canApply" class="muted small">Исправьте ошибки в файле и выберите его снова</span>
    </div>
  </StructurePlan>
</template>

<style scoped src="./transfer.css"></style>
