<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/client'
import { useOrgStore } from '../stores/org'
import { useAuthStore } from '../stores/auth'
import { useUiStore } from '../stores/ui'
import { pluralize } from '../utils/format'
import HelpLink from '../components/common/HelpLink.vue'
import AppIcon from '../components/common/AppIcon.vue'
import UserAvatar from '../components/common/UserAvatar.vue'

/** Структура компании для всех сотрудников: подразделения, руководители, кто кому подчиняется. */
const org = useOrgStore()
const auth = useAuthStore()
const ui = useUiStore()
const route = useRoute()
const router = useRouter()
const search = ref('')
const collapsed = ref(new Set())
const card = ref(null)

onMounted(async () => {
  try {
    await org.reload()
    // По умолчанию верхние два уровня развёрнуты
    collapsed.value = new Set(org.departments.filter((d) => d.level >= 2 && d.childCount).map((d) => d.id))
    if (!route.query.unit && !route.query.user) {
      const me = org.personById[auth.user?.id]
      const start = me?.departmentId || org.children.get(null)?.[0]?.id
      if (start) router.replace({ query: { unit: start } })
    }
  } catch (err) { ui.error(err) }
})

const unit = computed(() => org.depById[route.query.unit] || null)
const personId = computed(() => (route.query.user ? Number(route.query.user) : null))
watch(personId, async (id) => {
  card.value = null
  if (!id) return
  try { card.value = await api.get(`/org/users/${id}`) } catch (err) { ui.error(err) }
}, { immediate: true })

