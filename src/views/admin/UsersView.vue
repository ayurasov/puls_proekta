<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import { ROLE_LABELS, useAuthStore } from '../../stores/auth'
import { useOrgStore } from '../../stores/org'
import { useRoute } from 'vue-router'
import { useUiStore } from '../../stores/ui'
import { useDialogs } from '../../composables/useDialogs'
import { resizeImage } from '../../utils/images'
import { countLabel, formatDateTime } from '../../utils/format'
import { CAPABILITY_LABELS, canManageAccount, capabilityGroups } from '../../utils/capabilities'
import AppIcon from '../../components/common/AppIcon.vue'
import HelpLink from '../../components/common/HelpLink.vue'
import BaseModal from '../../components/common/BaseModal.vue'
import UserAvatar from '../../components/common/UserAvatar.vue'

const auth = useAuthStore()
const org = useOrgStore()
const route = useRoute()
// Фильтр по подразделению: ссылка со страницы «Структура» (?department=<id> или none)
const departmentFilter = ref(typeof route.query.department === 'string' ? route.query.department : '')
const ui = useUiStore()
const { confirm } = useDialogs()
const users = ref([])
const loading = ref(true)
const search = ref('')
const roleFilter = ref('')
const sort = ref({ key: 'name', dir: 1 })
const editor = ref(null)
const saving = ref(false)
const fileInput = ref(null)

const ROLE_HINTS = {
  admin: 'Всё, включая сотрудников, доступы, корзину и настройки',
  manager: 'Создаёт проекты и направления, полностью управляет своими проектами',
  editor: 'Работает в проектах по выданным доступам',
  viewer: 'Только просмотр, даже если доступ выдан на редактирование',
}

// Полномочия без роли администратора (ADR-0013): выдаёт только администратор; «Ведёт учётные записи» не меняет
// учётные записи администраторов и сотрудников с полномочиями, которых нет у него самого
const CAPABILITY_GROUPS = capabilityGroups()
function editable(user) {
  return canManageAccount(auth.user, user)
}
function lockedReason(user) {
  if (editable(user)) return ''
  return user.role === 'admin' ? 'Учётную запись администратора меняет только администратор'
    : 'У сотрудника есть полномочия, которых нет у вас: его учётную запись меняет администратор'
}
function capabilitiesTitle(user) {
  return (user.capabilities || []).map((c) => CAPABILITY_LABELS[c]).join(', ')
}
/** Логин, роль и способ входа: защищённая чужая запись или своя (не у администратора). */
function locked(user) {
  if (user.mode === 'create') return false
  return !editable(user) || (user.id === auth.user?.id && !auth.isAdmin)
}
function toggleCapability(key) {
  const list = editor.value.capabilities
  editor.value.capabilities = list.includes(key) ? list.filter((c) => c !== key) : [...list, key]
}

async function load() {
  loading.value = true
  try { users.value = await api.get('/users') } catch (err) { ui.error(err) } finally { loading.value = false }
}
onMounted(() => { load(); org.reload().catch(() => {}) })

const visible = computed(() => {
  const q = search.value.trim().toLowerCase()
  return users.value
    .filter((u) => !roleFilter.value || u.role === roleFilter.value || (roleFilter.value === 'inactive' && !u.isActive)
      || (roleFilter.value === 'capabilities' && u.role !== 'admin' && (u.capabilities || []).length))
    .filter((u) => !departmentFilter.value || (departmentFilter.value === 'none' ? !u.departmentId : u.departmentId === departmentFilter.value))
    .filter((u) => !q || [u.name, u.login, u.position, u.email].some((v) => (v || '').toLowerCase().includes(q)))
    .sort((a, b) => String(a[sort.value.key] ?? '').localeCompare(String(b[sort.value.key] ?? ''), 'ru') * sort.value.dir)
})
const counts = computed(() => ({
  total: users.value.length,
  inactive: users.value.filter((u) => !u.isActive).length,
}))

function toggleSort(key) {
  sort.value = sort.value.key === key ? { key, dir: -sort.value.dir } : { key, dir: 1 }
}

