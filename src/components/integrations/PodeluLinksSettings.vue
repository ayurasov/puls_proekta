<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../../api/client'
import { useUiStore } from '../../stores/ui'
import { useDialogs } from '../../composables/useDialogs'
import { formatDateTime } from '../../utils/format'
import AppIcon from '../common/AppIcon.vue'
import HelpLink from '../common/HelpLink.vue'
import ToggleSwitch from '../common/ToggleSwitch.vue'
import { resetPodeluLinks } from '../../integrations/podeluTasks'

/**
 * Ответная интеграция с «По Делу» (ADR-0010): обмен ключами, проверка связи и показ задач «По Делу» в проектах.
 * 1) ключ «Пульса» — выпускается здесь, вводится в «По Делу»; 2) адрес и ключ «По Делу» — выпускается там, вводится
 * здесь; 3) после успешной проверки — показ задач «По Делу», которые ссылаются на проекты и строки планов.
 */
const emit = defineEmits(['changed'])
const ui = useUiStore()
const { confirm } = useDialogs()

const saved = ref(null)
const form = reactive({ baseUrl: '', key: '', acceptInvalidCerts: false })
const dirty = ref(false)
const busy = ref('')
const status = ref(null) // { kind, text, details? }
const issued = ref(null) // { key, prefix, createdAt } — ключ показывается один раз
const copied = ref(false)

function normalizeUrl(url) { return (url || '').trim().replace(/\/+$/, '') }
const keyPlaceholder = computed(() => (saved.value?.hasKey && normalizeUrl(form.baseUrl) === saved.value.baseUrl
  ? 'Сохранён — оставьте пустым, чтобы не менять' : 'podelu_…'))

function fill(data) {
  saved.value = data
  Object.assign(form, { baseUrl: data.baseUrl || data.suggestedBaseUrl || '', key: '', acceptInvalidCerts: data.acceptInvalidCerts })
  dirty.value = !data.baseUrl && !!data.suggestedBaseUrl
}
onMounted(async () => {
  try { fill(await api.get('/settings/podelu/links')) } catch (err) { ui.error(err) }
})
function change() { dirty.value = true; if (status.value?.kind !== 'error') status.value = null }
function payload(extra = {}) {
  return { baseUrl: normalizeUrl(form.baseUrl), key: form.key.trim(), acceptInvalidCerts: form.acceptInvalidCerts, enabled: saved.value?.enabled || false, ...extra }
}

async function issueKey() {
  if (saved.value?.inbound && !await confirm({
    title: 'Выпустить новый ключ «Пульса»?', message: 'Прежний ключ сразу перестанет действовать — «По Делу» не сможет искать проекты, пока в ней не введут новый ключ.',
    confirmText: 'Выпустить новый',
  })) return
  busy.value = 'key'
  try {
    issued.value = await api.post('/settings/podelu/links/key')
    copied.value = false
    saved.value = await api.get('/settings/podelu/links')
  } catch (err) { ui.error(err) } finally { busy.value = '' }
}
async function revokeKey() {
  if (!await confirm({ title: 'Отозвать ключ «Пульса»?', message: '«По Делу» перестанет видеть проекты «Пульса»; ссылки, уже сохранённые в задачах, останутся.', confirmText: 'Отозвать', danger: true })) return
  try {
    await api.delete('/settings/podelu/links/key')
    issued.value = null
    saved.value = await api.get('/settings/podelu/links')
    ui.success('Ключ «Пульса» отозван')
  } catch (err) { ui.error(err) }
}
async function copyKey() {
  try {
    await navigator.clipboard.writeText(issued.value.key)
    copied.value = true
  } catch {
    ui.error('Не удалось скопировать — выделите ключ и скопируйте вручную')
  }
}