function reveal(id) {
  let dep = org.depById[id]
  const next = new Set(collapsed.value)
  while (dep) { next.delete(dep.parentId); dep = org.depById[dep.parentId] }
  collapsed.value = next
}
function openUnit(id) {
  router.push({ query: { unit: id } })
  reveal(id)
}
function openFound(person) {
  router.push({ query: { unit: person.departmentId || route.query.unit, user: person.id } })
  if (person.departmentId) reveal(person.departmentId)
  search.value = ''
}
function openPerson(id) {
  router.push({ query: { ...route.query, user: id } })
}
function toggle(id) {
  const next = new Set(collapsed.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  collapsed.value = next
}

const q = computed(() => search.value.trim().toLowerCase())
const treeRows = computed(() => {
  if (q.value) {
    const keep = new Set()
    for (const dep of org.departments) {
      if (dep.name.toLowerCase().includes(q.value)) {
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
const foundPeople = computed(() => (q.value
  ? org.people.filter((p) => `${p.name} ${p.position}`.toLowerCase().includes(q.value)).slice(0, 30)
  : []))

const members = computed(() => {
  if (!unit.value) return []
  const heads = new Set(unit.value.headIds)
  return (org.membersOf.get(unit.value.id) || []).filter((p) => !heads.has(p.id) && p.isActive)
})
const childUnits = computed(() => (unit.value ? org.children.get(unit.value.id) || [] : []))
const breadcrumbs = computed(() => {
  const out = []
  let dep = unit.value
  while (dep) { out.unshift(dep); dep = org.depById[dep.parentId] }
  return out
})
const managerNote = computed(() => {
  if (!card.value) return ''
  if (card.value.reportsToNone) return 'Первое лицо — никому не подчиняется'
  if (card.value.managerSource === 'explicit') return 'назначен явно'
  if (card.value.managerSource === 'structure') return 'по структуре'
  return ''
})
const activeCount = computed(() => org.people.filter((p) => p.isActive).length)
const without = computed(() => org.people.filter((p) => p.isActive && !p.departmentId))
</script>

<template>
  <div class="page org-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Структура компании <HelpLink topic="org" /></h1>
        <div class="page-subtitle">{{ org.departments.length }} {{ pluralize(org.departments.length, 'подразделение', 'подразделения', 'подразделений') }} ·
          {{ activeCount }} {{ pluralize(activeCount, 'сотрудник', 'сотрудника', 'сотрудников') }}</div>
      </div>
      <div class="spacer" />
      <label class="search">
        <AppIcon name="search" :size="14" class="search-icon" />
        <input v-model="search" class="input" type="search" placeholder="Сотрудник или подразделение" data-testid="org-search" />
      </label>
      <router-link v-if="auth.isAdmin" class="btn" to="/admin/structure"><AppIcon name="edit" :size="14" /> Изменить структуру</router-link>
    </div>

    <div v-if="org.loaded && !org.departments.length" class="card empty-state">
      <h3>Структура компании ещё не заполнена</h3>
      <p v-if="auth.isAdmin">Создайте подразделения в разделе <router-link to="/admin/structure">«Структура»</router-link> или загрузите
        структуру из файла «По Делу».</p>
      <p v-else>Администратор пока не заполнил структуру.</p>
    </div>

    <div v-else class="layout">
      <aside class="card tree scroll-thin" aria-label="Подразделения">
        <div v-if="foundPeople.length" class="found">
          <div class="menu-label">Сотрудники</div>
          <button v-for="p in foundPeople" :key="p.id" class="found-person" type="button" @click="openFound(p)">
            <UserAvatar :user="p" :size="22" /><span class="ellipsis">{{ p.name }}</span>
          </button>
          <div v-if="treeRows.length" class="menu-label">Подразделения</div>
        </div>
        <div v-for="dep in treeRows" :key="dep.id" class="node" :class="{ active: unit?.id === dep.id }" :style="{ paddingLeft: 6 + (dep.level - 1) * 16 + 'px' }">
          <button v-if="dep.childCount && !q" class="caret" type="button" :aria-expanded="!collapsed.has(dep.id)" @click="toggle(dep.id)">
            <AppIcon :name="collapsed.has(dep.id) ? 'chevronRight' : 'chevronDown'" :size="12" />
          </button>
          <span v-else class="caret-space" />
          <button class="node-name" type="button" :title="org.kindLabel(dep.kind)" @click="openUnit(dep.id)">
            <span class="dot" :style="{ background: org.kindColor(dep.kind) }" />
            <span class="ellipsis">{{ dep.name }}</span>
            <span class="muted small count">{{ dep.branchEmployeeCount }}</span>
          </button>
        </div>
        <p v-if="q && !treeRows.length && !foundPeople.length" class="muted small pad">Ничего не найдено</p>
        <p v-if="without.length && !q" class="muted small pad">Без подразделения: {{ without.length }}</p>
      </aside>

      <section class="detail">
        <div v-if="card" class="card person-card" data-testid="org-person">
          <div class="person-head">
            <UserAvatar :user="card" :size="56" />
            <div class="person-main">
              <h2>{{ card.name }}</h2>
              <div class="muted">{{ card.position || 'Должность не указана' }}</div>
              <div v-if="card.departmentPath.length" class="small path">{{ card.departmentPath.join(' / ') }}</div>
            </div>
            <button class="btn btn-ghost btn-icon btn-sm" type="button" title="Закрыть" @click="router.push({ query: { unit: route.query.unit } })"><AppIcon name="close" /></button>
          </div>
          <dl class="facts">
            <dt>Руководитель</dt>
            <dd>
              <button v-if="card.directManagerId && org.personById[card.directManagerId]" class="person-link" type="button" @click="openPerson(card.directManagerId)">
                <UserAvatar :user="org.personById[card.directManagerId]" :size="20" /> {{ org.personById[card.directManagerId].name }}
              </button>
              <span v-else>—</span>
              <span v-if="managerNote" class="muted small"> · {{ managerNote }}</span>
            </dd>
            <template v-if="card.chain.length > 1">
              <dt>Цепочка подчинения</dt>
              <dd class="chain">
                <template v-for="(id, i) in card.chain" :key="id">
                  <span v-if="i" class="muted">→</span>
                  <button class="link" type="button" @click="openPerson(id)">{{ org.personById[id]?.name }}</button>
                </template>
              </dd>
            </template>
            <template v-if="card.headOf.length">
              <dt>Руководит</dt>
              <dd><button v-for="id in card.headOf" :key="id" class="link block" type="button" @click="openUnit(id)">{{ org.pathText(id) }}</button></dd>
            </template>
            <template v-if="card.directReports.length">
              <dt>Подчинённые · {{ card.directReports.length }}</dt>
              <dd class="reports">
                <button v-for="id in card.directReports" :key="id" class="person-link" type="button" @click="openPerson(id)">
                  <UserAvatar :user="org.personById[id]" :size="20" /> {{ org.personById[id]?.name }}
                </button>
              </dd>
            </template>
          </dl>
        </div>

        <div v-if="unit" class="card unit-card" data-testid="org-unit">
          <nav class="crumbs small">
            <template v-for="(dep, i) in breadcrumbs" :key="dep.id">
              <span v-if="i" class="muted">/</span>
              <button class="link" type="button" @click="openUnit(dep.id)">{{ dep.name }}</button>
            </template>
          </nav>
          <div class="unit-head">
            <span class="kind" :style="{ '--k': org.kindColor(unit.kind) }">{{ org.kindLabel(unit.kind) }}</span>
            <h2>{{ unit.name }}</h2>
          </div>
          <div class="stats small muted">
            {{ unit.employeeCount }} {{ pluralize(unit.employeeCount, 'сотрудник', 'сотрудника', 'сотрудников') }} в подразделении
            <template v-if="unit.branchEmployeeCount !== unit.employeeCount"> · {{ unit.branchEmployeeCount }} во всей ветке</template>
            <template v-if="childUnits.length"> · {{ childUnits.length }} {{ pluralize(childUnits.length, 'вложенное', 'вложенных', 'вложенных') }}</template>
          </div>

          <h3 class="section-title">{{ unit.headIds.length > 1 ? 'Руководители' : 'Руководитель' }}</h3>
          <div v-if="unit.headIds.length" class="people">
            <button v-for="id in unit.headIds" :key="id" class="person-row" type="button" @click="openPerson(id)">
              <UserAvatar :user="org.personById[id]" :size="32" />
              <span><b>{{ org.personById[id]?.name }}</b><span v-if="id === unit.primaryHeadId && unit.headIds.length > 1" class="badge badge-primary">основной</span>
                <span class="muted small block">{{ org.personById[id]?.position }}</span></span>
            </button>
          </div>
          <p v-else class="muted small">Не назначен — сотрудники подчиняются руководителю вышестоящего подразделения.</p>

          <template v-if="childUnits.length">
            <h3 class="section-title">Подразделения</h3>
            <div class="children">
              <button v-for="dep in childUnits" :key="dep.id" class="child" type="button" @click="openUnit(dep.id)">
                <span class="kind" :style="{ '--k': org.kindColor(dep.kind) }">{{ org.kindLabel(dep.kind) }}</span>
                <span class="ellipsis">{{ dep.name }}</span>
                <span class="muted small">{{ dep.branchEmployeeCount }}</span>
              </button>
            </div>
          </template>

          <template v-if="members.length">
            <h3 class="section-title">Сотрудники · {{ members.length }}</h3>
            <div class="people grid">
              <button v-for="p in members" :key="p.id" class="person-row" type="button" @click="openPerson(p.id)">
                <UserAvatar :user="p" :size="32" />
                <span class="ellipsis-block"><b class="ellipsis">{{ p.name }}</b><span class="muted small ellipsis block">{{ p.position }}</span></span>
              </button>
            </div>
          </template>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.org-page { max-width: 1320px; }
.search { position: relative; width: 300px; }
.search .input { padding-left: 28px; width: 100%; }
.search-icon { position: absolute; left: 9px; top: 50%; transform: translateY(-50%); color: var(--color-text-muted); }
.layout { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 14px; align-items: start; }
.tree { position: sticky; top: 12px; max-height: calc(100vh - 140px); overflow: auto; padding: 6px; }
.node { display: flex; align-items: center; gap: 2px; border-radius: 6px; }
.node.active { background: var(--color-primary-soft); }
.node:not(.active):hover { background: var(--color-hover); }
.caret { border: 0; background: none; width: 20px; height: 24px; display: grid; place-items: center; color: var(--color-text-muted); padding: 0; flex-shrink: 0; }
.caret-space { width: 20px; flex-shrink: 0; }
.node-name { flex: 1; min-width: 0; display: flex; align-items: center; gap: 7px; border: 0; background: none; text-align: left; padding: 5px 6px 5px 0; font-size: 13px; color: var(--color-text); }
.node.active .node-name { font-weight: 600; color: var(--color-primary-dark); }
.dot { width: 8px; height: 8px; border-radius: 2px; flex-shrink: 0; }
.count { margin-left: auto; }
.pad { padding: 6px 10px; margin: 0; }
.found { border-bottom: 1px solid var(--color-border); margin-bottom: 4px; padding-bottom: 4px; }
.found-person { display: flex; align-items: center; gap: 8px; width: 100%; border: 0; background: none; padding: 5px 8px; border-radius: 6px; font-size: 13px; text-align: left; }
.found-person:hover { background: var(--color-hover); }
.detail { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.unit-card, .person-card { padding: 16px 18px; display: flex; flex-direction: column; gap: 10px; }
.crumbs { display: flex; flex-wrap: wrap; gap: 4px 6px; align-items: center; }
.unit-head { display: flex; align-items: center; gap: 10px; }
.unit-head h2, .person-main h2 { font-size: 18px; }
.kind { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em; color: var(--k); background: color-mix(in srgb, var(--k) 12%, white); padding: 1px 6px; border-radius: 4px; white-space: nowrap; }
.section-title { margin-top: 6px; }
.people { display: flex; flex-direction: column; gap: 2px; }
.people.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 2px 10px; }
.person-row { display: flex; align-items: center; gap: 10px; border: 0; background: none; padding: 6px 8px; border-radius: 8px; text-align: left; font-size: 13px; color: var(--color-text); min-width: 0; }
.person-row:hover { background: var(--color-hover); }
.person-row .badge { margin-left: 6px; }
.ellipsis-block { min-width: 0; display: flex; flex-direction: column; }
.block { display: block; }
.children { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 6px; }
.child { display: flex; align-items: center; gap: 8px; border: 1px solid var(--color-border); background: var(--color-surface); border-radius: 8px; padding: 8px 10px; text-align: left; font-size: 13px; min-width: 0; }
.child:hover { border-color: var(--color-border-strong); background: var(--color-surface-2); }
.child .muted { margin-left: auto; }
.person-head { display: flex; gap: 14px; align-items: flex-start; }
.person-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.path { color: var(--color-text-secondary); }
.facts { display: grid; grid-template-columns: 170px minmax(0, 1fr); gap: 8px 14px; margin: 4px 0 0; font-size: 13px; }
.facts dt { color: var(--color-text-muted); }
.facts dd { margin: 0; }
.person-link { display: inline-flex; align-items: center; gap: 6px; border: 0; background: none; padding: 0; font-size: 13px; color: var(--color-primary-dark); }
.person-link:hover { text-decoration: underline; }
.reports { display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.chain { display: flex; flex-wrap: wrap; gap: 4px 6px; align-items: center; }
.link { border: 0; background: none; padding: 0; color: var(--color-primary-dark); font-size: inherit; text-align: left; }
.link:hover { text-decoration: underline; }
@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; }
  .tree { position: static; max-height: 40vh; }
  .search { width: 100%; }
  .facts { grid-template-columns: 1fr; gap: 2px; }
  .facts dd { margin-bottom: 8px; }
}
</style>
