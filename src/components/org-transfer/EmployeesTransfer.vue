<script setup>
import { onBeforeUnmount, reactive, ref } from 'vue'
import { useOrgStore } from '../../stores/org'
import { api, downloadFrom } from '../../api/client'
import { useUiStore } from '../../stores/ui'
import { useDirectoryStore } from '../../stores/directory'
import AppIcon from '../common/AppIcon.vue'
import EmployeesPlan from './EmployeesPlan.vue'
import { NEW_USER_MODES, EmployeeFileError, avatarBatches, readEmployeeFile } from '../../utils/employeeImport'

/**
 * Сотрудники файлом podelu.employees (общий с «По Делу»). Файл читается в браузере; сервер строит
 * план: новые сотрудники, изменения, коллизии и ошибки. Администратор решает каждую коллизию —
 * значение системы, файла, объединение или своё — и применяет план одной транзакцией; фото
 * отправляются следом пачками.
 */
const ui = useUiStore()
const directory = useDirectoryStore()

// --- выгрузка
const exportOptions = reactive({ format: 'zip', avatars: true, inactive: true, withoutEmail: true })
const EXPORT_FORMATS = [
  { value: 'zip', label: 'ZIP', hint: 'Для переноса в другую систему; с фото или без' },
  { value: 'csv', label: 'CSV', hint: 'Для Excel и кадровой системы; без фото' },
  { value: 'json', label: 'JSON', hint: 'Для обработки скриптами; без фото' },
]
const exporting = ref(false)
async function download() {
  error.value = ''
  exporting.value = true
  try {
    const { format, avatars, inactive, withoutEmail } = exportOptions
    await downloadFrom('/users/export', {
      format, avatars: format === 'zip' && avatars ? 1 : 0, inactive: inactive ? 1 : 0, withoutEmail: withoutEmail ? 1 : 0,
    }, `puls-employees.${format}`)
  } catch (err) {
    error.value = err?.message || 'Не удалось выгрузить сотрудников'
  } finally {
    exporting.value = false
  }
}

// --- загрузка
const file = ref(null)          // { name, format, content, avatars, photos, count }
const newUsers = ref('inactive')
const decisions = ref({})
const plan = ref(null)
const busy = ref(false)
const error = ref('')
const result = ref(null)
const avatarProgress = ref(null)

async function readFile(event) {
  const picked = event.target.files?.[0]
  event.target.value = ''
  if (!picked) return
  error.value = ''
  plan.value = null
  result.value = null
  decisions.value = {}
  releasePhotos()
  busy.value = true
  try {
    file.value = { name: picked.name, ...(await readEmployeeFile(picked)) }
  } catch (err) {
    file.value = null
    error.value = err instanceof EmployeeFileError ? err.message : (err?.message || 'Не удалось прочитать файл')
  } finally {
    busy.value = false
  }
  if (file.value) await refresh()
}

function payload(apply) {
  return {
    format: file.value.format, content: file.value.content, options: { newUsers: newUsers.value },
    decisions: decisions.value, avatars: file.value.avatars, apply,
  }
}

let refreshTimer = null
let requestSeq = 0
async function refresh() {
  if (!file.value) return
  clearTimeout(refreshTimer)
  const seq = ++requestSeq
  busy.value = true
  error.value = ''
  try {
    const next = await api.post('/users/import', payload(false))
    if (seq === requestSeq) plan.value = next
  } catch (err) {
    if (seq === requestSeq) error.value = err?.message || 'Не удалось проверить файл'
  } finally {
    if (seq === requestSeq) busy.value = false
  }
}
// Решения меняют план (ошибки своих значений, сопоставление) — пересчёт с небольшой задержкой
function scheduleRefresh() {
  clearTimeout(refreshTimer)
  refreshTimer = setTimeout(refresh, 500)
}
onBeforeUnmount(() => { clearTimeout(refreshTimer); releasePhotos() })