async function save(extra = {}) {
  busy.value = 'save'
  try {
    const data = await api.put('/settings/podelu/links', payload(extra))
    fill(data)
    resetPodeluLinks()
    emit('changed')
    return true
  } catch (err) {
    status.value = { kind: 'error', text: err.message }
    return false
  } finally { busy.value = '' }
}
async function saveAndCheck() {
  if (dirty.value && !await save()) return
  busy.value = 'test'
  status.value = { kind: 'info', text: 'Проверяем связь с «По Делу»…' }
  try {
    const res = await api.post('/settings/podelu/links/test', {})
    fill(res.settings)
    status.value = {
      kind: res.warnings.length ? 'warn' : 'ok',
      text: `Связь установлена: ${res.name}${res.version ? ` ${res.version}` : ''} принимает ключ`,
      details: res.warnings,
    }
  } catch (err) { status.value = { kind: 'error', text: err.message } } finally { busy.value = '' }
}
async function toggleEnabled(value) {
  if (await save({ enabled: value })) ui.success(value ? 'Задачи «По Делу» показываются в проектах' : 'Показ задач «По Делу» выключен')
}
async function removeAll() {
  if (!await confirm({ title: 'Отключить ссылки из задач «По Делу»?', message: 'Адрес, ключ «По Делу» и ключ «Пульса» будут удалены. Ссылки в задачах «По Делу» останутся, но связь перестанет работать в обе стороны.', confirmText: 'Отключить', danger: true })) return
  try {
    await api.delete('/settings/podelu/links')
    issued.value = null
    status.value = null
    fill(await api.get('/settings/podelu/links'))
    resetPodeluLinks()
    emit('changed')
  } catch (err) { ui.error(err) }
}
const checked = computed(() => !!saved.value?.checkedAt)
const canEnable = computed(() => checked.value && !dirty.value)
</script>

<template>
  <div v-if="saved" class="card block" data-testid="podelu-links">
    <div class="head">
      <div>
        <h2 class="section-title">Ссылки из задач «По Делу» <HelpLink topic="admin-podelu-links" /></h2>
        <p class="muted small lead">Сотрудники связывают задачи «По Делу» с проектами «Пульса» и конкретными строками планов, а в «Пульсе»
          на проекте и в карточке строки видны эти задачи: статус, исполнитель и срок. Связь настраивается обменом ключами — по шагам ниже.</p>
      </div>
    </div>

    <ol class="steps">
      <li class="step" :class="{ done: saved.inbound }">
        <span class="num">1</span>
        <div class="step-body">
          <div class="step-title">Ключ «Пульса» для «По Делу»</div>
          <p class="muted small">«По Делу» ищет с ним проекты и строки планов — только те, что доступны сотруднику в «Пульсе». Вставьте ключ в «По Делу»:
            Настройки → Интеграции → «Пульс проекта».</p>
          <div v-if="issued" class="issued" data-testid="podelu-links-issued">
            <code class="key mono">{{ issued.key }}</code>
            <button class="btn btn-sm" type="button" data-testid="podelu-links-copy" @click="copyKey"><AppIcon :name="copied ? 'check' : 'copy'" :size="13" /> {{ copied ? 'Скопирован' : 'Скопировать' }}</button>
            <p class="small warn-text"><AppIcon name="warning" :size="13" /> Ключ показывается один раз — скопируйте его сейчас.</p>
          </div>
          <div v-else-if="saved.inbound" class="key-meta small">
            <span class="badge badge-success">Выпущен</span>
            <span class="mono">{{ saved.inbound.prefix }}…</span>
            <span class="muted">{{ formatDateTime(saved.inbound.createdAt) }}</span>
            <span class="muted">· {{ saved.inbound.lastUsedAt ? `«По Делу» обращалась ${formatDateTime(saved.inbound.lastUsedAt)}` : '«По Делу» ещё не обращалась' }}</span>
          </div>
          <div class="row-actions">
            <button class="btn btn-sm" :class="{ 'btn-primary': !saved.inbound }" type="button" :disabled="!!busy" data-testid="podelu-links-issue" @click="issueKey">
              <AppIcon name="key" :size="13" /> {{ saved.inbound ? 'Выпустить новый' : 'Выпустить ключ' }}
            </button>
            <button v-if="saved.inbound" class="btn btn-sm btn-ghost danger" type="button" @click="revokeKey">Отозвать</button>
          </div>
        </div>
      </li>

      <li class="step" :class="{ done: saved.hasKey && checked }">
        <span class="num">2</span>
        <div class="step-body">
          <div class="step-title">Адрес и ключ «По Делу»</div>
          <p class="muted small">Ключ выпускается в «По Делу» (Настройки → Интеграции → «Пульс проекта» → «Ключ для «Пульса»»): с ним «Пульс» читает
            задачи, которые ссылаются на проекты. Хранится зашифрованным и в интерфейс не возвращается.</p>
          <div class="form-grid">
            <label class="field"><span class="field-label">Адрес «По Делу»</span><input v-model="form.baseUrl" class="input" type="url" placeholder="https://podelu.company.ru" data-testid="podelu-links-url" @input="change" /></label>
            <label class="field"><span class="field-label">Ключ «По Делу»</span><input v-model="form.key" class="input mono" type="password" autocomplete="new-password" :placeholder="keyPlaceholder" data-testid="podelu-links-key" @input="change" /></label>
          </div>
          <label class="check small"><input v-model="form.acceptInvalidCerts" type="checkbox" @change="change" /> Не проверять TLS-сертификат</label>
          <div class="row-actions">
            <button class="btn btn-sm btn-primary" type="button" :disabled="!!busy || !form.baseUrl" data-testid="podelu-links-check" @click="saveAndCheck">
              <AppIcon name="link" :size="13" /> {{ busy === 'test' ? 'Проверяем…' : dirty ? 'Сохранить и проверить' : 'Проверить связь' }}
            </button>
            <span v-if="checked && !dirty" class="small ok-text"><AppIcon name="check" :size="13" /> Проверено {{ formatDateTime(saved.checkedAt) }}{{ saved.checkedVersion ? ` · «По Делу» ${saved.checkedVersion}` : '' }}</span>
          </div>
          <div v-if="status" class="status" :class="status.kind" role="status" data-testid="podelu-links-status">
            <AppIcon :name="status.kind === 'ok' ? 'check' : status.kind === 'info' ? 'clock' : 'warning'" :size="14" />
            <span>{{ status.text }}<template v-for="d in status.details || []" :key="d"><br />{{ d }}</template></span>
          </div>
        </div>
      </li>

      <li class="step" :class="{ done: saved.enabled }">
        <span class="num">3</span>
        <div class="step-body">
          <ToggleSwitch
            :model-value="saved.enabled" :disabled="!canEnable && !saved.enabled" data-testid="podelu-links-enabled"
            label="Показывать задачи «По Делу» в проектах"
            :hint="canEnable || saved.enabled ? 'Кнопка «По Делу» в заголовке проекта и раздел в карточке строки плана.' : 'Включается после успешной проверки связи (шаг 2).'"
            @update:model-value="toggleEnabled"
          />
        </div>
      </li>
    </ol>

    <div v-if="saved.baseUrl || saved.inbound" class="footer">
      <button class="btn btn-ghost danger btn-sm" type="button" @click="removeAll">Отключить ссылки из задач</button>
    </div>
  </div>