function openCreate() {
  editor.value = {
    mode: 'create', name: '', login: '', position: '', email: '', role: 'editor', authSource: 'local', password: '', isActive: true,
    capabilities: [],
  }
}
function openEdit(user) {
  editor.value = {
    mode: 'edit', id: user.id, ...user, password: '', departmentId: user.departmentId || '',
    managerChoice: user.reportsToNone ? 'none' : user.managerId ? String(user.managerId) : 'structure',
    capabilities: [...(user.capabilities || [])],
  }
  editor.value.original = {
    departmentId: editor.value.departmentId, managerChoice: editor.value.managerChoice, capabilities: [...editor.value.capabilities],
  }
}
const structureManager = computed(() => {
  const person = editor.value && org.personById[editor.value.id]
  return person && person.managerSource === 'structure' ? org.personById[person.directManagerId] : null
})
// Отключённый руководитель остаётся в списке, если он назначен сейчас: иначе список был бы пуст, а сохранение
// карточки — невозможно; заменить его можно любым активным сотрудником
const managerOptions = computed(() => org.people.filter((p) => p.id !== editor.value?.id
  && (p.isActive || String(p.id) === editor.value?.managerChoice)))

async function save() {
  const e = editor.value
  saving.value = true
  try {
    if (e.mode === 'create') {
      let created = await api.post('/users', {
        name: e.name, login: e.login, position: e.position, email: e.email, role: e.role, authSource: e.authSource,
        password: e.authSource === 'local' ? e.password : '',
      })
      if (auth.isAdmin && e.role !== 'admin' && e.capabilities.length) {
        created = await api.patch(`/users/${created.id}`, { capabilities: e.capabilities })
      }
      users.value.push({ ...created, projectsCreated: 0 })
      ui.success('Сотрудник добавлен', created.name)
    } else {
      // Чужую защищённую учётную запись — только поля структуры; свою (не администратору) — без роли и входа
      const own = e.id === auth.user?.id && !auth.isAdmin
      const payload = { position: e.position }
      if (editable(e)) {
        Object.assign(payload, { name: e.name, email: e.email })
        if (!own) Object.assign(payload, { login: e.login, role: e.role, authSource: e.authSource })
      }
      if (auth.isAdmin && [...e.capabilities].sort().join() !== [...e.original.capabilities].sort().join()) payload.capabilities = e.capabilities
      // Подразделение и руководитель — только если их изменили (руководитель мог быть отключён после назначения)
      if ((e.departmentId || null) !== (e.original.departmentId || null)) payload.departmentId = e.departmentId || ''
      if (e.managerChoice !== e.original.managerChoice) {
        payload.managerId = e.managerChoice === 'none' || e.managerChoice === 'structure' ? 0 : Number(e.managerChoice)
        payload.reportsToNone = e.managerChoice === 'none'
      }
      if (e.password && editable(e)) payload.password = e.password
      const updated = await api.patch(`/users/${e.id}`, payload)
      replace(updated)
      if (updated.id === auth.user?.id) auth.setUser(updated)
      org.reload().catch(() => {})
      ui.success('Сохранено')
    }
    editor.value = null
  } catch (err) {
    ui.error(err)
  } finally {
    saving.value = false
  }
}

function replace(updated) {
  const i = users.value.findIndex((u) => u.id === updated.id)
  if (i >= 0) users.value[i] = { ...users.value[i], ...updated }
}

// Роль в таблице меняется сразу; изменение можно отменить из уведомления. Выдача и снятие роли
// администратора — с подтверждением: это доступ ко всем проектам, сотрудникам и настройкам.
async function setRole(user, role, event = null, { undo = false } = {}) {
  const previous = user.role
  if (role === previous) return
  if (!undo && (role === 'admin' || previous === 'admin')) {
    const ok = await confirm({
      title: role === 'admin' ? `Сделать ${user.name} администратором?` : `Снять с ${user.name} роль администратора?`,
      message: role === 'admin'
        ? 'Администратор видит и меняет все проекты, сотрудников, доступы и настройки системы.'
        : `Сотрудник станет «${ROLE_LABELS[role]}» и будет видеть только проекты по выданным доступам.`,
      confirmText: role === 'admin' ? 'Сделать администратором' : 'Снять роль', danger: role !== 'admin',
    })
    if (!ok) { if (event?.target) event.target.value = previous; return }
  }
  try {
    replace(await api.patch(`/users/${user.id}`, { role }))
    if (undo) return
    ui.toast({
      title: `${user.name}: ${ROLE_LABELS[role]}`, body: `Было: ${ROLE_LABELS[previous]}`, kind: 'success', timeout: 8000,
      action: { label: 'Отменить', handler: () => setRole(users.value.find((u) => u.id === user.id) || user, previous, null, { undo: true }) },
    })
  } catch (err) { ui.error(err); load() }
}

