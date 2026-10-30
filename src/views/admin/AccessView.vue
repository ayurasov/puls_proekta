<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import { ACCESS_LABELS, useProjectsStore } from '../../stores/projects'
import { ROLE_LABELS } from '../../stores/auth'
import { useUiStore } from '../../stores/ui'
import { useDialogs } from '../../composables/useDialogs'
import AppIcon from '../../components/common/AppIcon.vue'

const ui = useUiStore()
const projects = useProjectsStore()
const { confirm } = useDialogs()
const tab = ref('grant')
const users = ref([])
const rules = ref([])
const matrix = ref(null)
const userSearch = ref('')
const projectSearch = ref('')
const selectedUsers = ref(new Set())
const selectedProjects = ref(new Set())
const selectedDirections = ref(new Set())
const level = ref('view')
const mode = ref('replace')
const ruleSearch = ref('')
const selectedRules = ref(new Set())
const busy = ref(false)

async function load() {
  try {
    const [u, r] = await Promise.all([api.get('/users'), api.get('/access')])
    users.value = u.filter((x) => x.isActive)
    rules.value = r
    if (!projects.loaded) await projects.load()
  } catch (err) { ui.error(err) }
}
onMounted(load)

const candidates = computed(() => {
  const q = userSearch.value.trim().toLowerCase()
  return users.value.filter((u) => u.role !== 'admin')
    .filter((u) => !q || u.name.toLowerCase().includes(q) || u.login.toLowerCase().includes(q) || (u.position || '').toLowerCase().includes(q))
})
const groupedProjects = computed(() => {
  const q = projectSearch.value.trim().toLowerCase()
  return projects.grouped.map((g) => ({ ...g, projects: g.projects.filter((p) => !q || p.title.toLowerCase().includes(q)) }))
    .filter((g) => g.projects.length || (g.direction && !q))
})
const grantCount = computed(() => selectedUsers.value.size * (selectedProjects.value.size + selectedDirections.value.size))

