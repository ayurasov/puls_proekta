<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../../api/client'
import { useUiStore } from '../../../stores/ui'
import { useDialogs } from '../../../composables/useDialogs'
import { formatDateTime } from '../../../utils/format'
import AppIcon from '../../../components/common/AppIcon.vue'
import BaseModal from '../../../components/common/BaseModal.vue'
import SegmentedControl from '../../../components/common/SegmentedControl.vue'
import IntegrationHeader from '../../../components/integrations/IntegrationHeader.vue'
import { APPS, SCOPES, appLabel, normalizeScopes, scopeLabel, sortKeys, toggleScope } from '../../../integrations/integrationKeys'

/**
 * «Интеграции → Ключи доступа» (ADR-0011): у каждой системы свой ключ для API `/api/integration/v1` и свои права.
 * Ключ показывается один раз — при выпуске и перевыпуске; отозванный ключ остаётся в списке для истории.
 */
const ui = useUiStore()
const { confirm } = useDialogs()

const keys = ref([])
const loaded = ref(false)
const showRevoked = ref(false)
const journal = ref([])
const editor = ref(null)  // { mode: 'issue' | 'edit', id?, name, app, scopes }
const shown = ref(null)   // { key, item, reissued } — ключ, который нужно скопировать сейчас
const copied = ref(false)
const busy = ref(false)

async function load() {
  try {
    const [data, audit] = await Promise.all([
      api.get('/settings/integration-keys'),
      api.get('/audit', { section: 'Интеграции', limit: 15 }).catch(() => ({ rows: [] })),
    ])
    keys.value = data.keys
    journal.value = audit.rows || []
  } catch (err) { ui.error(err) } finally { loaded.value = true }
}
onMounted(load)

const active = computed(() => sortKeys(keys.value.filter((k) => k.active)))
const revoked = computed(() => sortKeys(keys.value.filter((k) => !k.active)))

function openIssue() {
  const app = keys.value.some((k) => k.active && k.app === 'dashboards') ? 'other' : 'dashboards'
  const preset = APPS.find((a) => a.value === app)
  editor.value = { mode: 'issue', name: preset.name, app, scopes: [...preset.scopes] }
}
function openEdit(item) {
  editor.value = { mode: 'edit', id: item.id, name: item.name, app: item.app, scopes: [...item.scopes] }
}
function chooseApp(value) {
  const preset = APPS.find((a) => a.value === value)
  const previous = APPS.find((a) => a.value === editor.value.app)
  if (!editor.value.name.trim() || editor.value.name === previous?.name) editor.value.name = preset.name
  editor.value.app = value
  editor.value.scopes = [...preset.scopes]
}
const canSave = computed(() => editor.value && editor.value.name.trim() && normalizeScopes(editor.value.scopes).length > 0)

async function save() {
  if (!canSave.value) return
  busy.value = true
  try {
    const body = { name: editor.value.name.trim(), scopes: normalizeScopes(editor.value.scopes) }
    if (editor.value.mode === 'issue') {
      const res = await api.post('/settings/integration-keys', { ...body, app: editor.value.app })
      shown.value = { key: res.key, item: res.item, reissued: false }
      copied.value = false
    } else {
      await api.patch(`/settings/integration-keys/${editor.value.id}`, body)
      ui.success('Ключ изменён')
    }
    editor.value = null
    await load()
  } catch (err) { ui.error(err) } finally { busy.value = false }
}

async function reissue(item) {
  const ok = await confirm({
    title: `Перевыпустить ключ «${item.name}»?`,
    message: `Прежний ключ ${item.prefix}… перестанет действовать сразу — ${appLabel(item.app)} не сможет читать данные, пока в ней не введут новый ключ.`,
    confirmText: 'Перевыпустить',
  })
  if (!ok) return
  try {
    const res = await api.post(`/settings/integration-keys/${item.id}/reissue`)
    shown.value = { key: res.key, item: res.item, reissued: true }
    copied.value = false
    await load()
  } catch (err) { ui.error(err) }
}

