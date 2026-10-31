<script setup>
import HelpLink from '../common/HelpLink.vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../../api/client'
import { useUiStore } from '../../stores/ui'
import { useDialogs } from '../../composables/useDialogs'
import { resetPodelu } from '../../integrations/podelu'
import AppIcon from '../common/AppIcon.vue'

/**
 * Подключение к «По Делу» (ADR-0009): адрес, сервисная учётная запись и пароль (хранится на сервере
 * зашифрованным и в интерфейс не возвращается). «По Делу» не выдаёт API-токенов — «Пульс» входит так же,
 * как браузер. Отсюда — переход к загрузке структуры и сотрудников.
 */
const ui = useUiStore()
const { confirm } = useDialogs()

const saved = ref(null)
const loaded = ref(false)
const form = reactive({ baseUrl: '', login: '', password: '', acceptInvalidCerts: false })
const dirty = ref(false)
const status = ref(null) // { kind: 'ok' | 'error' | 'info', text }
const busy = ref('')

function normalizeUrl(url) { return (url || '').trim().replace(/\/+$/, '') }
const sameAccount = computed(() => saved.value && normalizeUrl(form.baseUrl) === saved.value.baseUrl && form.login.trim() === saved.value.login)
const passwordPlaceholder = computed(() => (saved.value?.hasPassword && sameAccount.value ? 'Сохранён — оставьте пустым, чтобы не менять' : 'Пароль учётной записи в «По Делу»'))

function fill(conn) {
  saved.value = conn
  Object.assign(form, conn
    ? { baseUrl: conn.baseUrl, login: conn.login, password: '', acceptInvalidCerts: conn.acceptInvalidCerts }
    : { baseUrl: '', login: '', password: '', acceptInvalidCerts: false })
  dirty.value = false
  status.value = conn ? { kind: 'ok', text: 'Подключение сохранено' } : null
}

onMounted(async () => {
  try { fill(await api.get('/settings/podelu')) } catch (err) { ui.error(err) } finally { loaded.value = true }
})

function change() { dirty.value = true; if (status.value?.kind !== 'error') status.value = null }
function payload() { return { ...form, baseUrl: normalizeUrl(form.baseUrl), login: form.login.trim() } }

async function test() {
  busy.value = 'test'
  status.value = { kind: 'info', text: 'Проверка подключения…' }
  try {
    const res = await api.post('/settings/podelu/test', payload())
    const who = [res.user?.name, res.user?.login && `(${res.user.login})`].filter(Boolean).join(' ')
    status.value = res.problem
      ? { kind: 'error', text: res.problem }
      : { kind: 'ok', text: `Соединение установлено${who ? `: ${who}` : ''} · права на выгрузку структуры и сотрудников есть` }
  } catch (err) { status.value = { kind: 'error', text: err.message } } finally { busy.value = '' }
}
async function save() {
  busy.value = 'save'
  try {
    fill(await api.put('/settings/podelu', payload()))
    resetPodelu()
    ui.success('Подключение к «По Делу» сохранено')
  } catch (err) { status.value = { kind: 'error', text: err.message } } finally { busy.value = '' }
}
async function disconnect() {
  const ok = await confirm({
    title: 'Отключить «По Делу»?',
    message: 'Подключение и сохранённый пароль будут удалены. Загруженные подразделения и сотрудники останутся, ссылки на «По Делу» показываться не будут до нового подключения.',
    confirmText: 'Отключить', danger: true,
  })
  if (!ok) return
  try { await api.delete('/settings/podelu'); fill(null); resetPodelu(); ui.success('«По Делу» отключена') } catch (err) { ui.error(err) }
}
</script>

<template>
  <div v-if="loaded" class="card block" data-testid="podelu-settings">
    <div class="head">
      <div>
        <h2 class="section-title">Структура и сотрудники из «По Делу» <HelpLink topic="admin-podelu" /></h2>
        <p class="muted small">«Пульс» входит в «По Делу» сервисной учётной записью и забирает оттуда структуру компании и сотрудников, а в
          «Пульсе» появляются ссылки на подразделения и сотрудников в «По Делу». Пароль хранится на сервере зашифрованным и в интерфейс не
          возвращается; сотрудники видят только адрес «По Делу».</p>
      </div>
    </div>

    <form class="form" autocomplete="off" @submit.prevent="save">
      <div class="form-grid">
        <label class="field wide"><span class="field-label">Адрес «По Делу»</span><input v-model="form.baseUrl" class="input" type="url" placeholder="https://podelu.company.ru" data-testid="podelu-url" @input="change" /></label>
        <label class="field"><span class="field-label">Логин</span><input v-model="form.login" class="input" placeholder="svc-puls" data-testid="podelu-login" @input="change" /></label>
        <label class="field"><span class="field-label">Пароль</span><input v-model="form.password" class="input" type="password" autocomplete="new-password" :placeholder="passwordPlaceholder" data-testid="podelu-password" @input="change" /></label>
      </div>
      <p class="muted small hint">Адрес — тот, по которому сотрудники открывают «По Делу». Учётной записи нужна роль «Администратор» или полномочия
        «Ведёт структуру компании» и «Ведёт учётные записи». Выгрузки «По Делу» отдаёт только после входа по логину и паролю, как в браузере, поэтому используйте адрес https://.</p>
      <label class="check"><input v-model="form.acceptInvalidCerts" type="checkbox" data-testid="podelu-insecure" @change="change" /> Не проверять TLS-сертификат (для самоподписанных и корпоративных сертификатов)</label>

      <div v-if="status" class="status" :class="status.kind" role="status" data-testid="podelu-status">
        <AppIcon :name="status.kind === 'ok' ? 'check' : status.kind === 'error' ? 'warning' : 'clock'" :size="14" />
        <span>{{ status.text }}</span>
      </div>

      <div class="footer">
        <button v-if="saved" class="btn btn-ghost danger" type="button" @click="disconnect">Отключить</button>
        <router-link v-if="saved && !dirty" class="btn btn-ghost" to="/admin/org-transfer?tab=podelu" data-testid="podelu-sync-link"><AppIcon name="download" :size="14" /> Загрузить структуру и сотрудников</router-link>
        <span class="spacer" />
        <button class="btn" type="button" :disabled="!!busy || !form.baseUrl || !form.login" data-testid="podelu-test" @click="test"><AppIcon name="link" :size="14" /> Проверить подключение</button>
        <button class="btn btn-primary" type="submit" :disabled="!!busy || !form.baseUrl || !form.login || (!dirty && !!saved)" data-testid="podelu-save">Сохранить</button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.block { padding: 18px 20px; margin-bottom: 16px; }
.head { display: flex; gap: 12px; align-items: flex-start; margin-bottom: 14px; }
.head p { margin: 4px 0 0; line-height: 1.5; }
.form { display: flex; flex-direction: column; gap: 12px; }
.wide { grid-column: 1 / -1; }
.hint { margin: -4px 0 0; line-height: 1.5; }
.check { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.status { display: flex; align-items: flex-start; gap: 8px; padding: 8px 10px; border-radius: var(--radius-sm); font-size: 13px; line-height: 1.45; }
.status.ok { background: var(--color-success-soft); color: #1d7a4c; }
.status.error { background: var(--color-danger-soft); color: #b42318; }
.status.info { background: var(--color-hover); color: var(--color-text-secondary); }
.status :deep(svg) { flex-shrink: 0; margin-top: 2px; }
.footer { display: flex; align-items: center; gap: 10px; border-top: 1px solid var(--color-border); padding-top: 12px; flex-wrap: wrap; }
.danger { color: var(--color-danger); }
</style>