function toggle(setRef, id) {
  const next = new Set(setRef.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  setRef.value = next
}
function toggleAllUsers() {
  selectedUsers.value = selectedUsers.value.size === candidates.value.length ? new Set() : new Set(candidates.value.map((u) => u.id))
}

async function grant() {
  busy.value = true
  try {
    const res = await api.post('/access', {
      userIds: [...selectedUsers.value], projectIds: [...selectedProjects.value], directionIds: [...selectedDirections.value],
      level: level.value, mode: mode.value,
    })
    ui.success('Доступы выданы', `Новых правил: ${res.created}, изменено: ${res.updated}, без изменений: ${res.unchanged}`)
    selectedUsers.value = new Set()
    selectedProjects.value = new Set()
    selectedDirections.value = new Set()
    rules.value = await api.get('/access')
  } catch (err) { ui.error(err) } finally { busy.value = false }
}

const visibleRules = computed(() => {
  const q = ruleSearch.value.trim().toLowerCase()
  return rules.value.filter((r) => !q || r.userName.toLowerCase().includes(q) || r.userLogin.toLowerCase().includes(q) || r.targetName.toLowerCase().includes(q))
    .sort((a, b) => a.userName.localeCompare(b.userName, 'ru') || a.targetName.localeCompare(b.targetName, 'ru'))
})

async function changeLevel(rule, value) {
  try {
    const updated = await api.patch(`/access/${rule.id}`, { level: value })
    Object.assign(rule, updated)
  } catch (err) { ui.error(err) }
}
async function removeRules(ids) {
  const ok = await confirm({ title: ids.length === 1 ? 'Отозвать доступ?' : `Отозвать доступы (${ids.length})?`, message: 'Сотрудники перестанут видеть эти проекты.', confirmText: 'Отозвать' })
  if (!ok) return
  try {
    await api.post('/access/delete', { ids })
    rules.value = rules.value.filter((r) => !ids.includes(r.id))
    selectedRules.value = new Set()
  } catch (err) { ui.error(err) }
}

async function loadMatrix() {
  tab.value = 'matrix'
  try { matrix.value = await api.get('/access/effective') } catch (err) { ui.error(err) }
}
const LEVEL_SHORT = { view: 'П', edit: 'Р', manage: 'У' }
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Доступы</h1>
        <div class="page-subtitle">Кто какие проекты видит и может менять. Администраторы видят всё; наблюдатель — только просмотр.</div>
      </div>
      <div class="spacer" />
      <div class="segmented">
        <button type="button" :class="{ 'is-active': tab === 'grant' }" @click="tab = 'grant'">Выдать доступ</button>
        <button type="button" :class="{ 'is-active': tab === 'rules' }" @click="tab = 'rules'">Правила · {{ rules.length }}</button>
        <button type="button" :class="{ 'is-active': tab === 'matrix' }" @click="loadMatrix">Кто что видит</button>
      </div>
    </div>

    <div v-if="tab === 'grant'" class="grant">
      <section class="card pane">
        <header><h2 class="section-title">Сотрудники · {{ selectedUsers.size }}</h2><button class="btn btn-ghost btn-sm" type="button" @click="toggleAllUsers">{{ selectedUsers.size === candidates.length && candidates.length ? 'Снять все' : 'Выбрать все' }}</button></header>
        <input v-model="userSearch" class="input input-sm" placeholder="Поиск" />
        <div class="options scroll-thin">
          <label v-for="u in candidates" :key="u.id" class="option">
            <input type="checkbox" :checked="selectedUsers.has(u.id)" @change="toggle(selectedUsers, u.id)" />
            <span class="ellipsis">{{ u.name }}</span><span class="muted small">{{ ROLE_LABELS[u.role] }}</span>
          </label>
        </div>
      </section>
      <section class="card pane">
        <header><h2 class="section-title">Проекты · {{ selectedProjects.size + selectedDirections.size }}</h2></header>
        <input v-model="projectSearch" class="input input-sm" placeholder="Поиск" />
        <div class="options scroll-thin">
          <template v-for="g in groupedProjects" :key="g.direction ? g.direction.id : 'none'">
            <label v-if="g.direction" class="option group">
              <input type="checkbox" :checked="selectedDirections.has(g.direction.id)" @change="toggle(selectedDirections, g.direction.id)" />
              <span class="ellipsis"><b>{{ g.direction.name }}</b> — всё направление, включая будущие проекты</span>
            </label>
            <div v-else class="group-title muted small">Без направления</div>
            <label v-for="p in g.projects" :key="p.id" class="option nested" :class="{ dim: g.direction && selectedDirections.has(g.direction.id) }">
              <input type="checkbox" :checked="selectedProjects.has(p.id)" @change="toggle(selectedProjects, p.id)" />
              <span class="ellipsis">{{ p.title }}</span>
            </label>
          </template>
        </div>
      </section>
      <section class="card pane controls">
        <label class="field">
          <span class="field-label">Уровень доступа</span>
          <select v-model="level" class="select">
            <option v-for="(label, key) in ACCESS_LABELS" :key="key" :value="key">{{ label }}</option>
          </select>
          <span class="field-hint">{{ { view: 'Смотреть план, фильтровать, выгружать', edit: 'Менять план: задачи, сроки, версии, комментарии', manage: '+ переименовывать проект, удалять архивные планы, модерировать комментарии' }[level] }}</span>
        </label>
        <label class="field">
          <span class="field-label">Если доступ уже есть</span>
          <select v-model="mode" class="select">
            <option value="replace">Заменить уровень</option>
            <option value="raise">Только повысить</option>
          </select>
        </label>
        <button class="btn btn-primary" type="button" :disabled="!grantCount || busy" @click="grant">
          Выдать доступ ({{ selectedUsers.size }} × {{ selectedProjects.size + selectedDirections.size }})
        </button>
        <p class="muted small">Повторная выдача не создаёт дублей (в 1.0 одинаковые правила копились).</p>
      </section>
    </div>

    <div v-else-if="tab === 'rules'" class="card">
      <div class="rules-bar">
        <input v-model="ruleSearch" class="input input-sm" placeholder="Сотрудник или проект" style="max-width: 320px" />
        <span class="spacer" />
        <button v-if="selectedRules.size" class="btn btn-sm btn-text-danger" type="button" @click="removeRules([...selectedRules])"><AppIcon name="trash" :size="14" /> Отозвать выбранные ({{ selectedRules.size }})</button>
      </div>
      <div class="table-wrap" style="max-height: 65vh">
        <table class="table">
          <thead><tr><th style="width:32px" /><th>Сотрудник</th><th>Проект / направление</th><th>Уровень</th><th /></tr></thead>
          <tbody>
            <tr v-for="r in visibleRules" :key="r.id">
              <td><input type="checkbox" :checked="selectedRules.has(r.id)" @change="toggle(selectedRules, r.id)" /></td>
              <td><div>{{ r.userName }}</div><div class="muted small">@{{ r.userLogin }} · {{ ROLE_LABELS[r.userRole] }}</div></td>
              <td><span class="badge">{{ r.projectId ? 'Проект' : 'Направление' }}</span> {{ r.targetName }}</td>
              <td>
                <select class="select input-sm" :value="r.level" style="width: 190px" @change="changeLevel(r, $event.target.value)">
                  <option v-for="(label, key) in ACCESS_LABELS" :key="key" :value="key">{{ label }}</option>
                </select>
                <div v-if="r.effectiveLevel !== r.level" class="muted small">действует: {{ ACCESS_LABELS[r.effectiveLevel] }} (наблюдатель)</div>
              </td>
              <td class="actions"><button class="btn btn-ghost btn-icon btn-sm btn-text-danger" type="button" title="Отозвать" @click="removeRules([r.id])"><AppIcon name="trash" :size="14" /></button></td>
            </tr>
            <tr v-if="!visibleRules.length"><td colspan="5" class="muted">Правил нет</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-else class="card">
      <div v-if="!matrix" class="muted" style="padding:16px">Загрузка…</div>
      <div v-else class="table-wrap matrix scroll-thin">
        <table class="table">
          <thead><tr><th class="sticky">Сотрудник</th><th v-for="p in matrix.projects" :key="p.id" class="pcol" :title="p.title"><span>{{ p.title }}</span></th></tr></thead>
          <tbody>
            <tr v-for="u in matrix.users" :key="u.id">
              <td class="sticky"><div class="nowrap">{{ u.name }}</div><div class="muted small">{{ ROLE_LABELS[u.role] }}</div></td>
              <td v-for="p in matrix.projects" :key="p.id" class="cell" :class="matrix.matrix[u.id]?.[p.id]" :title="ACCESS_LABELS[matrix.matrix[u.id]?.[p.id]] || 'нет доступа'">
                {{ LEVEL_SHORT[matrix.matrix[u.id]?.[p.id]] || '' }}
              </td>
            </tr>
          </tbody>
        </table>
        <p class="muted small legend">П — просмотр, Р — редактирование, У — полное управление (с учётом роли, направлений и авторства проекта)</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.grant { display: grid; grid-template-columns: 1fr 1.3fr 0.9fr; gap: 12px; align-items: start; }
.pane { padding: 12px; display: flex; flex-direction: column; gap: 8px; }
.pane header { display: flex; align-items: center; justify-content: space-between; }
.options { max-height: 58vh; overflow: auto; display: flex; flex-direction: column; }
.option { display: flex; align-items: center; gap: 8px; padding: 5px 6px; border-radius: 6px; font-size: 13px; cursor: pointer; }
.option:hover { background: var(--color-hover); }
.option .ellipsis { flex: 1; }
.option.group { margin-top: 6px; }
.option.nested { padding-left: 22px; }
.option.dim { opacity: 0.5; }
.group-title { padding: 8px 6px 2px; text-transform: uppercase; letter-spacing: 0.04em; }
.controls { gap: 14px; }
.rules-bar { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--color-border); }
.actions { text-align: right; }
.matrix { max-height: 70vh; }
.matrix .sticky { position: sticky; left: 0; background: #fff; z-index: 2; }
.matrix thead .sticky { z-index: 3; background: var(--color-surface-2); }
.pcol { height: 150px; vertical-align: bottom; min-width: 34px; max-width: 34px; }
.pcol span { display: inline-block; writing-mode: vertical-rl; transform: rotate(180deg); white-space: nowrap; max-height: 140px; overflow: hidden; text-overflow: ellipsis; }
.cell { text-align: center; font-weight: 700; font-size: 12px; padding: 6px 4px; }
.cell.view { background: #eef3ff; color: #2b4db5; }
.cell.edit { background: #e3f6ec; color: #1f7a4c; }
.cell.manage { background: #fdf1dd; color: #a8670f; }
.legend { padding: 8px 12px; }
@media (max-width: 1100px) { .grant { grid-template-columns: 1fr; } }
</style>