async function revoke(item) {
  const ok = await confirm({
    title: `Отозвать ключ «${item.name}»?`,
    message: `${appLabel(item.app)} сразу потеряет доступ к API «Пульса». Отозванный ключ нельзя вернуть — только выпустить новый.`,
    confirmText: 'Отозвать', danger: true,
  })
  if (!ok) return
  try {
    await api.delete(`/settings/integration-keys/${item.id}`)
    ui.success('Ключ отозван')
    await load()
  } catch (err) { ui.error(err) }
}

async function copyKey() {
  try {
    await navigator.clipboard.writeText(shown.value.key)
    copied.value = true
  } catch {
    ui.error('Не удалось скопировать — выделите ключ и скопируйте вручную')
  }
}

const appOptions = APPS.map((a) => ({ value: a.value, label: a.label }))
const badge = computed(() => (loaded.value
  ? (active.value.length ? { kind: 'ok', text: `Действующих: ${active.value.length}` } : { kind: 'muted', text: 'Ключей нет' })
  : null))
const ACTIONS = { create: 'выпущен', update: 'изменён', delete: 'отозван' }
function journalText(row) {
  if (row.field === 'key' && row.action === 'update') return `перевыпущен: ${row.oldValue} → ${row.newValue}`
  if (row.field === 'key') return `${ACTIONS[row.action] || row.actionLabel} ${row.newValue || row.oldValue}`
  if (row.field === 'scopes') return `права: ${row.oldValue || '—'} → ${row.newValue}`
  if (row.field === 'name') return `название: «${row.oldValue}» → «${row.newValue}»`
  return `${row.fieldLabel || row.field}: ${row.oldLabel || row.oldValue} → ${row.newLabel || row.newValue}`
}
</script>

