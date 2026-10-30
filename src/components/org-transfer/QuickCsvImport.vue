<script setup>
import { reactive, ref } from 'vue'
import { api } from '../../api/client'
import { useUiStore } from '../../stores/ui'
import { decodeCsv } from '../../utils/employeeImport'
import AppIcon from '../common/AppIcon.vue'
import ImportPlan from './ImportPlan.vue'

/**
 * Быстрый импорт — подразделения и сотрудники одной таблицей из кадровой системы; формат и правила —
 * как в «По Делу». Сначала проверка без записи, затем применение одной транзакцией.
 */
const ui = useUiStore()
const csv = reactive({ text: '', fileName: '', createMissing: false })
const plan = ref(null)
const busy = ref(false)
const error = ref('')
const applied = ref(false)

const SAMPLE = 'email;ФИО;Подразделение;Должность;Руководитель;Руководит\n'
  + 'ceo@company.ru;Иванов Иван;ООО «Компания»;Генеральный директор;;да\n'
  + 'sales@company.ru;Петрова Анна;ООО «Компания» / Продажи;Начальник отдела;;да\n'
  + 'manager@company.ru;Сидоров Пётр;ООО «Компания» / Продажи;Менеджер;;\n'

function reset() {
  plan.value = null
  applied.value = false
  error.value = ''
}

async function readFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  reset()
  if (file.size > 5 * 1024 * 1024) { error.value = 'Файл больше 5 МБ'; return }
  csv.text = decodeCsv(new Uint8Array(await file.arrayBuffer()))
  csv.fileName = file.name
}

function sample() {
  csv.text = SAMPLE
  csv.fileName = ''
  reset()
}

async function run(apply) {
  if (busy.value || !csv.text.trim()) return
  busy.value = true
  error.value = ''
  applied.value = false
  try {
    const result = await api.post('/org/import/csv', { csv: csv.text, createMissing: csv.createMissing, apply })
    plan.value = result
    applied.value = Boolean(result.applied)
    if (applied.value) ui.success('Изменения применены', 'Структура обновлена')
  } catch (err) {
    // 409 — план с ошибками: показываем его, ничего не применено
    if (err?.payload?.summary) plan.value = err.payload
    error.value = err?.message || 'Не удалось выполнить импорт'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="card transfer-card" data-testid="import-csv">
    <p class="hint">
      Быстрая загрузка: подразделения и сотрудники одной таблицей. Для переноса всех полей, фото и разбора различий — вкладки «Структура» и «Сотрудники».
      Первая строка — заголовки, разделитель «;» или «,», кодировка UTF-8 или Windows из Excel. Обязательны <b>email</b> (или логин) и <b>подразделение</b> — путь от корня через «/».
      Необязательны: ФИО, должность, email руководителя, «руководит» («да» — сотрудник становится руководителем своего подразделения), табельный номер.
      Сотрудники сопоставляются по email или логину; руководитель из файла записывается явно, только если он отличается от руководителя по структуре.
    </p>
    <div class="file-row">
      <label class="btn btn-sm file-btn"><AppIcon name="upload" :size="13" /> Выбрать файл<input type="file" accept=".csv,.txt,text/csv,text/plain" data-testid="csv-file" @change="readFile" /></label>
      <span v-if="csv.fileName" class="muted small">{{ csv.fileName }}</span>
      <button type="button" class="btn btn-ghost btn-sm" data-testid="csv-sample" @click="sample">Вставить пример</button>
    </div>
    <textarea v-model="csv.text" class="textarea mono" rows="7" spellcheck="false" placeholder="email;ФИО;Подразделение;Должность;Руководитель;Руководит" aria-label="Содержимое CSV" data-testid="csv-text" @input="reset" />
    <label class="checkbox"><input v-model="csv.createMissing" type="checkbox" data-testid="csv-create" @change="reset" /> Создавать новых сотрудников — тех, кого нет в системе (без пароля, с ролью «Наблюдатель»)</label>
    <div class="actions">
      <button type="button" class="btn" :disabled="busy || !csv.text.trim()" data-testid="import-preview" @click="run(false)">Проверить</button>
      <button type="button" class="btn btn-primary" :disabled="busy || !plan || !plan.canApply || applied" data-testid="import-apply" @click="run(true)">Применить</button>
    </div>
  </section>

  <div v-if="error" class="status error" role="alert"><AppIcon name="warning" :size="14" /><span>{{ error }}</span></div>
  <div v-if="applied" class="status ok" role="status" data-testid="import-applied"><AppIcon name="check" :size="14" /><span>Изменения применены. Структура обновлена.</span></div>
  <ImportPlan v-if="plan" :plan="plan" />
</template>

<style scoped src="./transfer.css"></style>
