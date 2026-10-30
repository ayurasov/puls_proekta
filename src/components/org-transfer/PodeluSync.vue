<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api } from '../../api/client'
import { useOrgStore } from '../../stores/org'
import { useDirectoryStore } from '../../stores/directory'
import { useUiStore } from '../../stores/ui'
import { NEW_USER_MODES } from '../../utils/employeeImport'
import AppIcon from '../common/AppIcon.vue'
import StructurePlan from './StructurePlan.vue'
import EmployeesPlan from './EmployeesPlan.vue'

/**
 * Структура и сотрудники из «По Делу» (ADR-0009) — по подключению из «Интеграций», тем же конвейером, что
 * файлы podelu.*: сервер получает выгрузки «По Делу» и показывает план (структура, затем сотрудники по ней,
 * с коллизиями); решения пересчитывают план по полученному снимку без обращения к «По Делу»; «Применить»
 * получает данные заново, сверяет с проверенными и применяет всё одной транзакцией.
 */
const ui = useUiStore()
const directory = useDirectoryStore()
const status = ref(null)
const loading = ref(true)
const options = reactive({ applySettings: true, createMissing: true, newUsers: 'inactive', photos: true })
const result = ref(null)      // план: { source, fingerprint, structure, employees, canApply }
const snapshot = ref(null)    // данные «По Делу» для пересчёта плана
const decisions = ref({})
const busy = ref(false)
const error = ref('')
const applied = ref(null)
let timer = null
let seq = 0

onMounted(async () => {
  try { status.value = await api.get('/org/sync/podelu') } catch (err) { error.value = err.message } finally { loading.value = false }
})

function body(extra) {
  return { ...options, decisions: decisions.value, ...extra }
}
function show(data) {
  result.value = data
  if (data.snapshot) snapshot.value = data.snapshot
}

async function fetchPlan() {
  if (busy.value) return
  clearTimeout(timer)
  busy.value = true
  error.value = ''
  applied.value = null
  try {
    show(await api.post('/org/sync/podelu', body()))
  } catch (err) {
    error.value = err?.message || 'Не удалось получить данные из «По Делу»'
  } finally {
    busy.value = false
  }
}

async function replan() {
  if (!snapshot.value) return
  clearTimeout(timer)
  const mine = ++seq
  busy.value = true
  error.value = ''
  try {
    const next = await api.post('/org/sync/podelu', body({ snapshot: snapshot.value }))
    if (mine === seq) show(next)
  } catch (err) {
    if (mine === seq) error.value = err?.message || 'Не удалось пересчитать план'
  } finally {
    if (mine === seq) busy.value = false
  }
}
function decide(next, immediate) {
  decisions.value = next
  clearTimeout(timer)
  if (immediate) replan()
  else timer = setTimeout(replan, 500)
}
function changeOptions() { if (snapshot.value && !applied.value) replan() }
function changePhotos() {
  // Фото входят в данные «По Делу» — их нужно получить заново
  result.value = null
  snapshot.value = null
}
onBeforeUnmount(() => clearTimeout(timer))

async function apply() {
  if (!result.value?.canApply || busy.value) return
  clearTimeout(timer)
  busy.value = true
  error.value = ''
  try {
    const done = await api.post('/org/sync/podelu', body({ apply: true, fingerprint: result.value.fingerprint }))
    result.value = done
    applied.value = done
    directory.loaded = false
    useOrgStore().loaded = false // структура и сотрудники на других страницах перечитаются
    ui.success('Загружено из «По Делу»', `Подразделений новых: ${done.structure.summary.create}, сотрудников новых: ${done.employees.summary.create}`)
  } catch (err) {
    // «По Делу» изменилась после проверки или в плане ошибки — показывается новый план
    if (err?.payload?.structure && err?.payload?.employees) show(err.payload)
    error.value = err?.message || 'Не удалось загрузить данные из «По Делу»'
  } finally {
    busy.value = false
  }
}

const fetchedAt = computed(() => {
  const value = result.value?.source?.fetchedAt
  return value ? new Date(value).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }) : ''
})
</script>