// Решение по коллизии — пересчёт с задержкой; «тот же человек» и «во всех коллизиях» — сразу
function decide(next, immediate) {
  decisions.value = next
  if (immediate) refresh()
  else scheduleRefresh()
}
function changeMode() { if (file.value) refresh() }

async function apply() {
  if (!plan.value?.canApply || busy.value) return
  clearTimeout(refreshTimer)
  busy.value = true
  error.value = ''
  try {
    const applied = await api.post('/users/import', payload(true))
    plan.value = applied
    result.value = { create: applied.summary.create, update: applied.summary.update, photos: 0, photoErrors: [] }
    await uploadPhotos(applied)
    directory.loaded = false
    useOrgStore().loaded = false // списки подразделений на других страницах перечитаются
    ui.success('Сотрудники загружены', `Новых: ${result.value.create}, обновлено: ${result.value.update}`)
  } catch (err) {
    if (err?.payload?.summary) plan.value = err.payload
    error.value = err?.message || 'Не удалось загрузить сотрудников'
  } finally {
    busy.value = false
    avatarProgress.value = null
  }
}

async function uploadPhotos(applied) {
  const batches = avatarBatches(applied.avatarUploads, applied.ids, file.value.photos)
  const total = batches.reduce((sum, batch) => sum + batch.length, 0)
  let done = 0
  for (const batch of batches) {
    avatarProgress.value = { done, total }
    const form = new FormData()
    for (const item of batch) form.append(String(item.userId), item.blob, 'avatar')
    try {
      const response = await api.upload('/users/import/avatars', form, 'POST')
      result.value.photos += response.saved
      result.value.photoErrors.push(...Object.values(response.errors || {}))
    } catch (err) {
      result.value.photoErrors.push(err?.message || 'пачка фото не загружена')
    }
    done += batch.length
  }
}

// Адреса фото из архива создаются один раз на файл и освобождаются при смене файла
const photoUrls = new Map()
function releasePhotos() {
  for (const url of photoUrls.values()) URL.revokeObjectURL(url)
  photoUrls.clear()
}
function photoUrl(record) {
  const blob = file.value?.photos?.get(record.key)
  if (!blob) return ''
  if (!photoUrls.has(record.key)) photoUrls.set(record.key, URL.createObjectURL(blob))
  return photoUrls.get(record.key)
}
</script>

