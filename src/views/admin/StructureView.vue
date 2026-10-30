<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../../api/client'
import { useOrgStore } from '../../stores/org'
import { useUiStore } from '../../stores/ui'
import { useDialogs } from '../../composables/useDialogs'
import HelpLink from '../../components/common/HelpLink.vue'
import AppIcon from '../../components/common/AppIcon.vue'
import BaseModal from '../../components/common/BaseModal.vue'
import UserAvatar from '../../components/common/UserAvatar.vue'

/** Администрирование структуры компании: подразделения, их тип, порядок, руководители; типы и глубина. */
const org = useOrgStore()
const ui = useUiStore()
const { confirm } = useDialogs()
const collapsed = ref(new Set())
const search = ref('')
const draft = reactive({ name: '', parentId: '', kind: 'department' })
const edit = ref(null)
const heads = ref(null)
const settingsDraft = ref(null)
const busy = ref(false)

onMounted(async () => {
  try { await org.reload() } catch (err) { ui.error(err) }
})

const rows = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (q) {
    // Поиск: подходящие подразделения вместе с цепочкой вышестоящих
    const keep = new Set()
    for (const dep of org.departments) {
      const heads = dep.headIds.map((id) => org.personById[id]?.name || '').join(' ')
      if (`${dep.name} ${heads}`.toLowerCase().includes(q)) {
        let current = dep
        while (current && !keep.has(current.id)) { keep.add(current.id); current = org.depById[current.parentId] }
      }
    }
    return org.departments.filter((d) => keep.has(d.id))
  }
  const hidden = new Set()
  return org.departments.filter((dep) => {
    if (dep.parentId && (hidden.has(dep.parentId) || collapsed.value.has(dep.parentId))) { hidden.add(dep.id); return false }
    return true
  })
})