<template>
  <div class="page narrow">
    <IntegrationHeader title="Ключи доступа" subtitle="Другие системы читают проекты «Пульса» по своим ключам и с теми правами, что вы им выдали" logo="keys" :badge="badge" help="admin-integration-keys" />

    <div class="card block" data-testid="integration-keys">
      <div class="head">
        <div>
          <h2 class="section-title">Ключи систем</h2>
          <p class="muted small lead">У каждой системы — свой ключ: его можно перевыпустить или отозвать, не затрагивая другие. Ключ передаётся
            в заголовке <code>Authorization: Bearer puls_…</code> к <code>/api/integration/v1</code>; «Пульс» хранит только его отпечаток.</p>
        </div>
        <button class="btn btn-primary" type="button" data-testid="key-issue-open" @click="openIssue"><AppIcon name="key" :size="14" /> Выпустить ключ</button>
      </div>

      <p v-if="loaded && !active.length" class="empty" data-testid="keys-empty">Ключей пока нет. Ключ для «По Делу» выпускается и на странице
        <router-link to="/admin/integrations/podelu">«По Делу»</router-link>.</p>
      <ul v-else class="keys">
        <li v-for="item in active" :key="item.id" class="key-row" data-testid="key-row">
          <div class="key-main">
            <div class="key-title">
              <span class="name">{{ item.name }}</span>
              <span class="app">{{ item.appLabel || appLabel(item.app) }}</span>
            </div>
            <div class="scopes">
              <span v-for="s in item.scopes" :key="s" class="scope" :class="`scope-${s.replace('.', '-')}`" :title="s">{{ scopeLabel(s) }}</span>
            </div>
            <div class="meta small muted">
              <span class="mono">{{ item.prefix }}…</span>
              <span>выпущен {{ formatDateTime(item.issuedAt) }}<template v-if="item.issuedBy"> · {{ item.issuedBy }}</template></span>
              <span>{{ item.lastUsedAt ? `последний запрос ${formatDateTime(item.lastUsedAt)}` : 'запросов ещё не было' }}</span>
            </div>
          </div>
          <div class="key-actions">
            <button class="btn btn-sm" type="button" data-testid="key-edit" @click="openEdit(item)"><AppIcon name="edit" :size="13" /> Права</button>
            <button class="btn btn-sm" type="button" data-testid="key-reissue" @click="reissue(item)"><AppIcon name="refresh" :size="13" /> Перевыпустить</button>
            <button class="btn btn-sm btn-ghost danger" type="button" data-testid="key-revoke" @click="revoke(item)">Отозвать</button>
          </div>
        </li>
      </ul>

      <div v-if="revoked.length" class="revoked">
        <button class="btn btn-ghost btn-sm" type="button" :aria-expanded="showRevoked" data-testid="keys-revoked-toggle" @click="showRevoked = !showRevoked">
          <AppIcon :name="showRevoked ? 'chevronDown' : 'chevronRight'" :size="13" /> Отозванные ({{ revoked.length }})
        </button>
        <ul v-if="showRevoked" class="keys revoked-list">
          <li v-for="item in revoked" :key="item.id" class="key-row is-revoked">
            <div class="key-main">
              <div class="key-title"><span class="name">{{ item.name }}</span><span class="app">{{ item.appLabel || appLabel(item.app) }}</span></div>
              <div class="meta small muted">
                <span class="mono">{{ item.prefix }}…</span>
                <span>отозван {{ formatDateTime(item.revokedAt) }}<template v-if="item.revokedBy"> · {{ item.revokedBy }}</template></span>
              </div>
            </div>
          </li>
        </ul>
      </div>
    </div>

    <div class="card block" data-testid="keys-journal">
      <h2 class="section-title">Журнал</h2>
      <p class="muted small lead">Выпуск, перевыпуск, права и отзыв ключей — из реестра изменений (раздел «Интеграции»). Сами ключи туда не попадают.</p>
      <p v-if="!journal.length" class="muted small">Записей пока нет.</p>
      <ul v-else class="journal">
        <li v-for="row in journal" :key="row.id">
          <span class="when small muted">{{ formatDateTime(row.createdAt) }}</span>
          <span class="who small">{{ row.userName || row.userId }}</span>
          <span class="what"><b>{{ row.entity }}</b> {{ journalText(row) }}</span>
        </li>
      </ul>
      <router-link class="small more" to="/admin/audit">Весь реестр изменений →</router-link>
    </div>

    <BaseModal v-if="editor" :title="editor.mode === 'issue' ? 'Новый ключ' : `Права ключа «${editor.name}»`" @close="editor = null">
      <form class="form" @submit.prevent="save">
        <div v-if="editor.mode === 'issue'" class="field">
          <span class="field-label">Система</span>
          <SegmentedControl :model-value="editor.app" :options="appOptions" block fit="content" aria-label="Система" data-testid="key-app" @update:model-value="chooseApp" />
        </div>
        <label class="field">
          <span class="field-label">Название</span>
          <input v-model="editor.name" class="input" maxlength="100" placeholder="Например, «Дашборды компании»" data-testid="key-name" />
        </label>
        <fieldset class="field scopes-field">
          <legend class="field-label">Права</legend>
          <label v-for="s in SCOPES" :key="s.value" class="scope-option" :class="{ on: editor.scopes.includes(s.value) }">
            <input type="checkbox" :checked="editor.scopes.includes(s.value)" :data-testid="`key-scope-${s.value}`" @change="editor.scopes = toggleScope(editor.scopes, s.value)" />
            <span><b>{{ s.label }}</b> <code>{{ s.value }}</code><small>{{ s.hint }}</small></span>
          </label>
        </fieldset>
        <button type="submit" class="sr-only" tabindex="-1">ok</button>
      </form>
      <template #footer>
        <button class="btn btn-ghost" type="button" @click="editor = null">Отмена</button>
        <button class="btn btn-primary" type="button" :disabled="!canSave || busy" data-testid="key-save" @click="save">
          {{ editor.mode === 'issue' ? 'Выпустить' : 'Сохранить' }}
        </button>
      </template>
    </BaseModal>

    <BaseModal v-if="shown" :title="shown.reissued ? 'Новый ключ выпущен' : 'Ключ выпущен'" @close="shown = null">
      <p class="lead">Ключ «{{ shown.item.name }}» для {{ shown.item.appLabel || appLabel(shown.item.app) }} — с правами:
        {{ shown.item.scopes.map(scopeLabel).join(', ').toLowerCase() }}.</p>
      <div class="issued" data-testid="key-issued">
        <code class="secret mono">{{ shown.key }}</code>
        <button class="btn btn-sm" type="button" data-testid="key-copy" @click="copyKey"><AppIcon :name="copied ? 'check' : 'copy'" :size="13" /> {{ copied ? 'Скопирован' : 'Скопировать' }}</button>
      </div>
      <p class="warn small"><AppIcon name="warning" :size="13" /> Ключ показывается один раз — скопируйте его сейчас и введите в настройках системы.
        <template v-if="shown.reissued">Прежний ключ уже не действует.</template></p>
      <template #footer>
        <button class="btn btn-primary" type="button" @click="shown = null">Готово</button>
      </template>
    </BaseModal>
  </div>
