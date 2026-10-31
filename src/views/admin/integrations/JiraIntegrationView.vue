<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { api } from '../../../api/client'
import { useUiStore } from '../../../stores/ui'
import { useDialogs } from '../../../composables/useDialogs'
import HelpLink from '../../../components/common/HelpLink.vue'
import AppIcon from '../../../components/common/AppIcon.vue'
import IntegrationHeader from '../../../components/integrations/IntegrationHeader.vue'
import DiagnosticsList from '../../../components/integrations/DiagnosticsList.vue'

/**
 * «Интеграции → Jira» (ADR-0007): подключение (как в burndown_tool), другие адреса этой Jira для распознавания ссылок,
 * поля дат, проверка и пошаговая диагностика — почему задачи не загружаются, даже когда «соединение установлено».
 */
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
const EMPTY = { baseUrl: '', instanceType: 'server_basic', login: '', token: '', acceptInvalidCerts: false, startField: '', endField: 'duedate', linkUrls: '' }

const saved = ref(null)
const loaded = ref(false)
const form = reactive({ ...EMPTY })
const dirty = ref(false)
const status = ref(null) // { kind: 'ok' | 'error' | 'info' | 'warn', text, details? }
const tested = ref(false) // проверка прошла для текущих (несохранённых) значений формы
const busy = ref('')
const fields = ref(null)
const diagnostics = ref(null)
const issueKey = ref('')
const type = computed(() => TYPES[form.instanceType])
const sameUrl = computed(() => saved.value && normalizeUrl(form.baseUrl) === saved.value.baseUrl)
const tokenPlaceholder = computed(() => (saved.value?.hasToken && sameUrl.value ? 'Сохранён — оставьте пустым, чтобы не менять' : type.value.tokenPh))

function normalizeUrl(url) { return (url || '').trim().replace(/\/+$/, '') }
function linkList() { return form.linkUrls.split(/[\s,]+/).map(normalizeUrl).filter(Boolean) }

function fill(conn) {
  saved.value = conn
  Object.assign(form, conn
    ? { ...EMPTY, ...conn, token: '', linkUrls: (conn.linkUrls || []).join('\n') }
    : { ...EMPTY })
  dirty.value = false
  tested.value = false
  status.value = conn
    ? { kind: 'ok', text: conn.hasToken ? 'Подключение сохранено — задачи загружаются в планы' : 'Подключение сохранено без пароля или токена' }
    : null
}

onMounted(async () => {
  try { fill(await api.get('/settings/jira')) } catch (err) { ui.error(err) } finally { loaded.value = true }
})

function change() { dirty.value = true; tested.value = false; if (status.value?.kind !== 'error') status.value = null }
function onUrl() {
  change()
  // Адрес *.atlassian.net — это Jira Cloud
  if (/atlassian\.net/i.test(form.baseUrl)) form.instanceType = 'cloud'
}
function payload() {
  const { linkUrls: _links, ...rest } = form
  return { ...rest, baseUrl: normalizeUrl(form.baseUrl), login: form.login.trim(), linkUrls: linkList() }
}

async function test() {
  busy.value = 'test'
  status.value = { kind: 'info', text: 'Проверка подключения…' }
  try {
    const res = await api.post('/settings/jira/test', payload())
    const jira = [res.deploymentType && `Jira ${res.deploymentType}`, res.version].filter(Boolean).join(' ')
    const projects = res.projects === null || res.projects === undefined ? '' : ` · проектов видно: ${res.projects}`
    const text = `Соединение установлено${res.user ? `: ${res.user}` : ''}${jira ? ` · ${jira}` : ''} · поиск задач работает${projects}`
    tested.value = dirty.value || !saved.value
    status.value = {
      kind: res.warnings?.length ? 'warn' : 'ok',
      text: tested.value ? `${text}. Подключение ещё не сохранено — нажмите «Сохранить», чтобы задачи загружались в планы.` : text,
      details: res.warnings || [],
    }
    // Адрес, который Jira считает своим, — предложить добавить в «Другие адреса»
    if (res.baseUrl && normalizeUrl(res.baseUrl) !== normalizeUrl(form.baseUrl) && !linkList().includes(normalizeUrl(res.baseUrl))) {
      suggestedLink.value = normalizeUrl(res.baseUrl)
    }
  } catch (err) { status.value = { kind: 'error', text: err.message } } finally { busy.value = '' }
}
const suggestedLink = ref('')
function addSuggested() {
  form.linkUrls = [...linkList(), suggestedLink.value].join('\n')
  suggestedLink.value = ''
  change()
}