</template>

<style scoped>
.block { padding: 18px 20px; margin-bottom: 16px; }
.lead { margin: 4px 0 6px; line-height: 1.5; }
.steps { list-style: none; margin: 10px 0 0; padding: 0; display: flex; flex-direction: column; }
.step { display: flex; gap: 14px; padding: 14px 0; border-top: 1px solid var(--color-border); }
.num { width: 28px; height: 28px; border-radius: 50%; display: grid; place-items: center; font-weight: 700; font-size: 13px; background: #eef1f7; color: var(--color-text-secondary); flex-shrink: 0; }
.step.done .num { background: #22a06b; color: #fff; }
.step-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.step-body p { margin: 0; line-height: 1.5; }
.step-title { font-weight: 700; font-size: 14px; }
.row-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.issued { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 10px 12px; border-radius: var(--radius-md); background: #f3f6ff; border: 1px dashed #9db5ff; }
.key { font-size: 12.5px; padding: 4px 8px; background: #fff; border-radius: 6px; border: 1px solid var(--color-border); overflow-wrap: anywhere; user-select: all; }
.key-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.warn-text { color: #8a5a00; display: flex; align-items: center; gap: 6px; margin: 0; flex-basis: 100%; }
.ok-text { color: #1d7a4c; display: inline-flex; align-items: center; gap: 4px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.check { display: flex; align-items: center; gap: 8px; }
.status { display: flex; align-items: flex-start; gap: 8px; padding: 9px 12px; border-radius: var(--radius-sm); font-size: 13px; line-height: 1.5; }
.status.ok { background: var(--color-success-soft); color: #1d7a4c; }
.status.warn { background: var(--color-warning-soft); color: #8a5a00; }
.status.error { background: var(--color-danger-soft); color: #b42318; }
.status.info { background: var(--color-hover); color: var(--color-text-secondary); }
.status :deep(svg) { flex-shrink: 0; margin-top: 3px; }
.footer { border-top: 1px solid var(--color-border); padding-top: 10px; }
.danger { color: var(--color-danger); }
</style>