</template>

<style scoped>
.narrow { max-width: 920px; }
.block { padding: 18px 20px; margin-bottom: 16px; }
.head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.lead { margin: 4px 0 12px; line-height: 1.5; }
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.empty { padding: 18px; text-align: center; color: var(--color-text-muted); border: 1px dashed var(--color-border); border-radius: var(--radius-md); }
.keys { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.key-row { display: flex; align-items: center; gap: 16px; padding: 12px 14px; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface); }
.key-row.is-revoked { background: var(--color-surface-2); color: var(--color-text-muted); }
.key-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.key-title { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.name { font-weight: 700; font-size: 14px; }
.app { font-size: 11.5px; padding: 1px 8px; border-radius: 999px; background: #eef1f7; color: var(--color-text-secondary); font-weight: 600; }
.scopes { display: flex; gap: 6px; flex-wrap: wrap; }
.scope { font-size: 11.5px; font-weight: 600; padding: 2px 9px; border-radius: 6px; }
.scope-projects-read { background: #e9f2ff; color: #0055cc; }
.scope-projects-stats { background: #f3f0ff; color: #5e4db2; }
.meta { display: flex; gap: 12px; flex-wrap: wrap; }
.key-actions { display: flex; gap: 6px; flex-shrink: 0; }
.danger { color: var(--color-danger); }
.revoked { margin-top: 12px; }
.revoked-list { margin-top: 8px; }
.journal { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.journal li { display: grid; grid-template-columns: 130px 160px minmax(0, 1fr); gap: 10px; padding: 7px 0; border-bottom: 1px solid #f0f2f6; font-size: 13px; }
.journal li:last-child { border-bottom: 0; }
.more { display: inline-block; margin-top: 10px; color: var(--color-primary); text-decoration: none; }
.form { display: flex; flex-direction: column; gap: 14px; }
.scopes-field { border: 0; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; }
.scope-option { display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; border: 1px solid var(--color-border); border-radius: var(--radius-md); cursor: pointer; }
.scope-option.on { border-color: #b9cbff; background: #f5f8ff; }
.scope-option input { margin-top: 3px; }
.scope-option small { display: block; color: var(--color-text-muted); font-size: 12px; margin-top: 2px; line-height: 1.45; }
.scope-option code { color: var(--color-text-muted); margin-left: 4px; }
.issued { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 10px 12px; border-radius: var(--radius-md); background: #f3f6ff; border: 1px dashed #9db5ff; }
.secret { font-size: 12.5px; padding: 4px 8px; background: #fff; border-radius: 6px; border: 1px solid var(--color-border); overflow-wrap: anywhere; user-select: all; }
.warn { color: #8a5a00; display: flex; align-items: flex-start; gap: 6px; margin: 10px 0 0; line-height: 1.45; }
@media (max-width: 768px) {
  .head, .key-row { flex-direction: column; align-items: stretch; }
  .key-actions { flex-wrap: wrap; }
  .journal li { grid-template-columns: 1fr; gap: 2px; }
}
</style>