function toggle(id) {
  const next = new Set(collapsed.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  collapsed.value = next
}
function collapseAll(collapse) {
  collapsed.value = collapse ? new Set(org.departments.filter((d) => d.childCount).map((d) => d.id)) : new Set()
}

async function create() {
  if (!draft.name.trim()) return
  busy.value = true
  try {
    await api.post('/departments', { name: draft.name, parentId: draft.parentId || null, kind: draft.kind })
    draft.name = ''
    await org.reload()
  } catch (err) { ui.error(err) } finally { busy.value = false }
}
function addChild(dep) {
  draft.parentId = dep.id
  draft.kind = 'department'
  collapsed.value.delete(dep.id)
  document.querySelector('[data-testid=dep-name]')?.focus()
}

function openEdit(dep) {
  edit.value = { id: dep.id, name: dep.name, parentId: dep.parentId || '', kind: dep.kind, sortOrder: dep.sortOrder }
}
async function saveEdit() {
  const e = edit.value
  busy.value = true
  try {
    await api.patch(`/departments/${e.id}`, { name: e.name, parentId: e.parentId || null, kind: e.kind, sortOrder: Number(e.sortOrder) || 0 })
    edit.value = null
    await org.reload()
  } catch (err) { ui.error(err) } finally { busy.value = false }
}
async function remove(dep) {
  const ok = await confirm({
    title: `Удалить «${dep.name}»?`,
    message: dep.employeeCount
      ? `Сотрудники подразделения (${dep.employeeCount}) останутся в системе без подразделения, руководство подразделением будет снято.`
      : 'Подразделение будет удалено.',
    confirmText: 'Удалить',
  })
  if (!ok) return
  try { await api.delete(`/departments/${dep.id}`); await org.reload() } catch (err) { ui.error(err) }
}

// ---------- руководители ----------
function openHeads(dep) {
  heads.value = { dep, ids: [...dep.headIds], primaryId: dep.primaryHeadId, query: '' }
}
const headCandidates = computed(() => {
  if (!heads.value) return []
  const q = heads.value.query.trim().toLowerCase()
  return org.people
    .filter((p) => p.isActive || heads.value.ids.includes(p.id))
    .filter((p) => !q || `${p.name} ${p.position}`.toLowerCase().includes(q))
    .slice(0, 200)
})
function toggleHead(id) {
  const h = heads.value
  if (h.ids.includes(id)) {
    h.ids = h.ids.filter((x) => x !== id)
    if (h.primaryId === id) h.primaryId = h.ids[0] || null
  } else {
    h.ids = [...h.ids, id]
    if (!h.primaryId) h.primaryId = id
  }
}
async function saveHeads() {
  const h = heads.value
  busy.value = true
  try {
    await api.put(`/departments/${h.dep.id}/heads`, { userIds: h.ids, primaryId: h.ids.length ? h.primaryId : null })
    heads.value = null
    await org.reload()
  } catch (err) { ui.error(err) } finally { busy.value = false }
}

// ---------- типы подразделений и глубина ----------
function openSettings() {
  settingsDraft.value = { unitKinds: org.settings.unitKinds.map((k) => ({ ...k })), maxDepth: org.settings.maxDepth }
}
function addKind() {
  settingsDraft.value.unitKinds.push({ value: '', label: '', color: '#4f7cff' })
}
async function saveSettings() {
  busy.value = true
  try {
    await api.put('/org/settings', settingsDraft.value)
    settingsDraft.value = null
    await org.reload()
    ui.success('Настройки структуры сохранены')
  } catch (err) { ui.error(err) } finally { busy.value = false }
}
const kindsInUse = computed(() => new Set(org.departments.map((d) => d.kind)))
const peopleWithoutUnit = computed(() => org.people.filter((p) => p.isActive && !p.departmentId).length)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Структура компании <HelpLink topic="admin-structure" /></h1>
        <div class="page-subtitle">
          {{ org.departments.length }} подразделений · {{ org.people.filter((p) => p.isActive).length }} сотрудников
          <span v-if="peopleWithoutUnit"> · <router-link to="/admin/users?department=none">без подразделения: {{ peopleWithoutUnit }}</router-link></span>
        </div>
      </div>
      <div class="spacer" />
      <router-link class="btn" to="/org"><AppIcon name="hierarchy" :size="14" /> Как видят сотрудники</router-link>
      <button class="btn" type="button" @click="openSettings"><AppIcon name="settings" :size="14" /> Типы и уровни</button>
    </div>

    <form class="card create-row" @submit.prevent="create">
      <input v-model="draft.name" class="input" placeholder="Новое подразделение" maxlength="200" data-testid="dep-name" />
      <select v-model="draft.parentId" class="select" aria-label="Входит в">
        <option value="">Верхний уровень</option>
        <option v-for="o in org.options()" :key="o.id" :value="o.id" :disabled="o.depth >= org.settings.maxDepth">{{ o.label }}</option>
      </select>
      <select v-model="draft.kind" class="select kind-select" aria-label="Тип">
        <option v-for="k in org.settings.unitKinds" :key="k.value" :value="k.value">{{ k.label }}</option>
      </select>
      <button class="btn btn-primary" type="submit" :disabled="busy || !draft.name.trim()" data-testid="dep-add"><AppIcon name="plus" :size="14" /> Добавить</button>
    </form>

    <div class="toolbar">
      <input v-model="search" class="input search" type="search" placeholder="Поиск подразделения или руководителя" />
      <span class="spacer" />
      <button class="btn btn-ghost btn-sm" type="button" @click="collapseAll(false)"><AppIcon name="expandAll" :size="14" /> Развернуть</button>
      <button class="btn btn-ghost btn-sm" type="button" @click="collapseAll(true)"><AppIcon name="collapseAll" :size="14" /> Свернуть</button>
    </div>

    <div class="card table-wrap">
      <table class="table tree" role="treegrid">
        <thead>
          <tr><th>Подразделение</th><th class="num">Сотрудники</th><th>Руководители</th><th /></tr>
        </thead>
        <tbody>
          <tr v-for="dep in rows" :key="dep.id" :aria-level="dep.level" data-testid="dep-row">
            <td>
              <div class="unit" :style="{ paddingLeft: (dep.level - 1) * 20 + 'px' }">
                <button v-if="dep.childCount && !search" class="caret" type="button" :aria-expanded="!collapsed.has(dep.id)" @click="toggle(dep.id)">
                  <AppIcon :name="collapsed.has(dep.id) ? 'chevronRight' : 'chevronDown'" :size="13" />
                </button>
                <span v-else class="caret-space" />
                <span class="kind" :style="{ '--k': org.kindColor(dep.kind) }">{{ org.kindLabel(dep.kind) }}</span>
                <span class="unit-name">{{ dep.name }}</span>
              </div>
            </td>
            <td class="num small">
              <router-link :to="`/admin/users?department=${dep.id}`" title="Сотрудники подразделения">{{ dep.employeeCount }}</router-link>
              <span v-if="dep.branchEmployeeCount !== dep.employeeCount" class="muted"> · в ветке {{ dep.branchEmployeeCount }}</span>
            </td>
            <td>
              <div class="heads">
                <template v-for="id in dep.headIds" :key="id">
                  <span v-if="org.personById[id]" class="head" :class="{ primary: id === dep.primaryHeadId }" :title="id === dep.primaryHeadId ? 'Основной руководитель' : 'Руководитель'">
                    <UserAvatar :user="org.personById[id]" :size="20" />{{ org.personById[id].name }}<span v-if="id === dep.primaryHeadId && dep.headIds.length > 1" class="star">★</span>
                  </span>
                </template>
                <span v-if="!dep.headIds.length" class="muted small">{{ dep.parentId ? 'Подчиняется вышестоящему' : 'Руководитель не назначен' }}</span>
              </div>
            </td>
            <td class="actions">
              <button class="btn btn-ghost btn-icon btn-sm" type="button" title="Вложенное подразделение" :disabled="dep.level >= org.settings.maxDepth" @click="addChild(dep)"><AppIcon name="plus" :size="15" /></button>
              <button class="btn btn-ghost btn-icon btn-sm" type="button" title="Руководители" data-testid="dep-heads" @click="openHeads(dep)"><AppIcon name="users" :size="15" /></button>
              <button class="btn btn-ghost btn-icon btn-sm" type="button" title="Изменить" @click="openEdit(dep)"><AppIcon name="edit" :size="15" /></button>
              <button class="btn btn-ghost btn-icon btn-sm" type="button" :title="dep.childCount ? 'Сначала перенесите или удалите вложенные подразделения' : 'Удалить'" :disabled="!!dep.childCount" @click="remove(dep)"><AppIcon name="trash" :size="15" /></button>
            </td>
          </tr>
          <tr v-if="org.loaded && !org.departments.length">
            <td colspan="4" class="empty">
              <b>Подразделения ещё не созданы</b>
              <p class="muted small">Добавьте компанию и её подразделения выше или загрузите структуру из файла на странице
                <router-link to="/admin/org-transfer">«Импорт и экспорт»</router-link> — в том же формате, что и в «По Делу».</p>
            </td>
          </tr>
          <tr v-else-if="org.loaded && !rows.length"><td colspan="4" class="muted">Ничего не найдено</td></tr>
        </tbody>
      </table>
    </div>

    <BaseModal v-if="edit" title="Подразделение" @close="edit = null">
      <form class="form-stack" @submit.prevent="saveEdit">
        <label class="field"><span class="field-label">Название</span><input v-model="edit.name" class="input" maxlength="200" required /></label>
        <label class="field">
          <span class="field-label">Входит в</span>
          <select v-model="edit.parentId" class="select">
            <option value="">Верхний уровень</option>
            <option v-for="o in org.options({ exclude: edit.id })" :key="o.id" :value="o.id">{{ o.label }}</option>
          </select>
        </label>
        <div class="form-grid">
          <label class="field">
            <span class="field-label">Тип</span>
            <select v-model="edit.kind" class="select"><option v-for="k in org.settings.unitKinds" :key="k.value" :value="k.value">{{ k.label }}</option></select>
          </label>
          <label class="field">
            <span class="field-label">Порядок среди соседних</span>
            <input v-model.number="edit.sortOrder" class="input" type="number" />
            <span class="field-hint">Меньше — выше в списке</span>
          </label>
        </div>
        <button type="submit" class="sr-only" tabindex="-1">ok</button>
      </form>
      <template #footer>
        <button class="btn btn-ghost" type="button" @click="edit = null">Отмена</button>
        <button class="btn btn-primary" type="button" :disabled="busy || !edit.name.trim()" @click="saveEdit">Сохранить</button>
      </template>
    </BaseModal>

    <BaseModal v-if="heads" :title="`Руководители: ${heads.dep.name}`" @close="heads = null">
      <p class="muted small hint">Основной руководитель — непосредственный руководитель сотрудников подразделения. Без руководителя
        сотрудники подчиняются руководителю вышестоящего подразделения.</p>
      <div v-if="heads.ids.length" class="chosen">
        <label v-for="id in heads.ids" :key="id" class="chosen-item">
          <input v-model="heads.primaryId" type="radio" :value="id" name="primary" />
          <UserAvatar :user="org.personById[id]" :size="22" />
          <span class="ellipsis">{{ org.personById[id]?.name }}</span>
          <span v-if="heads.primaryId === id" class="badge badge-primary">основной</span>
          <button class="btn btn-ghost btn-icon btn-sm" type="button" title="Убрать" @click.prevent="toggleHead(id)"><AppIcon name="close" :size="13" /></button>
        </label>
      </div>
      <input v-model="heads.query" class="input" type="search" placeholder="Найти сотрудника" />
      <div class="candidates scroll-thin">
        <label v-for="p in headCandidates" :key="p.id" class="candidate">
          <input type="checkbox" :checked="heads.ids.includes(p.id)" @change="toggleHead(p.id)" />
          <UserAvatar :user="p" :size="22" />
          <span class="ellipsis">{{ p.name }}</span>
          <span class="muted small ellipsis">{{ p.position }}</span>
        </label>
      </div>
      <template #footer>
        <button class="btn btn-ghost" type="button" @click="heads = null">Отмена</button>
        <button class="btn btn-primary" type="button" :disabled="busy" data-testid="heads-save" @click="saveHeads">Сохранить</button>
      </template>
    </BaseModal>

    <BaseModal v-if="settingsDraft" title="Типы подразделений и уровни" size="wide" @close="settingsDraft = null">
      <p class="muted small hint">Типы — как в «По Делу»: подпись видят сотрудники, код используется в файлах обмена. Тип, который уже
        есть в структуре, убрать нельзя.</p>
      <div class="kinds">
        <div v-for="(k, i) in settingsDraft.unitKinds" :key="i" class="kind-row">
          <input v-model="k.color" class="color" type="color" :aria-label="`Цвет типа ${k.label}`" />
          <input v-model="k.label" class="input" placeholder="Подпись" maxlength="60" />
          <input v-model="k.value" class="input mono" placeholder="code" maxlength="32" :readonly="kindsInUse.has(k.value) && !!k.value" />
          <button class="btn btn-ghost btn-icon btn-sm" type="button" :disabled="kindsInUse.has(k.value)" :title="kindsInUse.has(k.value) ? 'Тип используется' : 'Убрать'" @click="settingsDraft.unitKinds.splice(i, 1)"><AppIcon name="close" :size="13" /></button>
        </div>
      </div>
      <button class="btn btn-sm" type="button" @click="addKind"><AppIcon name="plus" :size="13" /> Тип</button>
      <label class="field depth">
        <span class="field-label">Уровней в структуре, не больше</span>
        <input v-model.number="settingsDraft.maxDepth" class="input" type="number" min="2" max="30" />
      </label>
      <template #footer>
        <button class="btn btn-ghost" type="button" @click="settingsDraft = null">Отмена</button>
        <button class="btn btn-primary" type="button" :disabled="busy" @click="saveSettings">Сохранить</button>
      </template>
    </BaseModal>
  </div>
</template>

<style scoped>
.create-row { display: grid; grid-template-columns: minmax(200px, 1.4fr) minmax(180px, 1fr) 160px auto; gap: 8px; padding: 10px; margin-bottom: 12px; }
.toolbar { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.search { width: 320px; }
.tree td { vertical-align: middle; }
.unit { display: flex; align-items: center; gap: 6px; min-width: 0; }
.caret { border: 0; background: none; width: 20px; height: 20px; display: grid; place-items: center; border-radius: 4px; color: var(--color-text-muted); padding: 0; }
.caret:hover { background: var(--color-hover); }
.caret-space { width: 20px; flex-shrink: 0; }
.kind { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em; color: var(--k); background: color-mix(in srgb, var(--k) 12%, white); padding: 1px 6px; border-radius: 4px; white-space: nowrap; }
.unit-name { font-weight: 600; }
.num { white-space: nowrap; }
.heads { display: flex; flex-wrap: wrap; gap: 4px 10px; }
.head { display: inline-flex; align-items: center; gap: 5px; font-size: 13px; }
.head.primary { font-weight: 600; }
.star { color: #e8a13a; }
.actions { text-align: right; white-space: nowrap; width: 150px; }
.empty { text-align: center; padding: 36px 20px; }
.empty p { margin: 6px 0 0; }
.hint { margin: 0 0 10px; line-height: 1.5; }
.chosen { display: flex; flex-direction: column; gap: 4px; margin-bottom: 10px; }
.chosen-item, .candidate { display: grid; grid-template-columns: 18px 22px minmax(0, 1fr) auto auto; align-items: center; gap: 8px; font-size: 13px; padding: 3px 4px; border-radius: 6px; }
.candidate { grid-template-columns: 18px 22px minmax(0, 1fr) minmax(0, 1fr); cursor: pointer; }
.candidate:hover { background: var(--color-hover); }
.candidates { max-height: 300px; overflow: auto; margin-top: 8px; border: 1px solid var(--color-border); border-radius: var(--radius-sm); padding: 4px; }
.kinds { display: flex; flex-direction: column; gap: 6px; margin-bottom: 8px; }
.kind-row { display: grid; grid-template-columns: 36px 1fr 160px 32px; gap: 8px; align-items: center; }
.color { width: 32px; height: 30px; border: 1px solid var(--color-border); border-radius: 6px; padding: 2px; background: #fff; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }
.depth { max-width: 240px; margin-top: 12px; }
@media (max-width: 900px) { .create-row { grid-template-columns: 1fr; } .search { width: 100%; } }
</style>
