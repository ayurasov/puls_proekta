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

async function load() {
  loading.value = true
  try { users.value = await api.get('/users') } catch (err) { ui.error(err) } finally { loading.value = false }
}
onMounted(() => { load(); org.load().catch(() => {}) })

const visible = computed(() => {
  const q = search.value.trim().toLowerCase()
  return users.value
    .filter((u) => !roleFilter.value || u.role === roleFilter.value || (roleFilter.value === 'inactive' && !u.isActive))
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
  editor.value = { mode: 'create', name: '', login: '', position: '', email: '', role: 'editor', authSource: 'local', password: '', isActive: true }
}
function openEdit(user) {
  editor.value = {
    mode: 'edit', id: user.id, ...user, password: '', departmentId: user.departmentId || '',
    managerChoice: user.reportsToNone ? 'none' : user.managerId ? String(user.managerId) : 'structure',
  }
}
const structureManager = computed(() => {
  const person = editor.value && org.personById[editor.value.id]
  return person && person.managerSource === 'structure' ? org.personById[person.directManagerId] : null
})
const managerOptions = computed(() => org.people.filter((p) => p.isActive && p.id !== editor.value?.id))

async function save() {
  const e = editor.value
  saving.value = true
  try {
    if (e.mode === 'create') {
      const created = await api.post('/users', {
        name: e.name, login: e.login, position: e.position, email: e.email, role: e.role, authSource: e.authSource,
        password: e.authSource === 'local' ? e.password : '',
      })
      users.value.push({ ...created, projectsCreated: 0 })
      ui.success('Сотрудник добавлен', created.name)
    } else {
      const payload = {
        name: e.name, login: e.login, position: e.position, email: e.email, role: e.role, authSource: e.authSource,
        departmentId: e.departmentId || '', managerId: e.managerChoice === 'none' || e.managerChoice === 'structure' ? 0 : Number(e.managerChoice),
        reportsToNone: e.managerChoice === 'none',
      }
      if (e.password) payload.password = e.password
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
              <select class="select input-sm" :value="user.role" :disabled="user.id === auth.user?.id" :title="ROLE_HINTS[user.role]" @change="setRole(user, $event.target.value, $event)">
                <option v-for="(label, key) in ROLE_LABELS" :key="key" :value="key">{{ label }}</option>
              </select>
            </td>
            <td class="small">{{ user.authSource === 'ldap' ? 'домен' : 'пароль' }}</td>
            <td class="small muted">{{ user.lastLoginAt ? formatDateTime(user.lastLoginAt) : 'не входил' }}</td>
            <td>
              <label class="switch" :title="user.id === auth.user?.id ? 'Свою учётную запись отключить нельзя' : ''">
                <input type="checkbox" :checked="user.isActive" :disabled="user.id === auth.user?.id" @change="setActive(user, $event.target.checked)" /><span class="switch-track" />
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
        <div v-if="editor.mode === 'edit'" class="photo-row">
          <UserAvatar :user="editor" :size="64" />
          <button class="btn btn-sm" type="button" @click="fileInput.click()"><AppIcon name="upload" :size="14" /> Загрузить фото</button>
          <button v-if="editor.avatarUrl" class="btn btn-sm btn-ghost btn-text-danger" type="button" @click="removePhoto">Удалить фото</button>
          <input ref="fileInput" type="file" accept="image/*" hidden @change="uploadPhoto" />
        </div>
        <label class="field"><span class="field-label">ФИО</span><input v-model="editor.name" class="input" maxlength="255" required /></label>
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
              <option v-for="p in managerOptions" :key="p.id" :value="String(p.id)">{{ p.name }}</option>
            </select>
          </label>
        </div>
        <div class="form-grid">
          <label class="field"><span class="field-label">Логин</span><input v-model="editor.login" class="input" maxlength="150" autocomplete="off" required /></label>
          <label class="field"><span class="field-label">Email</span><input v-model="editor.email" class="input" type="email" maxlength="255" /></label>
        </div>
        <div class="form-grid">
          <label class="field">
            <span class="field-label">Роль</span>
            <select v-model="editor.role" class="select" :disabled="editor.id === auth.user?.id">
              <option v-for="(label, key) in ROLE_LABELS" :key="key" :value="key">{{ label }}</option>
            </select>
            <span class="field-hint">{{ ROLE_HINTS[editor.role] }}</span>
          </label>
          <label class="field">
            <span class="field-label">Способ входа</span>
            <select v-model="editor.authSource" class="select">
              <option value="local">Пароль системы</option>
              <option value="ldap">Учётная запись домена (LDAP)</option>
            </select>
          </label>
        </div>
        <label v-if="editor.authSource === 'local'" class="field">
          <span class="field-label">{{ editor.mode === 'create' ? 'Пароль' : 'Новый пароль' }}</span>
          <input v-model="editor.password" class="input" type="password" autocomplete="new-password" :placeholder="editor.mode === 'edit' ? 'Оставьте пустым, чтобы не менять' : 'Не короче 8 символов'" />
          <span v-if="editor.mode === 'edit'" class="field-hint">После смены пароля сотрудник выйдет из системы на всех устройствах</span>
        </label>
        <button type="submit" class="sr-only" tabindex="-1">ok</button>
      </form>
      <template #footer>
        <button v-if="editor.mode === 'edit' && editor.id !== auth.user?.id" class="btn btn-ghost btn-text-danger" type="button" style="margin-right:auto" @click="remove(editor)">Удалить</button>
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
@media (max-width: 768px) {
  .search { width: auto; flex: 1 1 100%; }
  .role-filter { width: auto; flex: 1 1 140px; }
}
</style>
