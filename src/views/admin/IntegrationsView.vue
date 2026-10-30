<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../../api/client'
import { useUiStore } from '../../stores/ui'
import { useDialogs } from '../../composables/useDialogs'
import AppIcon from '../../components/common/AppIcon.vue'

const ui = useUiStore()
const { confirm } = useDialogs()

// Как в burndown_tool: подписи полей зависят от способа входа
const TYPES = {
  server_basic: {
    label: 'Jira Server/DC — логин и пароль (Basic)', login: 'Логин', token: 'Пароль',
    loginPh: 'svc-puls', tokenPh: 'Пароль от Jira', hint: 'Basic передаёт пароль в каждом запросе — используйте только адрес https://.',
  },
  server: {
    label: 'Jira Server/DC — Personal Access Token (8.14 и новее)', login: 'Логин (необязательно)', token: 'Personal Access Token',
    loginPh: '', tokenPh: 'Вставьте токен', hint: 'Токен создаётся в профиле Jira: «Профиль → Personal Access Tokens».',
  },
  cloud: {
    label: 'Jira Cloud — email и API-токен', login: 'Email', token: 'API-токен',
    loginPh: 'svc-puls@company.ru', tokenPh: 'Вставьте API-токен', hint: 'API-токен создаётся на id.atlassian.com → Security → API tokens.',
  },
}

const saved = ref(null)
const loaded = ref(false)
const form = reactive({ baseUrl: '', instanceType: 'server_basic', login: '', token: '', acceptInvalidCerts: false, startField: '', endField: 'duedate' })
const dirty = ref(false)
const status = ref(null) // { kind: 'ok' | 'error' | 'info', text }
const busy = ref('')
const fields = ref(null)
const type = computed(() => TYPES[form.instanceType])
const tokenPlaceholder = computed(() => (saved.value?.hasToken && sameUrl.value ? 'Сохранён — оставьте пустым, чтобы не менять' : type.value.tokenPh))
const sameUrl = computed(() => saved.value && normalizeUrl(form.baseUrl) === saved.value.baseUrl)

function normalizeUrl(url) { return (url || '').trim().replace(/\/+$/, '') }

function fill(conn) {
  saved.value = conn
  Object.assign(form, conn
    ? { baseUrl: conn.baseUrl, instanceType: conn.instanceType, login: conn.login, token: '', acceptInvalidCerts: conn.acceptInvalidCerts, startField: conn.startField, endField: conn.endField }
    : { baseUrl: '', instanceType: 'server_basic', login: '', token: '', acceptInvalidCerts: false, startField: '', endField: 'duedate' })
  dirty.value = false
  status.value = conn ? { kind: 'ok', text: conn.hasToken ? 'Подключение сохранено' : 'Подключение сохранено без пароля или токена' } : null
}

onMounted(async () => {
  try { fill(await api.get('/settings/jira')) } catch (err) { ui.error(err) } finally { loaded.value = true }
})

function change() { dirty.value = true; if (status.value?.kind !== 'error') status.value = null }
function onUrl() {
  change()
  // Адрес *.atlassian.net — это Jira Cloud
  if (/atlassian\.net/i.test(form.baseUrl)) form.instanceType = 'cloud'
}
function payload() { return { ...form, baseUrl: normalizeUrl(form.baseUrl), login: form.login.trim() } }

async function test() {
  busy.value = 'test'
  status.value = { kind: 'info', text: 'Проверка подключения…' }
  try {
    const res = await api.post('/settings/jira/test', payload())
    status.value = { kind: 'ok', text: `Соединение установлено${res.user ? `: ${res.user}` : ''}` }
  } catch (err) { status.value = { kind: 'error', text: err.message } } finally { busy.value = '' }
}
async function loadFields() {
  busy.value = 'fields'
  try { fields.value = await api.post('/settings/jira/fields', payload()) } catch (err) { status.value = { kind: 'error', text: err.message } } finally { busy.value = '' }
}
async function save() {
  busy.value = 'save'
  try {
    fill(await api.put('/settings/jira', payload()))
    ui.success('Подключение к Jira сохранено')
  } catch (err) { status.value = { kind: 'error', text: err.message } } finally { busy.value = '' }
}
async function disconnect() {
  const ok = await confirm({
    title: 'Отключить Jira?',
    message: 'Подключение и сохранённый пароль или токен будут удалены. Ссылки строк на задачи Jira сохранятся, но статусы и сроки из Jira показываться не будут до нового подключения.',
    confirmText: 'Отключить', danger: true,
  })
  if (!ok) return
  try { await api.delete('/settings/jira'); fill(null); fields.value = null; ui.success('Jira отключена') } catch (err) { ui.error(err) }
}
const fieldOptions = computed(() => fields.value || [])
function fieldLabel(id) { return fieldOptions.value.find((f) => f.id === id)?.name || id }
</script>

