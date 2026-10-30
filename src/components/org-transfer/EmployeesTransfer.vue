<script setup>
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { useOrgStore } from '../../stores/org'
import { api, downloadFrom } from '../../api/client'
import { useUiStore } from '../../stores/ui'
import { useDirectoryStore } from '../../stores/directory'
import AppIcon from '../common/AppIcon.vue'
import {
  CHOICE_LABEL, NEW_USER_MODES, ROLE_OPTIONS, EmployeeFileError, avatarBatches, customValueFor, decisionFor,
  needsAttention, readEmployeeFile, visibleChoices, withBulkChoice, withFieldChoice, withIdentity,
} from '../../utils/employeeImport'

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
const filter = ref('attention')
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

function choose(record, conflict, choice) {
  const value = choice === 'custom' ? customValueFor(decisions.value, record, conflict) : undefined
  decisions.value = withFieldChoice(decisions.value, record.key, conflict.field, choice, value)
  scheduleRefresh()
}
function setCustom(record, conflict, value) {
  decisions.value = withFieldChoice(decisions.value, record.key, conflict.field, 'custom', value)
  scheduleRefresh()
}
function setIdentity(record, identity) {
  decisions.value = withIdentity(decisions.value, record.key, identity)
  refresh()
}
function bulk(choice) {
  decisions.value = withBulkChoice(decisions.value, plan.value, choice)
  refresh()
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

const records = computed(() => {
  const list = plan.value?.records || []
  if (filter.value === 'attention') return list.filter(needsAttention)
  if (filter.value === 'create') return list.filter((r) => r.action === 'create')
  return list
})
const chips = computed(() => {
  const s = plan.value?.summary
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

  <section v-if="plan" class="card plan-card" data-testid="employees-plan">
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
        <button type="button" class="btn btn-ghost btn-sm" data-testid="bulk-incoming" @click="bulk('incoming')">взять из файла</button>
      </div>
    </div>

    <div class="records">
      <article v-for="record in records" :key="record.key + record.line" class="record" :class="{ 'record-error': record.errors.length || record.unresolved }" :data-record="record.key">
        <header class="record-head">
          <div>
            <strong>{{ record.name || record.email || record.login }}</strong>
            <small>{{ record.email || 'без email' }}<template v-if="record.login"> · {{ record.login }}</template> · {{ file?.format === 'csv' ? 'строка' : 'запись №' }} {{ record.line }}</small>
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
          <thead><tr><th>Поле</th><th>В системе</th><th>В файле</th><th>Решение</th></tr></thead>
          <tbody>
            <tr v-for="conflict in record.conflicts" :key="conflict.field" :data-field="conflict.field">
              <td class="field-name">{{ conflict.label }}<small v-if="conflict.note">{{ conflict.note }}</small></td>
              <td :class="{ chosen: decisionFor(decisions, record, conflict) === 'current' }">
                <img v-if="conflict.kind === 'avatar' && record.userId" class="photo" :src="`/api/users/${record.userId}/avatar`" alt="Фото в системе" />
                <template v-else>{{ display(conflict.current) }}</template>
              </td>
              <td :class="{ chosen: decisionFor(decisions, record, conflict) === 'incoming' }">
                <img v-if="conflict.kind === 'avatar' && photoUrl(record)" class="photo" :src="photoUrl(record)" alt="Фото из файла" />
                <template v-else>{{ display(conflict.incoming) }}</template>
              </td>
              <td class="decision">
                <div class="segmented" role="radiogroup" :aria-label="`Решение: ${conflict.label}`">
                  <button v-for="choice in visibleChoices(conflict)" :key="choice" type="button" :class="{ 'is-active': decisionFor(decisions, record, conflict) === choice }" :data-choice="choice" @click="choose(record, conflict, choice)">{{ CHOICE_LABEL[choice] }}</button>
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

    <div class="actions">
      <button type="button" class="btn btn-primary" :disabled="busy || !plan.canApply || Boolean(result)" data-testid="employees-apply" @click="apply">Применить</button>
      <span v-if="!plan.canApply" class="muted small">Разберите записи с пометкой «Нужно решение» и исправьте ошибки</span>
    </div>
  </section>
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