<template>
  <section class="card transfer-card" data-testid="podelu-sync">
    <p v-if="loading" class="muted">Загрузка…</p>
    <template v-else-if="status">
      <div class="transfer-block">
        <div class="grow">
          <h3>Подключение</h3>
          <p v-if="status.configured" class="hint">«По Делу» <b class="mono">{{ status.baseUrl }}</b>, учётная запись <b>{{ status.login }}</b> — из раздела
            <router-link to="/admin/integrations">«Интеграции»</router-link>.</p>
          <div v-else class="status info" data-testid="podelu-not-configured"><AppIcon name="warning" :size="14" /><span>«По Делу» не подключена: укажите адрес, логин и пароль сервисной учётной записи в разделе <router-link to="/admin/integrations">«Администрирование → Интеграции»</router-link>.</span></div>
          <p class="hint">Структура (подразделения с типами, порядком и руководителями) и сотрудники загружаются по тем же правилам, что файлы на вкладках «Структура» и «Сотрудники»:
            подразделения сопоставляются по пути от корня, сотрудники — по идентификатору «По Делу», email и логину. Ничего не удаляется, пустое в «По Делу» ничего не стирает,
            права администратора из «По Делу» не выдаются. Подразделения и сотрудники запоминают свои идентификаторы в «По Делу» — для ссылок и следующих загрузок.</p>
        </div>
      </div>

      <div v-if="status.configured" class="transfer-block">
        <div class="grow">
          <h3>Что загружать</h3>
          <label class="checkbox"><input v-model="options.applySettings" type="checkbox" data-testid="podelu-settings" @change="changeOptions" /> Перенести и настройки структуры (типы подразделений и глубину вложенности)</label>
          <label class="checkbox"><input v-model="options.photos" type="checkbox" data-testid="podelu-photos" @change="changePhotos" /> С фото сотрудников</label>
          <label class="checkbox"><input v-model="options.createMissing" type="checkbox" data-testid="podelu-create" @change="changeOptions" /> Создавать сотрудников, которых нет в «Пульсе»</label>
          <fieldset v-if="options.createMissing" class="modes">
            <legend class="field-label">Новые сотрудники после загрузки</legend>
            <label v-for="mode in NEW_USER_MODES" :key="mode.value" class="option">
              <input v-model="options.newUsers" type="radio" :value="mode.value" :data-testid="`podelu-new-users-${mode.value}`" @change="changeOptions" />
              <span><strong>{{ mode.label }}</strong><small>{{ mode.value === 'file' ? 'По активности в «По Делу»' : mode.hint }}</small></span>
            </label>
          </fieldset>
          <div class="actions">
            <button type="button" class="btn" :disabled="busy" data-testid="podelu-preview" @click="fetchPlan"><AppIcon name="refresh" :size="13" /> {{ result ? 'Получить заново' : 'Получить план из «По Делу»' }}</button>
            <span v-if="busy && !result" class="muted small">Вход в «По Делу» и выгрузка…</span>
          </div>
        </div>
      </div>
    </template>
  </section>

  <div v-if="error" class="status error" role="alert" data-testid="podelu-error"><AppIcon name="warning" :size="14" /><span>{{ error }}</span></div>
  <div v-if="applied" class="status ok" role="status" data-testid="podelu-applied">
    <AppIcon name="check" :size="14" />
    <span>Загружено из «По Делу»: подразделений новых {{ applied.structure.summary.create }}, изменено {{ applied.structure.summary.update }};
      сотрудников новых {{ applied.employees.summary.create }}, обновлено {{ applied.employees.summary.update }}<template v-if="applied.photos?.saved">, фото {{ applied.photos.saved }}</template>.
      <template v-if="applied.photos?.errors?.length"> Фото не загружены: {{ applied.photos.errors.join('; ') }}.</template></span>
  </div>

  <template v-if="result">
    <p class="source muted small" data-testid="podelu-source">Данные «По Делу» {{ fetchedAt }} · {{ result.source.account }}<template v-if="result.source.app"> · «По Делу» {{ result.source.app }}</template>
      · подразделений {{ result.source.departments }}, сотрудников {{ result.source.employees }}<template v-if="result.source.photos">, фото {{ result.source.photos }}</template></p>
    <h3 class="step">1. Структура</h3>
    <StructurePlan :plan="result.structure" :busy="busy" source="podelu" />
    <h3 class="step">2. Сотрудники</h3>
    <EmployeesPlan :plan="result.employees" :decisions="decisions" :busy="busy" source="podelu" @decide="decide" />
    <section class="card plan-card">
      <div class="actions">
        <button type="button" class="btn btn-primary" :disabled="busy || !result.canApply || Boolean(applied)" data-testid="podelu-apply" @click="apply">Применить</button>
        <span v-if="!result.canApply" class="muted small">Разберите записи с пометкой «Нужно решение» и исправьте ошибки</span>
        <span v-else-if="!applied" class="muted small">Структура и сотрудники загрузятся одной транзакцией; перед этим данные будут получены из «По Делу» заново и сверены с этим планом</span>
      </div>
    </section>
  </template>
</template>

<style scoped src="./transfer.css"></style>
<style scoped>
.modes { border: 0; padding: 0; margin: 0 0 0 24px; display: flex; flex-direction: column; gap: 6px; }
.modes legend { margin-bottom: 4px; }
.option { display: flex; gap: 8px; align-items: flex-start; padding: 6px 10px; border: 1px solid var(--color-border); border-radius: var(--radius-sm); cursor: pointer; font-size: 13px; }
.option span { display: flex; flex-direction: column; gap: 2px; }
.option small { color: var(--color-text-muted); font-size: 12px; }
.option input { margin-top: 3px; accent-color: var(--color-primary); }
.source { margin: 0 0 8px; }
.step { margin: 4px 0 8px; font-size: 14px; }
</style>