async function setActive(user, active) {
  if (!active) {
    const ok = await confirm({
      title: `Отключить ${user.name}?`,
      message: 'Сотрудник сразу выйдет из системы на всех устройствах и не сможет войти. Данные, доступы и история сохранятся — включить можно в любой момент.',
      confirmText: 'Отключить',
    })
    if (!ok) return
  }
  try { replace(await api.patch(`/users/${user.id}`, { isActive: active })) } catch (err) { ui.error(err) }
}

async function remove(user) {
  const ok = await confirm({
    title: `Удалить ${user.name}?`,
    message: 'Удаление нельзя отменить: пропадут учётная запись и доступы сотрудника. Его комментарии и записи истории останутся с его именем. Обычно достаточно отключить сотрудника.',
    confirmText: 'Удалить навсегда',
  })
  if (!ok) return
  try {
    await api.delete(`/users/${user.id}`)
    users.value = users.value.filter((u) => u.id !== user.id)
    editor.value = null
  } catch (err) { ui.error(err) }
}

async function uploadPhoto(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || !editor.value) return
  try {
    const form = new FormData()
    form.append('file', await resizeImage(file, 512), 'avatar.jpg')
    const updated = await api.upload(`/users/${editor.value.id}/avatar`, form)
    replace(updated)
    editor.value.avatarUrl = updated.avatarUrl
  } catch (err) { ui.error(err) }
}
async function removePhoto() {
  try {
    const updated = await api.delete(`/users/${editor.value.id}/avatar`)
    replace(updated)
    editor.value.avatarUrl = null
  } catch (err) { ui.error(err) }
}
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Сотрудники</h1>
        <div class="page-subtitle">{{ countLabel(counts.total, 'сотрудник', 'сотрудника', 'сотрудников') }}<span v-if="counts.inactive"> · {{ countLabel(counts.inactive, 'отключён', 'отключены', 'отключено') }}</span></div>
      </div>
      <HelpLink topic="admin-users" />
      <div class="spacer" />
      <input v-model="search" class="input search" type="search" placeholder="Поиск по ФИО, логину, должности" />
      <select v-if="org.departments.length" v-model="departmentFilter" class="select dep-filter" aria-label="Подразделение">
        <option value="">Все подразделения</option>
        <option value="none">Без подразделения</option>
        <option v-for="o in org.options()" :key="o.id" :value="o.id">{{ o.label }}</option>
      </select>
      <select v-model="roleFilter" class="select role-filter">
        <option value="">Все роли</option>
        <option v-for="(label, key) in ROLE_LABELS" :key="key" :value="key">{{ label }}</option>
        <option value="capabilities">С полномочиями</option>
        <option value="inactive">Отключённые</option>
      </select>
      <button class="btn btn-primary" type="button" @click="openCreate"><AppIcon name="plus" /> Сотрудник</button>
    </div>

    <div class="card table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th class="sortable" @click="toggleSort('name')">Сотрудник</th>
            <th class="sortable" @click="toggleSort('position')">Должность и подразделение</th>
            <th class="sortable" @click="toggleSort('role')">Роль</th>
            <th>Вход</th>
            <th class="sortable" @click="toggleSort('lastLoginAt')">Последний вход</th>
            <th>Активен</th>
            <th />
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading"><td colspan="7" class="muted">Загрузка…</td></tr>
          <tr v-for="user in visible" :key="user.id" :class="{ inactive: !user.isActive }">
            <td>
              <div class="person">
                <UserAvatar :user="user" :size="30" />
                <div>
                  <div class="person-name">{{ user.name }}<span v-if="user.id === auth.user?.id" class="badge badge-primary">вы</span></div>
                  <div class="muted small">@{{ user.login }}<span v-if="user.email"> · {{ user.email }}</span></div>
                </div>
              </div>
            </td>
            <td class="small">
              <div>{{ user.position || '—' }}</div>
              <div v-if="user.departmentId && org.depById[user.departmentId]" class="muted ellipsis dep" :title="org.pathText(user.departmentId)">{{ org.depById[user.departmentId].name }}</div>
            </td>
            <td>
              <select
                class="select input-sm" :value="user.role" :disabled="user.id === auth.user?.id || !editable(user)"
                :title="lockedReason(user) || ROLE_HINTS[user.role]" @change="setRole(user, $event.target.value, $event)"
              >
                <option v-for="(label, key) in ROLE_LABELS" :key="key" :value="key" :disabled="key === 'admin' && !auth.isAdmin">{{ label }}</option>
              </select>
              <div v-if="user.role !== 'admin' && user.capabilities?.length" class="cap-chip" :title="capabilitiesTitle(user)" data-testid="user-capabilities">
                <AppIcon name="key" :size="11" /> {{ countLabel(user.capabilities.length, 'полномочие', 'полномочия', 'полномочий') }}
              </div>
            </td>
            <td class="small">{{ user.authSource === 'ldap' ? 'домен' : 'пароль' }}</td>
            <td class="small muted">{{ user.lastLoginAt ? formatDateTime(user.lastLoginAt) : 'не входил' }}</td>
            <td>
              <label class="switch" :title="user.id === auth.user?.id ? 'Свою учётную запись отключить нельзя' : lockedReason(user)">
                <input type="checkbox" :checked="user.isActive" :disabled="user.id === auth.user?.id || !editable(user)" @change="setActive(user, $event.target.checked)" /><span class="switch-track" />
              </label>
            </td>
            <td class="actions">
              <button class="btn btn-ghost btn-icon btn-sm" type="button" title="Изменить" @click="openEdit(user)"><AppIcon name="edit" :size="15" /></button>
            </td>
          </tr>
          <tr v-if="!loading && !visible.length"><td colspan="7" class="muted">Никого не найдено</td></tr>
        </tbody>
      </table>
    </div>

    <BaseModal v-if="editor" :title="editor.mode === 'create' ? 'Новый сотрудник' : editor.name" @close="editor = null">
      <form class="form-stack" @submit.prevent="save">
        <p v-if="editor.mode === 'edit' && !editable(editor)" class="locked-note" data-testid="user-locked">
          <AppIcon name="lock" :size="14" /> {{ lockedReason(editor) }}. Вы можете изменить должность, подразделение и руководителя.
        </p>
        <div v-if="editor.mode === 'edit'" class="photo-row">
          <UserAvatar :user="editor" :size="64" />
          <button class="btn btn-sm" type="button" :disabled="!editable(editor)" @click="fileInput.click()"><AppIcon name="upload" :size="14" /> Загрузить фото</button>
          <button v-if="editor.avatarUrl && editable(editor)" class="btn btn-sm btn-ghost btn-text-danger" type="button" @click="removePhoto">Удалить фото</button>
          <input ref="fileInput" type="file" accept="image/*" hidden @change="uploadPhoto" />
        </div>
        <label class="field"><span class="field-label">ФИО</span><input v-model="editor.name" class="input" maxlength="255" required :disabled="editor.mode === 'edit' && !editable(editor)" /></label>
        <label class="field"><span class="field-label">Должность</span><input v-model="editor.position" class="input" maxlength="500" /></label>
        <div v-if="editor.mode === 'edit' && org.departments.length" class="form-grid">
          <label class="field">
            <span class="field-label">Подразделение</span>
            <select v-model="editor.departmentId" class="select" data-testid="user-department">
              <option value="">— не указано —</option>
              <option v-for="o in org.options()" :key="o.id" :value="o.id">{{ o.label }}</option>
            </select>
          </label>
          <label class="field">
            <span class="field-label">Руководитель</span>
            <select v-model="editor.managerChoice" class="select">
              <option value="structure">По структуре{{ structureManager ? `: ${structureManager.name}` : '' }}</option>
              <option value="none">Первое лицо — никому не подчиняется</option>
              <option v-for="p in managerOptions" :key="p.id" :value="String(p.id)">{{ p.name }}{{ p.isActive ? '' : ' (отключён)' }}</option>
            </select>
          </label>
        </div>
        <div class="form-grid">
          <label class="field"><span class="field-label">Логин</span><input v-model="editor.login" class="input" maxlength="150" autocomplete="off" required :disabled="locked(editor)" /></label>
          <label class="field"><span class="field-label">Email</span><input v-model="editor.email" class="input" type="email" maxlength="255" :disabled="editor.mode === 'edit' && !editable(editor)" /></label>
        </div>
        <div class="form-grid">
          <label class="field">
            <span class="field-label">Роль</span>
            <select v-model="editor.role" class="select" :disabled="editor.id === auth.user?.id || locked(editor)">
              <option v-for="(label, key) in ROLE_LABELS" :key="key" :value="key" :disabled="key === 'admin' && !auth.isAdmin">{{ label }}</option>
            </select>
            <span class="field-hint">{{ ROLE_HINTS[editor.role] }}</span>
          </label>
          <label class="field">
            <span class="field-label">Способ входа</span>
            <select v-model="editor.authSource" class="select" :disabled="locked(editor)">
              <option value="local">Пароль системы</option>
              <option value="ldap">Учётная запись домена (LDAP)</option>
            </select>
          </label>
        </div>
        <label v-if="editor.authSource === 'local' && (editor.mode === 'create' || editable(editor))" class="field">
          <span class="field-label">{{ editor.mode === 'create' ? 'Пароль' : 'Новый пароль' }}</span>
          <input v-model="editor.password" class="input" type="password" autocomplete="new-password" :placeholder="editor.mode === 'edit' ? 'Оставьте пустым, чтобы не менять' : 'Не короче 8 символов'" />
          <span v-if="editor.mode === 'edit'" class="field-hint">После смены пароля сотрудник выйдет из системы на всех устройствах</span>
        </label>
        <fieldset v-if="auth.isAdmin && editor.role !== 'admin'" class="caps" data-testid="user-capability-list">
          <legend class="field-label">Полномочия без роли администратора <HelpLink topic="admin-capabilities" /></legend>
          <p class="field-hint caps-hint">Отдельные разделы администрирования — без доступа ко всему остальному. Сотрудник увидит
            в меню только свои разделы. Роль «Администратор», полномочия, ключи доступа и вход через домен — только у администратора.</p>
          <div v-for="group in CAPABILITY_GROUPS" :key="group.name" class="caps-group">
            <div class="caps-group-name">{{ group.name }}</div>
            <label v-for="cap in group.items" :key="cap.key" class="cap-row" :class="{ 'is-on': editor.capabilities.includes(cap.key) }">
              <input type="checkbox" :checked="editor.capabilities.includes(cap.key)" :data-cap="cap.key" @change="toggleCapability(cap.key)" />
              <span class="cap-text"><span class="cap-label">{{ cap.label }}</span><span class="cap-hint">{{ cap.hint }}</span></span>
            </label>
          </div>
        </fieldset>
        <p v-else-if="auth.isAdmin && editor.role === 'admin'" class="field-hint">У администратора есть все полномочия.</p>
        <button type="submit" class="sr-only" tabindex="-1">ok</button>
      </form>
      <template #footer>
        <button v-if="editor.mode === 'edit' && editor.id !== auth.user?.id && editable(editor)" class="btn btn-ghost btn-text-danger" type="button" style="margin-right:auto" @click="remove(editor)">Удалить</button>
        <button class="btn btn-ghost" type="button" @click="editor = null">Отмена</button>
        <button class="btn btn-primary" type="button" :disabled="saving || !editor.name.trim() || !editor.login.trim() || (editor.mode === 'create' && editor.authSource === 'local' && editor.password.length < 8)" @click="save">
          {{ editor.mode === 'create' ? 'Добавить' : 'Сохранить' }}
        </button>
      </template>
    </BaseModal>
  </div>