<template>
  <section class="card transfer-card" data-testid="employees-transfer">
    <div class="transfer-block">
      <div class="grow">
        <h3>Выгрузить сотрудников</h3>
        <p class="hint">Email, логин, ФИО, должность, подразделение, руководитель, руководимые подразделения, роль, активность, способ входа. Пароли не выгружаются. Формат общий с «По Делу».</p>
        <div class="format-row" role="radiogroup" aria-label="Формат выгрузки">
          <label v-for="f in EXPORT_FORMATS" :key="f.value" class="option" :class="{ active: exportOptions.format === f.value }">
            <input v-model="exportOptions.format" type="radio" :value="f.value" :data-testid="`export-format-${f.value}`" /><span><strong>{{ f.label }}</strong><small>{{ f.hint }}</small></span>
          </label>
        </div>
        <label v-if="exportOptions.format === 'zip'" class="checkbox"><input v-model="exportOptions.avatars" type="checkbox" data-testid="export-avatars" /> С фото сотрудников</label>
        <label class="checkbox"><input v-model="exportOptions.inactive" type="checkbox" /> Включая отключённых</label>
        <label class="checkbox"><input v-model="exportOptions.withoutEmail" type="checkbox" /> Включая сотрудников без email (их не загрузить в «По Делу»)</label>
      </div>
      <button type="button" class="btn btn-sm" :disabled="exporting" data-testid="employees-export" @click="download"><AppIcon name="download" :size="13" /> {{ exporting ? 'Готовлю файл…' : 'Скачать' }}</button>
    </div>

    <div class="transfer-block">
      <div class="grow">
        <h3>Загрузить сотрудников</h3>
        <p class="hint">CSV (столбцы как у выгрузки, обязателен email или логин; кодировка UTF-8 или Windows из Excel), JSON или ZIP-архив выгрузки с фото — «Пульса» или «По Делу». Сотрудники сопоставляются по email, а без email — по логину. Если данные в файле и в системе различаются, вы решаете, что оставить. Пустое в файле ничего не стирает.</p>
        <fieldset class="modes">
          <legend class="field-label">Новые сотрудники после загрузки</legend>
          <label v-for="mode in NEW_USER_MODES" :key="mode.value" class="option">
            <input v-model="newUsers" type="radio" :value="mode.value" :data-testid="`new-users-${mode.value}`" @change="changeMode" />
            <span><strong>{{ mode.label }}</strong><small>{{ mode.hint }}</small></span>
          </label>
        </fieldset>
        <p class="hint">Новые сотрудники создаются без пароля, с ролью «Наблюдатель», если в файле нет другой. Пароль задаётся в разделе «Сотрудники».</p>
        <div class="file-row">
          <label class="btn btn-sm file-btn"><AppIcon name="upload" :size="13" /> Выбрать файл<input type="file" accept=".csv,.txt,.json,.zip,text/csv,application/json,application/zip" data-testid="employees-file" @change="readFile" /></label>
          <span v-if="file" class="muted small">{{ file.name }}<template v-if="file.photos.size"> · фото: {{ file.photos.size }}</template></span>
          <button v-if="file" type="button" class="btn btn-sm btn-ghost" :disabled="busy" data-testid="employees-check" @click="refresh">Проверить</button>
        </div>
      </div>
    </div>
  </section>

  <div v-if="error" class="status error" role="alert"><AppIcon name="warning" :size="14" /><span>{{ error }}</span></div>
  <div v-if="result" class="status ok" role="status" data-testid="employees-applied">
    <AppIcon name="check" :size="14" />
    <span>Загружено: новых {{ result.create }}, обновлено {{ result.update }}<template v-if="result.photos">, фото {{ result.photos }}</template>.
      <template v-if="result.photoErrors.length"> Фото не загружены: {{ result.photoErrors.length }} ({{ result.photoErrors.slice(0, 3).join('; ') }})</template></span>
  </div>
  <div v-if="avatarProgress" class="status info" role="status"><AppIcon name="upload" :size="14" /><span>Загружаются фото: {{ avatarProgress.done }} из {{ avatarProgress.total }}…</span></div>

  <EmployeesPlan v-if="plan" :plan="plan" :decisions="decisions" :busy="busy" :line-label="file?.format === 'csv' ? 'строка' : 'запись №'" :photo-url="photoUrl" @decide="decide">
    <div class="actions">
      <button type="button" class="btn btn-primary" :disabled="busy || !plan.canApply || Boolean(result)" data-testid="employees-apply" @click="apply">Применить</button>
      <span v-if="!plan.canApply" class="muted small">Разберите записи с пометкой «Нужно решение» и исправьте ошибки</span>
    </div>
  </EmployeesPlan>
</template>

<style scoped src="./transfer.css"></style>
<style scoped>
.format-row { display: flex; gap: 8px; flex-wrap: wrap; }
.option { display: flex; gap: 8px; align-items: flex-start; padding: 8px 10px; border: 1px solid var(--color-border); border-radius: var(--radius-sm); cursor: pointer; font-size: 13px; }
.format-row .option { flex: 1 1 180px; }
.option.active { border-color: var(--color-primary); background: var(--color-primary-soft); }
.option span { display: flex; flex-direction: column; gap: 2px; }
.option small { color: var(--color-text-muted); font-size: 12px; }
.option input { margin-top: 3px; accent-color: var(--color-primary); }
.modes { border: 0; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 6px; }
.modes legend { margin-bottom: 4px; }
</style>
