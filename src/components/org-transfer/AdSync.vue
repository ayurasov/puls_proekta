<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../../api/client'
import { useUiStore } from '../../stores/ui'
import AppIcon from '../common/AppIcon.vue'
import ImportPlan from './ImportPlan.vue'

/**
 * Синхронизация структуры с Active Directory: подключение — то же, что для входа через домен
 * (Администрирование → Настройки), параметры синхронизации — здесь. Сначала проверка, затем
 * применение одной транзакцией.
 */
const ui = useUiStore()
const status = ref(null)
const loading = ref(true)
const options = reactive({ userFilter: '', pathSource: 'attributes', pathAttributes: '', detectHeads: true })
const dirty = ref(false)
const flags = reactive({ createMissing: false, deactivateMissing: false })
const plan = ref(null)
const busy = ref(false)
const error = ref('')
const applied = ref(false)

function fill(data) {
  status.value = data
  Object.assign(options, { ...data.options, pathAttributes: (data.options.pathAttributes || []).join(', ') })
  dirty.value = false
}

onMounted(async () => {
  try { fill(await api.get('/org/sync/ad')) } catch (err) { error.value = err.message } finally { loading.value = false }
})

async function saveOptions() {
  busy.value = true
  error.value = ''
  try {
    fill(await api.put('/org/sync/ad/settings', {
      ...options, pathAttributes: options.pathAttributes.split(',').map((item) => item.trim()).filter(Boolean),
    }))
    plan.value = null
    ui.success('Параметры синхронизации сохранены')
  } catch (err) { error.value = err.message } finally { busy.value = false }
}

async function run(apply) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  applied.value = false
  try {
    const result = await api.post('/org/sync/ad', { ...flags, apply })
    plan.value = result
    applied.value = Boolean(result.applied)
    if (applied.value) ui.success('Синхронизация выполнена', 'Структура обновлена')
  } catch (err) {
    if (err?.payload?.summary) plan.value = err.payload
    error.value = err?.message || 'Не удалось выполнить синхронизацию'
  } finally {
    busy.value = false
  }
}

const MODES = { local: 'только пароли системы', ldap: 'только домен', hybrid: 'сначала домен, затем пароль системы' }
const pathText = computed(() => (status.value?.config?.pathSource === 'ou'
  ? 'по организационным единицам (OU) в DN сотрудника'
  : `из атрибутов ${(status.value?.config?.pathAttributes || []).join(' / ')}`))
</script>

<template>
  <section class="card transfer-card" data-testid="import-ad">
    <p v-if="loading" class="muted">Загрузка…</p>
    <template v-else-if="status">
      <div class="transfer-block">
        <div class="grow">
          <h3>Подключение к каталогу</h3>
          <template v-if="status.configured">
            <p class="hint">Каталог <b class="mono">{{ status.config.url }}</b>, база поиска <code class="mono">{{ status.config.baseDn }}</code> — из раздела «Настройки → Вход через домен».
              Вход по учётной записи домена {{ status.login.enabled ? `включён (${MODES[status.login.mode] || status.login.mode})` : 'выключен — синхронизация работает и без него' }}.</p>
            <p class="hint">Подразделения — {{ pathText }}; руководитель — атрибут manager; сотрудники сопоставляются по objectGUID, затем по email и логину. Новые сотрудники из каталога входят с паролем домена.</p>
          </template>
          <div v-else class="status info" data-testid="ad-not-configured"><AppIcon name="warning" :size="14" /><span>Каталог не настроен: укажите адрес сервера, Base DN и сервисную учётную запись в разделе <router-link to="/admin/settings">«Настройки → Вход через домен»</router-link>.</span></div>
          <div v-if="!status.ldap3Available" class="status error"><AppIcon name="warning" :size="14" /><span>На сервере не установлен пакет ldap3 — синхронизация недоступна.</span></div>
        </div>
      </div>

      <div class="transfer-block">
        <form class="grow" @submit.prevent="saveOptions">
          <h3>Параметры синхронизации</h3>
          <label class="field"><span class="field-label">Фильтр сотрудников</span><input v-model="options.userFilter" class="input mono" data-testid="ad-filter" @input="dirty = true" /></label>
          <div class="form-grid">
            <label class="field">
              <span class="field-label">Путь подразделения</span>
              <select v-model="options.pathSource" class="select" @change="dirty = true">
                <option value="attributes">Из атрибутов сотрудника</option>
                <option value="ou">Из OU в DN сотрудника</option>
              </select>
            </label>
            <label class="field">
              <span class="field-label">Атрибуты пути — от корня, через запятую</span>
              <input v-model="options.pathAttributes" class="input mono" :disabled="options.pathSource === 'ou'" @input="dirty = true" />
            </label>
          </div>
          <label class="checkbox"><input v-model="options.detectHeads" type="checkbox" @change="dirty = true" /> Определять руководителей подразделений — по подчинённым из того же подразделения</label>
          <div class="actions"><button type="submit" class="btn btn-sm" :disabled="busy || !dirty" data-testid="ad-save">Сохранить параметры</button></div>
        </form>
      </div>

      <div v-if="status.configured" class="transfer-block">
        <div class="grow">
          <h3>Синхронизация</h3>
          <label class="checkbox"><input v-model="flags.createMissing" type="checkbox" @change="plan = null" /> Создавать сотрудников из каталога, которых нет в системе</label>
          <label class="checkbox"><input v-model="flags.deactivateMissing" type="checkbox" @change="plan = null" /> Отключать сотрудников из каталога, которых в нём больше нет (отключённые в каталоге отключаются всегда)</label>
          <div class="actions">
            <button type="button" class="btn" :disabled="busy || dirty || !status.ldap3Available" data-testid="ad-preview" @click="run(false)">Проверить</button>
            <button type="button" class="btn btn-primary" :disabled="busy || !plan || !plan.canApply || applied" data-testid="ad-apply" @click="run(true)">Применить</button>
            <span v-if="dirty" class="muted small">Сохраните параметры перед проверкой</span>
          </div>
        </div>
      </div>
    </template>
  </section>

  <div v-if="error" class="status error" role="alert"><AppIcon name="warning" :size="14" /><span>{{ error }}</span></div>
  <div v-if="applied" class="status ok" role="status"><AppIcon name="check" :size="14" /><span>Синхронизация выполнена. Структура обновлена.</span></div>
  <ImportPlan v-if="plan" :plan="plan" />
</template>

<style scoped src="./transfer.css"></style>
<style scoped>
.grow .field + .form-grid, .grow .form-grid + .checkbox { margin-top: 4px; }
code { background: var(--color-surface-2); padding: 0 4px; border-radius: 4px; }
</style>