</template>

<style scoped>
.search { width: 280px; }
.role-filter { width: 170px; }
.dep-filter { width: 220px; }
.dep { max-width: 260px; }
.person { display: flex; align-items: center; gap: 10px; }
.person-name { font-weight: 600; display: flex; align-items: center; gap: 6px; }
tr.inactive td { opacity: 0.55; }
.actions { text-align: right; width: 50px; }
.photo-row { display: flex; align-items: center; gap: 10px; }
.table .select { width: 150px; }
.cap-chip { display: inline-flex; align-items: center; gap: 4px; margin-top: 4px; padding: 1px 8px; border-radius: 999px; background: #efe9fe; color: #6d3fd8; font-size: 11px; font-weight: 600; cursor: default; }
.locked-note { display: flex; align-items: flex-start; gap: 8px; margin: 0; padding: 9px 12px; border-radius: 10px; background: var(--color-warning-soft); color: #8a5a10; font-size: 12.5px; line-height: 1.45; }
.caps { margin: 0; padding: 12px 14px; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-surface-2); display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.caps legend { padding: 0 4px; }
.caps-hint { margin: 0; line-height: 1.45; }
.caps-group { display: flex; flex-direction: column; gap: 2px; }
.caps-group-name { font-size: 11px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; color: var(--color-text-muted); margin: 4px 0 2px; }
.cap-row { display: flex; align-items: flex-start; gap: 10px; padding: 7px 9px; border-radius: 9px; cursor: pointer; transition: background 0.15s; }
.cap-row:hover { background: var(--color-surface); }
.cap-row.is-on { background: var(--color-primary-soft); }
.cap-row input { margin-top: 2px; flex-shrink: 0; }
.cap-text { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.cap-label { font-size: 13px; font-weight: 600; color: var(--color-text); }
.cap-hint { font-size: 11.5px; color: var(--color-text-muted); line-height: 1.4; }
@media (max-width: 768px) {
  .search { width: auto; flex: 1 1 100%; }
  .role-filter { width: auto; flex: 1 1 140px; }
}
</style>