async function diagnose() {
  busy.value = 'diagnose'
  diagnostics.value = null
  try {
    const body = dirty.value ? payload() : {}
    if (issueKey.value.trim()) body.issueKey = issueKey.value.trim()
    diagnostics.value = (await api.post('/settings/jira/diagnose', body)).steps
  } catch (err) {
    diagnostics.value = [{ id: 'error', title: 'Диагностика', status: 'error', detail: err.message }]
  } finally { busy.value = '' }
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
  try { await api.delete('/settings/jira'); fill(null); fields.value = null; diagnostics.value = null; ui.success('Jira отключена') } catch (err) { ui.error(err) }
}
const fieldOptions = computed(() => fields.value || [])
function fieldLabel(id) { return fieldOptions.value.find((f) => f.id === id)?.name || id }

// Несохранённое подключение не пропадает молча: «соединение установлено» без «Сохранить» — частая причина
// «Jira подключена, но ничего не загружается»
onBeforeRouteLeave(async () => {
  if (!dirty.value) return true
  const choice = await ui.openDialog({
    type: 'choice', title: 'Сохранить подключение к Jira?',
    message: 'Изменения подключения ещё не сохранены — без сохранения задачи Jira в планах загружаться не будут.',
    cancelText: 'Остаться', choices: [{ value: 'discard', label: 'Не сохранять', danger: true }, { value: 'save', label: 'Сохранить' }],
  })
  if (choice === 'save') { await save(); return !dirty.value }
  return choice === 'discard'
})
function onBeforeUnload(event) {
  if (!dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}
onMounted(() => window.addEventListener('beforeunload', onBeforeUnload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', onBeforeUnload))

const stateBadge = computed(() => {
  if (!saved.value) return { kind: 'muted', text: 'Не подключена' }
  if (dirty.value) return { kind: 'warn', text: 'Есть несохранённые изменения' }
  return { kind: 'ok', text: 'Подключена' }
})
</script>

<template>
  <div class="page narrow">
    <IntegrationHeader title="Jira" subtitle="Статусы, сроки и прогресс задач Jira в планах; задачи, релизы и спринты — строками плана" logo="jira" :badge="stateBadge" help="admin-jira" />

    <div v-if="loaded" class="card block" data-testid="jira-settings">
      <h2 class="section-title">Подключение <HelpLink topic="admin-jira" /></h2>
      <p class="muted small lead">Одно подключение для всей системы. Учётная запись, от имени которой «Пульс» читает задачи, — лучше сервисная
        и только с правом просмотра нужных проектов: задачи, которые она видит, увидят все сотрудники с доступом к плану. Пароль или
        токен хранится на сервере зашифрованным и в интерфейс не возвращается.</p>

      <form class="form" autocomplete="off" @submit.prevent="save">
        <div class="form-grid">
          <label class="field"><span class="field-label">Адрес Jira</span><input v-model="form.baseUrl" class="input" type="url" placeholder="https://jira.company.ru" data-testid="jira-url" @input="onUrl" /></label>
          <label class="field">
            <span class="field-label">Способ входа</span>
            <select v-model="form.instanceType" class="select" data-testid="jira-type" @change="change"><option v-for="(t, key) in TYPES" :key="key" :value="key">{{ t.label }}</option></select>
          </label>
          <label class="field"><span class="field-label">{{ type.login }}</span><input v-model="form.login" class="input" :placeholder="type.loginPh" data-testid="jira-login" @input="change" /></label>
          <label class="field"><span class="field-label">{{ type.token }}</span><input v-model="form.token" class="input" type="password" autocomplete="new-password" :placeholder="tokenPlaceholder" data-testid="jira-token" @input="change" /></label>
        </div>
        <p class="muted small hint">{{ type.hint }}</p>
        <label class="check"><input v-model="form.acceptInvalidCerts" type="checkbox" @change="change" /> Не проверять TLS-сертификат (для самоподписанных и корпоративных сертификатов)</label>

        <div class="subsection">
          <div class="sub-head">
            <span class="field-label">Другие адреса этой Jira</span>
            <span class="muted small">для распознавания ссылок в строках планов</span>
          </div>
          <textarea v-model="form.linkUrls" class="input mono links" rows="2" placeholder="https://jira.old-company.ru&#10;http://10.0.0.15:8080" data-testid="jira-link-urls" @input="change" />
          <span class="field-hint">Если ссылки на задачи в планах ведут на другой адрес той же Jira (старый домен, адрес по IP, адрес из
            настроек Jira) — укажите его по одному в строке. Запросы к Jira идут только на основной адрес.</span>
          <div v-if="suggestedLink" class="suggest-link">
            <AppIcon name="info" :size="14" /> Jira сообщает свой адрес как <b class="mono">{{ suggestedLink }}</b>.
            <button class="btn btn-sm" type="button" data-testid="jira-add-link" @click="addSuggested">Добавить в другие адреса</button>
          </div>
        </div>

        <div class="subsection">
          <div class="sub-head">
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
          <AppIcon :name="status.kind === 'ok' ? 'check' : status.kind === 'info' ? 'clock' : 'warning'" :size="14" />
          <span>{{ status.text }}<template v-for="d in status.details || []" :key="d"><br />{{ d }}</template></span>
        </div>

        <div class="footer">
          <button v-if="saved" class="btn btn-ghost danger" type="button" @click="disconnect">Отключить</button>
          <span class="spacer" />
          <button class="btn" type="button" :disabled="!!busy || !form.baseUrl" data-testid="jira-test" @click="test"><AppIcon name="link" :size="14" /> {{ busy === 'test' ? 'Проверяем…' : 'Проверить подключение' }}</button>
          <button class="btn btn-primary" :class="{ pulse: tested }" type="submit" :disabled="!!busy || !form.baseUrl || (!dirty && !!saved)" data-testid="jira-save">Сохранить</button>
        </div>
      </form>
    </div>

    <div v-if="loaded && (saved || form.baseUrl)" class="card block" data-testid="jira-diagnostics">
      <div class="diag-head">
        <div>
          <h2 class="section-title">Диагностика</h2>
          <p class="muted small lead">Пошаговая проверка: сеть, вход, право «Просмотр проектов», видимые проекты, поиск, поля, конкретная задача
            и ссылки строк планов. Покажет, почему задачи не загружаются, даже если соединение установлено.</p>
        </div>
      </div>
      <div class="diag-form">
        <input v-model="issueKey" class="input mono key-input" placeholder="Ключ задачи для проверки, например ABC-123" maxlength="70" data-testid="jira-diag-key" @keydown.enter.prevent="diagnose" />
        <button class="btn" type="button" :disabled="!!busy" data-testid="jira-diagnose" @click="diagnose"><AppIcon name="checklist" :size="14" /> {{ busy === 'diagnose' ? 'Проверяем…' : 'Запустить диагностику' }}</button>
      </div>
      <DiagnosticsList v-if="diagnostics" :steps="diagnostics" data-testid="jira-diag-steps" />
    </div>
  </div>
</template>

<style scoped>
.narrow { max-width: 920px; }
.block { padding: 18px 20px; margin-bottom: 16px; }
.lead { margin: 4px 0 14px; line-height: 1.5; }
.form { display: flex; flex-direction: column; gap: 12px; }
.hint { margin: -4px 0 0; }
.check { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.subsection { border-top: 1px solid var(--color-border); padding-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.sub-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.links { resize: vertical; min-height: 54px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }
.suggest-link { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px; padding: 8px 10px; border-radius: var(--radius-sm); background: #eef4ff; color: #1d3f8f; }
.status { display: flex; align-items: flex-start; gap: 8px; padding: 9px 12px; border-radius: var(--radius-sm); font-size: 13px; line-height: 1.5; }
.status.ok { background: var(--color-success-soft); color: #1d7a4c; }
.status.warn { background: var(--color-warning-soft); color: #8a5a00; }
.status.error { background: var(--color-danger-soft); color: #b42318; }
.status.info { background: var(--color-hover); color: var(--color-text-secondary); }
.status :deep(svg) { flex-shrink: 0; margin-top: 3px; }
.footer { display: flex; align-items: center; gap: 10px; border-top: 1px solid var(--color-border); padding-top: 12px; }
.danger { color: var(--color-danger); }
.pulse { box-shadow: 0 0 0 0 rgba(79, 124, 255, 0.55); animation: pulse 1.6s ease-out infinite; }
@keyframes pulse { 70% { box-shadow: 0 0 0 8px rgba(79, 124, 255, 0); } 100% { box-shadow: 0 0 0 0 rgba(79, 124, 255, 0); } }
@media (prefers-reduced-motion: reduce) { .pulse { animation: none; } }
.diag-form { display: flex; gap: 10px; flex-wrap: wrap; }
.key-input { flex: 1 1 260px; }
@media (max-width: 768px) { .footer { flex-wrap: wrap; } }
</style>