<template>
  <div class="page narrow">
    <div class="page-header">
      <h1 class="page-title">Интеграции</h1>
    </div>

    <div v-if="loaded" class="card block" data-testid="jira-settings">
      <div class="head">
        <span class="logo" aria-hidden="true"><AppIcon name="link" :size="18" /></span>
        <div>
          <h2 class="section-title">Подключение к Jira</h2>
          <p class="muted small">Одно подключение для всей системы. Учётная запись, от имени которой «Пульс» читает задачи, — лучше
            сервисная и только с правом просмотра нужных проектов: задачи, которые она видит, увидят все сотрудники с доступом к
            плану. Пароль или токен хранится на сервере зашифрованным и в интерфейс не возвращается.</p>
        </div>
      </div>

      <form class="form" autocomplete="off" @submit.prevent="save">
        <div class="form-grid">
          <label class="field"><span class="field-label">Адрес Jira</span><input v-model="form.baseUrl" class="input" type="url" placeholder="https://jira.company.ru" data-testid="jira-url" @input="onUrl" /></label>
          <label class="field">
            <span class="field-label">Способ входа</span>
            <select v-model="form.instanceType" class="select" @change="change"><option v-for="(t, key) in TYPES" :key="key" :value="key">{{ t.label }}</option></select>
          </label>
          <label class="field"><span class="field-label">{{ type.login }}</span><input v-model="form.login" class="input" :placeholder="type.loginPh" data-testid="jira-login" @input="change" /></label>
          <label class="field"><span class="field-label">{{ type.token }}</span><input v-model="form.token" class="input" type="password" autocomplete="new-password" :placeholder="tokenPlaceholder" data-testid="jira-token" @input="change" /></label>
        </div>
        <p class="muted small hint">{{ type.hint }}</p>
        <label class="check"><input v-model="form.acceptInvalidCerts" type="checkbox" @change="change" /> Не проверять TLS-сертификат (для самоподписанных и корпоративных сертификатов)</label>

        <div class="dates">
          <div class="dates-head">
            <span class="field-label">Поля дат задачи</span>
            <button class="btn btn-sm btn-ghost" type="button" :disabled="!!busy || !form.baseUrl" @click="loadFields"><AppIcon name="refresh" :size="13" /> Загрузить поля из Jira</button>
          </div>
          <div class="form-grid">
            <label class="field">
              <span class="field-label">Начало</span>
              <select v-if="fields" v-model="form.startField" class="select" @change="change">
                <option value="">— не используется —</option>
                <option v-for="f in fieldOptions" :key="f.id" :value="f.id">{{ f.name }} · {{ f.id }}</option>
              </select>
              <input v-else v-model="form.startField" class="input mono" placeholder="customfield_10015" @input="change" />
              <span class="field-hint">Например, «Start date» из Advanced Roadmaps. Без поля начало задачи из Jira не переносится.</span>
            </label>
            <label class="field">
              <span class="field-label">Срок</span>
              <select v-if="fields" v-model="form.endField" class="select" @change="change">
                <option v-for="f in fieldOptions" :key="f.id" :value="f.id">{{ f.name }} · {{ f.id }}</option>
                <option v-if="!fieldOptions.some((f) => f.id === form.endField)" :value="form.endField">{{ fieldLabel(form.endField) }}</option>
              </select>
              <input v-else v-model="form.endField" class="input mono" placeholder="duedate" @input="change" />
              <span class="field-hint">Обычно стандартное поле «Срок» (duedate).</span>
            </label>
          </div>
        </div>

        <div v-if="status" class="status" :class="status.kind" role="status" data-testid="jira-status">
          <AppIcon :name="status.kind === 'ok' ? 'check' : status.kind === 'error' ? 'warning' : 'clock'" :size="14" />
          <span>{{ status.text }}</span>
        </div>

        <div class="footer">
          <button v-if="saved" class="btn btn-ghost danger" type="button" @click="disconnect">Отключить</button>
          <span class="spacer" />
          <button class="btn" type="button" :disabled="!!busy || !form.baseUrl" data-testid="jira-test" @click="test"><AppIcon name="link" :size="14" /> Проверить подключение</button>
          <button class="btn btn-primary" type="submit" :disabled="!!busy || !form.baseUrl || (!dirty && !!saved)" data-testid="jira-save">Сохранить</button>
        </div>
      </form>
    </div>
  </div>
</template>

<style scoped>
.narrow { max-width: 900px; }
.block { padding: 16px 18px; }
.head { display: flex; gap: 12px; align-items: flex-start; margin-bottom: 14px; }
.head p { margin: 4px 0 0; line-height: 1.5; }
.logo { width: 34px; height: 34px; border-radius: 9px; background: #e9f0ff; color: #0052cc; display: grid; place-items: center; flex-shrink: 0; }
.form { display: flex; flex-direction: column; gap: 12px; }
.hint { margin: -4px 0 0; }
.check { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.dates { border-top: 1px solid var(--color-border); padding-top: 12px; display: flex; flex-direction: column; gap: 10px; }
.dates-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }
.status { display: flex; align-items: flex-start; gap: 8px; padding: 8px 10px; border-radius: var(--radius-sm); font-size: 13px; line-height: 1.45; }
.status.ok { background: var(--color-success-soft); color: #1d7a4c; }
.status.error { background: var(--color-danger-soft); color: #b42318; }
.status.info { background: var(--color-hover); color: var(--color-text-secondary); }
.status :deep(svg) { flex-shrink: 0; margin-top: 2px; }
.footer { display: flex; align-items: center; gap: 10px; border-top: 1px solid var(--color-border); padding-top: 12px; }
.danger { color: var(--color-danger); }
</style>
