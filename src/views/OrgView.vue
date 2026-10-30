<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/client'
import { useOrgStore } from '../stores/org'
import { useAuthStore } from '../stores/auth'
import { localStorageGet, localStorageSet, useUiStore } from '../stores/ui'
import { pluralize } from '../utils/format'
import { loadPodelu, podeluPersonLink, podeluUnitLink } from '../integrations/podelu'
import {
  DEFAULT_LEVELS, DEFAULT_ORG_VIEW, LOOSE_ID, ORG_VIEWS, clampZoom, expandedToLevel, forestDepth, indexForest,
  normText, normalizeView, pathTo, peopleForest, readOrgPrefs, revealIn, searchOrg, unitForest, unitTableRows,
} from '../utils/orgViews'
import HelpLink from '../components/common/HelpLink.vue'
import AppIcon from '../components/common/AppIcon.vue'
import UserAvatar from '../components/common/UserAvatar.vue'
import OrgChart from '../components/org/OrgChart.vue'
import OrgUnitCard from '../components/org/OrgUnitCard.vue'
import OrgPersonNode from '../components/org/OrgPersonNode.vue'
import OrgTable from '../components/org/OrgTable.vue'

/**
 * Структура компании для всех сотрудников (ADR-0008) — четыре вида, как в «По Делу»:
 *  - «Оргсхема» — подразделения карточками с линиями подчинения, уровни, масштаб, «Где я»;
 *  - «Люди» — кто кому подчиняется: руководители и их команды по вычисленному руководителю;
 *  - «Дерево» — компактное дерево подразделений (вид версии 0.3);
 *  - «Таблица» — все подразделения с численностью, сортировкой и выгрузкой в CSV.
 * Справа во всех видах — карточка подразделения и карточка сотрудника. Ссылки ?unit=, ?user= и ?view=
 * можно передавать; выбранный вид запоминается для каждого сотрудника в браузере.
 */
const org = useOrgStore()
const auth = useAuthStore()
const ui = useUiStore()
const route = useRoute()
const router = useRouter()
const search = ref('')
const collapsed = ref(new Set())
const card = ref(null)
const panel = ref(null)
const chart = ref(null)

// ---------- вид и личные настройки (localStorage, у каждого сотрудника свои) ----------
const prefsKey = `puls.org.${auth.user?.id || 0}`
const prefs = reactive(readOrgPrefs(localStorageGet(prefsKey)))
watch(prefs, (value) => localStorageSet(prefsKey, JSON.stringify(value)), { deep: true })
const view = computed(() => normalizeView(route.query.view) || prefs.view || DEFAULT_ORG_VIEW)
function setView(value) {
  prefs.view = value
  if (route.query.view !== value) router.replace({ query: { ...route.query, view: value } })
}
const tabs = ref(null)
function onTabKey(event) {
  const keys = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }
  const i = ORG_VIEWS.findIndex((v) => v.value === view.value)
  let next = null
  if (event.key in keys) next = (i + keys[event.key] + ORG_VIEWS.length) % ORG_VIEWS.length
  else if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = ORG_VIEWS.length - 1
  if (next === null) return
  event.preventDefault()
  setView(ORG_VIEWS[next].value)
  nextTick(() => tabs.value?.querySelector(`[data-testid=org-view-${ORG_VIEWS[next].value}]`)?.focus())
}
/** Переход внутри страницы: выбранный в адресе вид сохраняется. */
function go(query) {
  const next = { ...query }
  if (route.query.view) next.view = route.query.view
  for (const key of Object.keys(next)) if (next[key] === undefined || next[key] === null) delete next[key]
  return router.push({ query: next })
}

let ready = false  // структура загружена и выбранное показано — дальше переходы делает сотрудник
onMounted(async () => {
  try {
    await org.reload()
    // По умолчанию верхние два уровня развёрнуты
    collapsed.value = new Set(org.departments.filter((d) => d.level >= 2 && d.childCount).map((d) => d.id))
    resetExpansion()
    if (route.query.unit) reveal(route.query.unit)
    if (personId.value) revealPerson(personId.value)
    if (!route.query.unit && !route.query.user) {
      const me = org.personById[auth.user?.id]
      const start = me?.departmentId || me?.headOf?.[0] || org.children.get(null)?.[0]?.id
      if (start) router.replace({ query: { ...route.query, unit: start } })
    }
    if (view.value === 'people' && !personId.value) whereAmI({ focus: false })
  } catch (err) { ui.error(err) }
  ready = true
})

const unit = computed(() => org.depById[route.query.unit] || null)
// Ссылки на то же подразделение и сотрудника в «По Делу», если она подключена и данные загружены оттуда (ADR-0009)
const podelu = ref({ connected: false, baseUrl: null })
loadPodelu().then((status) => { podelu.value = status })
const unitPodeluUrl = computed(() => podeluUnitLink(podelu.value, unit.value))
const cardPodeluUrl = computed(() => podeluPersonLink(podelu.value, card.value))
const personId = computed(() => (route.query.user ? Number(route.query.user) : null))
watch(personId, async (id) => {
  card.value = null
  if (!id) return
  const initial = !ready
  try { card.value = await api.get(`/org/users/${id}`) } catch (err) { ui.error(err) }
  // Карточка сотрудника встаёт над карточкой подразделения: после щелчка в длинном списке — показать её
  if (!initial && card.value) nextTick(() => panel.value?.querySelector('[data-testid=org-person]')?.scrollIntoView({ block: 'nearest' }))
}, { immediate: true })

/** Показать подразделение в текущем виде: раскрыть дерево или ветки схемы до него и прокрутить к нему. */
function reveal(id) {
  if (!org.depById[id]) return
  let dep = org.depById[id]
  const next = new Set(collapsed.value)
  while (dep) { next.delete(dep.parentId); dep = org.depById[dep.parentId] }
  collapsed.value = next
  unitExpanded.value = revealIn(unitExpanded.value, unitIndex.value, id)
  if (view.value === 'chart') chart.value?.scrollTo(id)
}
/** Показать сотрудника: на схеме «Люди» — раскрыть цепочку руководителей до него. */
function revealPerson(id) {
  if (!teamIndex.value.has(id)) return
  teamExpanded.value = revealIn(teamExpanded.value, teamIndex.value, id)
  if (view.value === 'people') chart.value?.scrollTo(id)
  // Ссылка только на сотрудника — на оргсхеме раскрыть его подразделение
  const dep = org.personById[id]?.departmentId
  if (dep && !route.query.unit) {
    unitExpanded.value = revealIn(unitExpanded.value, unitIndex.value, dep)
    if (view.value === 'chart') chart.value?.scrollTo(dep)
  }
}
// На телефоне карточка — под схемой или таблицей: после выбора в них прокручиваем к ней
function showPanel() {
  if (view.value === 'tree' || !window.matchMedia?.('(max-width: 900px)').matches) return
  nextTick(() => requestAnimationFrame(() => panel.value?.scrollIntoView({ block: 'start' })))
}
function openUnit(id) {
  go({ unit: id })
}
// Переход по ссылке, «Назад» браузера или щелчок — выбранное подразделение раскрыто в текущем виде
watch(() => route.query.unit, (id) => { if (id && org.loaded) reveal(id) })
watch(personId, (id) => { if (id && org.loaded) revealPerson(id) })
function openFound(person) {
  go({ unit: person.departmentId || route.query.unit, user: person.id })
  search.value = ''
}
function openPerson(id) {
  go({ ...route.query, user: id })
}
function toggle(id) {
  const next = new Set(collapsed.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  collapsed.value = next
}

// ---------- выбор в оргсхеме, схеме «Люди» и таблице ----------
function selectUnit(id) {
  go({ unit: id })
  showPanel()
}
function selectHead(personIdValue, depId) {
  go({ unit: depId, user: personIdValue })
  showPanel()
}
function selectPerson(id) {
  const person = org.personById[id]
  go({ unit: person?.departmentId || undefined, user: id })
  showPanel()
}
function closeUnit() {
  go({ user: route.query.user })
}

// ---------- схемы: деревья, раскрытие уровней, путь к выбранному ----------
const units = computed(() => unitForest(org.departments))
const unitIndex = computed(() => indexForest(units.value))
const team = computed(() => peopleForest(org.people))
const teamIndex = computed(() => indexForest(team.value))
const unitExpanded = ref(new Set())
const teamExpanded = ref(new Set())
const levelsValue = () => (prefs.levels === 'all' ? null : Number(prefs.levels) || DEFAULT_LEVELS)
function resetExpansion() {
  unitExpanded.value = expandedToLevel(units.value, levelsValue())
  teamExpanded.value = expandedToLevel(team.value, levelsValue())
}
watch(() => prefs.levels, resetExpansion)
const levelOptions = computed(() => {
  const depth = Math.max(forestDepth(view.value === 'people' ? team.value : units.value), 1, Number(prefs.levels) || 1)
  return Array.from({ length: Math.min(depth, 12) }, (_, i) => i + 1)
})
function toggleIn(target, id) {
  const next = new Set(target.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  target.value = next
}
const unitPath = computed(() => pathTo(unitIndex.value, unit.value?.id || org.personById[personId.value]?.departmentId))
const teamPath = computed(() => pathTo(teamIndex.value, personId.value))
const meId = computed(() => auth.user?.id ?? null)
const myUnits = computed(() => {
  const me = org.personById[meId.value]
  return new Set([me?.departmentId, ...(me?.headOf || [])].filter(Boolean))
})
const canFindMe = computed(() => (view.value === 'people' ? teamIndex.value.has(meId.value) : myUnits.value.size > 0))
/** «Где я»: раскрыть ветку до своего подразделения (на схеме «Люди» — до себя) и прокрутить к нему. */
function whereAmI({ focus = true } = {}) {
  if (view.value === 'people') {
    if (!teamIndex.value.has(meId.value)) return
    teamExpanded.value = revealIn(teamExpanded.value, teamIndex.value, meId.value)
    chart.value?.scrollTo(meId.value, { focus })
  } else {
    const first = [...myUnits.value][0]
    if (!first) return
    unitExpanded.value = revealIn(unitExpanded.value, unitIndex.value, first)
    chart.value?.scrollTo(first, { focus })
  }
}
// Переключили вид — выбранное видно и в новом виде
watch(view, (value) => {
  if (!org.loaded) return
  nextTick(() => {
    if (value === 'people') {
      if (personId.value && teamIndex.value.has(personId.value)) revealPerson(personId.value)
      else whereAmI({ focus: false })
    } else if (route.query.unit) reveal(route.query.unit)
  })
})
function zoomBy(step) { prefs.zoom = clampZoom(Math.round((prefs.zoom + step) * 10) / 10) }
function setZoom(value) { prefs.zoom = clampZoom(value) }
const unitName = (person) => (person.departmentId ? org.depById[person.departmentId]?.name || '' : '')
/** Руководитель на карточке: основной, а если он не отмечен или отключён — первый из активных. */
function primaryHead(dep) {
  for (const id of [dep.primaryHeadId, ...dep.headIds]) {
    const person = org.personById[id]
    if (person?.isActive) return person
  }
  return null
}
const tableRows = computed(() => unitTableRows(org.departments, org.people, org.kindLabel))

// ---------- поиск ----------
const q = computed(() => normText(search.value.trim()))
const searchOpen = ref(false)
const activeResult = ref(0)
// В дереве поиск фильтрует само дерево; в схемах — список найденного; в таблице фильтрует строки, а люди — в списке
const results = computed(() => (view.value === 'tree' || !q.value ? []
  : searchOrg(search.value, org.departments, org.people, 12).filter((r) => view.value !== 'table' || r.type === 'person')))
const showResults = computed(() => searchOpen.value && !!q.value && view.value !== 'tree' && (results.value.length > 0 || view.value !== 'table'))
watch(search, () => { activeResult.value = 0; searchOpen.value = true })
function pick(result) {
  if (!result) return
  searchOpen.value = false
  if (result.type === 'unit') selectUnit(result.id)
  else selectPerson(result.id)
  search.value = ''
}
function onSearchKey(event) {
  if (!showResults.value) return
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    const n = results.value.length
    if (n) activeResult.value = (activeResult.value + (event.key === 'ArrowDown' ? 1 : -1) + n) % n
  } else if (event.key === 'Enter') {
    event.preventDefault()
    pick(results.value[activeResult.value])
  } else if (event.key === 'Escape') {
    searchOpen.value = false
  }
}
function closeSearchSoon() { setTimeout(() => { searchOpen.value = false }, 150) }

const treeRows = computed(() => {
  if (q.value) {
    const keep = new Set()
    for (const dep of org.departments) {
      if (normText(dep.name).includes(q.value)) {
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
const foundPeople = computed(() => (q.value && view.value === 'tree'
  ? org.people.filter((p) => p.isActive && normText(`${p.name} ${p.position}`).includes(q.value)).slice(0, 30)
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
  <div class="page org-page" :class="{ wide: view !== 'tree' }">
    <div class="page-header">
      <div>
        <h1 class="page-title">Структура компании <HelpLink topic="org" /></h1>
        <div class="page-subtitle">{{ org.departments.length }} {{ pluralize(org.departments.length, 'подразделение', 'подразделения', 'подразделений') }} ·
          {{ activeCount }} {{ pluralize(activeCount, 'сотрудник', 'сотрудника', 'сотрудников') }}</div>
      </div>
      <div class="spacer" />
      <div class="search">
        <AppIcon name="search" :size="14" class="search-icon" />
        <input
          v-model="search" class="input" type="search" placeholder="Сотрудник или подразделение" aria-label="Поиск по структуре" data-testid="org-search"
          :role="view !== 'tree' ? 'combobox' : undefined" :aria-expanded="view !== 'tree' ? showResults : undefined" :aria-controls="view !== 'tree' ? 'org-search-results' : undefined"
          :aria-autocomplete="view !== 'tree' ? 'list' : undefined" :aria-activedescendant="showResults && results.length ? `org-result-${activeResult}` : undefined"
          @focus="searchOpen = true" @blur="closeSearchSoon" @keydown="onSearchKey"
        />
        <div v-if="showResults" id="org-search-results" class="results card" role="listbox" aria-label="Найдено">
          <button
            v-for="(r, i) in results" :id="`org-result-${i}`" :key="`${r.type}-${r.id}`" type="button" role="option" tabindex="-1"
            class="result" :class="{ active: i === activeResult }" :aria-selected="i === activeResult" data-testid="org-search-result"
            @mousedown.prevent="pick(r)" @mousemove="activeResult = i"
          >
            <UserAvatar v-if="r.type === 'person'" :user="org.personById[r.id]" :size="24" />
            <span v-else class="result-icon"><AppIcon name="hierarchy" :size="14" /></span>
            <span class="result-text"><b class="ellipsis">{{ r.title }}</b>
              <small class="muted ellipsis">{{ r.type === 'person' ? [r.subtitle, unitName(org.personById[r.id])].filter(Boolean).join(' · ') : org.kindLabel(org.depById[r.id]?.kind) }}</small></span>
          </button>
          <p v-if="!results.length" class="muted small pad">Ничего не найдено</p>
        </div>
      </div>
      <router-link v-if="auth.isAdmin" class="btn" to="/admin/structure"><AppIcon name="edit" :size="14" /> Изменить структуру</router-link>
    </div>

    <div v-if="org.loaded && !org.departments.length" class="card empty-state">
      <h3>Структура компании ещё не заполнена</h3>
      <p v-if="auth.isAdmin">Создайте подразделения в разделе <router-link to="/admin/structure">«Структура»</router-link> или загрузите
        структуру из файла «По Делу».</p>
      <p v-else>Администратор пока не заполнил структуру.</p>
    </div>

    <template v-else>
      <div class="toolbar">
        <div ref="tabs" class="segmented views" role="tablist" aria-label="Вид структуры" @keydown="onTabKey">
          <button
            v-for="item in ORG_VIEWS" :id="`org-tab-${item.value}`" :key="item.value" type="button" role="tab"
            :class="{ 'is-active': view === item.value }" :aria-selected="view === item.value" aria-controls="org-view-panel"
            :tabindex="view === item.value ? 0 : -1" :title="item.hint" :data-testid="`org-view-${item.value}`" @click="setView(item.value)"
          >
            <AppIcon :name="item.icon" :size="14" /><span>{{ item.label }}</span>
          </button>
        </div>
        <div v-if="view === 'chart' || view === 'people'" class="tools">
          <label class="tool">
            <span>Уровней</span>
            <select v-model="prefs.levels" class="select input-sm" aria-label="Сколько уровней раскрыть" data-testid="org-levels">
              <option v-for="n in levelOptions" :key="n" :value="n">{{ n }}</option>
              <option value="all">все</option>
            </select>
          </label>
          <button type="button" class="btn btn-ghost btn-icon btn-sm" title="Развернуть всё" aria-label="Развернуть всё" @click="prefs.levels = 'all'; resetExpansion()"><AppIcon name="expandAll" :size="15" /></button>
          <button type="button" class="btn btn-ghost btn-icon btn-sm" title="Свернуть до верхнего уровня" aria-label="Свернуть до верхнего уровня" @click="prefs.levels = 1; resetExpansion()"><AppIcon name="collapseAll" :size="15" /></button>
          <button v-if="canFindMe" type="button" class="btn btn-ghost btn-sm" data-testid="org-where-am-i" @click="whereAmI()"><AppIcon name="target" :size="14" /> Где я</button>
          <button
            v-if="view === 'chart'" type="button" class="btn btn-ghost btn-icon btn-sm" :class="{ 'is-active': prefs.showKind }" :aria-pressed="prefs.showKind"
            title="Показывать тип подразделения" aria-label="Показывать тип подразделения" data-testid="org-show-kind" @click="prefs.showKind = !prefs.showKind"
          ><AppIcon name="tag" :size="15" /></button>
          <span class="tool-sep" />
          <button type="button" class="btn btn-ghost btn-icon btn-sm" title="Мельче" aria-label="Уменьшить масштаб" @click="zoomBy(-0.1)"><AppIcon name="zoomOut" :size="15" /></button>
          <button type="button" class="btn btn-ghost btn-sm zoom-value" title="Масштаб 100 %" aria-label="Масштаб 100 %" @click="setZoom(1)">{{ Math.round(prefs.zoom * 100) }} %</button>
          <button type="button" class="btn btn-ghost btn-icon btn-sm" title="Крупнее" aria-label="Увеличить масштаб" @click="zoomBy(0.1)"><AppIcon name="zoomIn" :size="15" /></button>
          <button type="button" class="btn btn-ghost btn-icon btn-sm" title="Вписать в экран" aria-label="Вписать в экран" @click="chart?.fit()"><AppIcon name="fit" :size="15" /></button>
        </div>
      </div>

      <div class="layout" :class="`layout-${view}`">
        <aside v-if="view === 'tree'" id="org-view-panel" class="card tree scroll-thin" role="tabpanel" aria-labelledby="org-tab-tree">
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

        <div v-else id="org-view-panel" class="view-main" role="tabpanel" :aria-labelledby="`org-tab-${view}`">
          <OrgChart
            v-if="view === 'chart'" ref="chart" class="canvas" label="Оргсхема подразделений" :forest="units" :expanded="unitExpanded" :path="unitPath"
            :zoom="prefs.zoom" data-testid="org-chart" @update:zoom="setZoom"
          >
            <template #default="{ node, expanded, onPath }">
              <OrgUnitCard
                :dep="node.item" :head="primaryHead(node.item)" :head-team="teamIndex.get(primaryHead(node.item)?.id)?.node.branch || 0"
                :extra-heads="Math.max(0, node.item.headIds.length - 1)" :kind-label="org.kindLabel(node.item.kind)" :kind-color="org.kindColor(node.item.kind)"
                :show-kind="prefs.showKind" :expanded="expanded" :on-path="onPath" :selected="unit?.id === node.id" :mine="myUnits.has(node.id)"
                @select="selectUnit" @toggle="toggleIn(unitExpanded, $event)" @select-person="selectHead($event, node.id)"
              />
            </template>
          </OrgChart>

          <OrgChart
            v-else-if="view === 'people'" ref="chart" class="canvas" label="Схема подчинения" :forest="team" :expanded="teamExpanded" :path="teamPath"
            :zoom="prefs.zoom" data-testid="org-people" @update:zoom="setZoom"
          >
            <template #default="{ node, expanded, onPath }">
              <div v-if="node.id === LOOSE_ID" class="loose-card" :class="{ 'is-path': onPath }">
                <span class="loose-title">Без руководителя</span>
                <span class="muted small">Нет ни руководителя, ни подчинённых — не входят в цепочки подчинения</span>
                <button type="button" class="loose-toggle" :aria-expanded="expanded" data-testid="org-loose-toggle" @click="toggleIn(teamExpanded, LOOSE_ID)">
                  {{ expanded ? 'Скрыть' : 'Показать' }}: {{ node.children.length }}<AppIcon :name="expanded ? 'chevronUp' : 'chevronDown'" :size="12" />
                </button>
              </div>
              <OrgPersonNode
                v-else :person="node.item" :unit-name="unitName(node.item)" :reports="node.reports" :branch="node.branch"
                :expanded="expanded" :on-path="onPath" :selected="personId === node.id" :me="node.id === meId"
                @select="selectPerson" @toggle="toggleIn(teamExpanded, $event)"
              />
            </template>
          </OrgChart>

          <OrgTable
            v-else :rows="tableRows" :kinds="org.settings.unitKinds" :kind-color="org.kindColor" :needle="search" :selected-id="unit?.id || null"
            @select="selectUnit" @select-person="selectHead"
          />
        </div>

        <section ref="panel" class="detail" :class="{ 'scroll-thin': view !== 'tree' }" aria-label="Подробности">
          <div v-if="card" class="card person-card" data-testid="org-person">
            <div class="person-head">
              <UserAvatar :user="card" :size="56" />
              <div class="person-main">
                <h2>{{ card.name }}</h2>
                <div class="muted">{{ card.position || 'Должность не указана' }}</div>
                <div v-if="card.departmentPath.length" class="small path">{{ card.departmentPath.join(' / ') }}</div>
                <a v-if="cardPodeluUrl" class="podelu-link small" :href="cardPodeluUrl" target="_blank" rel="noopener noreferrer" data-testid="org-person-podelu">
                  <AppIcon name="external" :size="12" /> Открыть в «По Делу»
                </a>
              </div>
              <button class="btn btn-ghost btn-icon btn-sm" type="button" title="Закрыть" @click="go({ unit: route.query.unit })"><AppIcon name="close" /></button>
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
            <div class="crumbs-row">
              <nav class="crumbs small" aria-label="Путь в структуре">
                <template v-for="(dep, i) in breadcrumbs" :key="dep.id">
                  <span v-if="i" class="muted">/</span>
                  <button class="link" type="button" @click="openUnit(dep.id)">{{ dep.name }}</button>
                </template>
              </nav>
              <button v-if="view !== 'tree'" class="btn btn-ghost btn-icon btn-sm" type="button" title="Закрыть карточку подразделения" aria-label="Закрыть карточку подразделения" @click="closeUnit"><AppIcon name="close" /></button>
            </div>
            <div class="unit-head">
              <span class="kind" :style="{ '--k': org.kindColor(unit.kind) }">{{ org.kindLabel(unit.kind) }}</span>
              <h2>{{ unit.name }}</h2>
            </div>
            <div class="stats small muted">
              {{ unit.employeeCount }} {{ pluralize(unit.employeeCount, 'сотрудник', 'сотрудника', 'сотрудников') }} в подразделении
              <template v-if="unit.branchEmployeeCount !== unit.employeeCount"> · {{ unit.branchEmployeeCount }} во всей ветке</template>
              <template v-if="childUnits.length"> · {{ childUnits.length }} {{ pluralize(childUnits.length, 'вложенное', 'вложенных', 'вложенных') }}</template>
            </div>
            <a v-if="unitPodeluUrl" class="podelu-link small" :href="unitPodeluUrl" target="_blank" rel="noopener noreferrer" data-testid="org-unit-podelu">
              <AppIcon name="external" :size="12" /> Открыть в «По Делу»
            </a>

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

          <p v-if="!card && !unit && view !== 'tree'" class="card hint muted small">Выберите подразделение или сотрудника — здесь появятся подробности.</p>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.org-page { max-width: 1320px; }
.org-page.wide { max-width: 1680px; }
.search { position: relative; width: 300px; }
.search .input { padding-left: 28px; width: 100%; }
.search-icon { position: absolute; left: 9px; top: 50%; transform: translateY(-50%); color: var(--color-text-muted); }
.results { position: absolute; z-index: 30; top: calc(100% + 4px); left: 0; right: 0; max-height: 360px; overflow-y: auto; padding: 4px; box-shadow: var(--shadow-2); }
.result { display: flex; align-items: center; gap: 9px; width: 100%; padding: 6px 8px; border: 0; border-radius: 6px; background: none; text-align: left; color: var(--color-text); min-width: 0; }
.result.active { background: var(--color-hover); }
.result-icon { display: grid; place-items: center; width: 24px; height: 24px; border-radius: 6px; background: var(--color-primary-soft); color: var(--color-primary-dark); flex-shrink: 0; }
.result-text { display: flex; flex-direction: column; min-width: 0; font-size: 13px; }
.result-text small { font-size: 11.5px; }

.toolbar { display: flex; align-items: center; gap: 8px 14px; flex-wrap: wrap; margin: -6px 0 12px; }
.views button { display: inline-flex; align-items: center; gap: 6px; }
.tools { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.tool { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--color-text-muted); margin-right: 4px; }
.tool .select { width: auto; min-width: 64px; }
.tool-sep { width: 1px; height: 22px; background: var(--color-border); margin: 0 4px; }
.zoom-value { min-width: 56px; font-variant-numeric: tabular-nums; color: var(--color-text-secondary); }

.layout { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 14px; align-items: start; }
.layout:not(.layout-tree) { grid-template-columns: minmax(0, 1fr) 360px; }
.layout-table:not(.layout-tree) { grid-template-columns: minmax(0, 1fr) 320px; }
.view-main { min-width: 0; }
.canvas { height: calc(100vh - 196px); min-height: 420px; }
.layout:not(.layout-tree) .detail { position: sticky; top: 12px; max-height: calc(100vh - 150px); min-height: 240px; overflow-y: auto; overscroll-behavior: contain; padding: 1px 3px 4px; margin: -1px -3px 0; }
.hint { padding: 16px 18px; margin: 0; }
.loose-card { width: 212px; display: flex; flex-direction: column; gap: 4px; padding: 10px 12px 0; border: 1.5px dashed var(--color-border-strong); border-radius: var(--radius-md); background: var(--color-surface); }
.loose-card.is-path { border-color: var(--color-primary); }
.loose-title { font-size: 13px; font-weight: 700; }
.loose-toggle { display: flex; align-items: center; justify-content: center; gap: 4px; margin: 4px -12px 0; padding: 6px; border: 0; border-top: 1px solid var(--color-border); border-radius: 0 0 var(--radius-md) var(--radius-md); background: var(--color-surface-2); color: var(--color-primary-dark); font-size: 11.5px; font-weight: 600; }
.loose-toggle:hover { background: var(--color-primary-soft); }

.tree { position: sticky; top: 12px; max-height: calc(100vh - 186px); overflow: auto; padding: 6px; }
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
.crumbs-row { display: flex; align-items: flex-start; gap: 8px; }
.crumbs-row .btn { margin: -6px -8px 0 auto; }
.crumbs { display: flex; flex-wrap: wrap; gap: 4px 6px; align-items: center; min-width: 0; }
.unit-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.unit-head h2, .person-main h2 { font-size: 18px; overflow-wrap: anywhere; }
.kind { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em; color: color-mix(in srgb, var(--k) 80%, black); background: color-mix(in srgb, var(--k) 12%, white); padding: 1px 6px; border-radius: 4px; white-space: nowrap; }
.section-title { margin-top: 6px; }
.people { display: flex; flex-direction: column; gap: 2px; }
.people.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 2px 10px; }
.layout:not(.layout-tree) .people.grid, .layout:not(.layout-tree) .children { grid-template-columns: minmax(0, 1fr); }
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
.layout:not(.layout-tree) .facts { grid-template-columns: 1fr; gap: 2px; }
.layout:not(.layout-tree) .facts dd { margin-bottom: 8px; }
.facts dt { color: var(--color-text-muted); }
.facts dd { margin: 0; }
.person-link { display: inline-flex; align-items: center; gap: 6px; border: 0; background: none; padding: 0; font-size: 13px; color: var(--color-primary-dark); }
.person-link:hover { text-decoration: underline; }
.reports { display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.chain { display: flex; flex-wrap: wrap; gap: 4px 6px; align-items: center; }
.link { border: 0; background: none; padding: 0; color: var(--color-primary-dark); font-size: inherit; text-align: left; }
.link:hover { text-decoration: underline; }
@media (max-width: 1100px) {
  .layout:not(.layout-tree) { grid-template-columns: minmax(0, 1fr) 300px; }
}
@media (max-width: 900px) {
  .layout, .layout:not(.layout-tree) { grid-template-columns: minmax(0, 1fr); }
  .tree { position: static; max-height: 40vh; }
  .layout:not(.layout-tree) .detail { position: static; max-height: none; overflow: visible; scroll-margin-top: 12px; }
  .search { width: 100%; }
  .facts { grid-template-columns: 1fr; gap: 2px; }
  .facts dd { margin-bottom: 8px; }
  .canvas { height: 62vh; min-height: 320px; }
  .hint { display: none; }
}
@media (max-width: 768px) {
  .toolbar { margin-top: 0; }
  /* Над карточкой — плавающая кнопка меню */
  .layout:not(.layout-tree) .detail { scroll-margin-top: 60px; }
  .views { width: 100%; }
  .views button { flex: 1 1 0; justify-content: center; padding: 4px 6px; min-width: 0; }
  .tool-sep { display: none; }
}
.podelu-link { display: inline-flex; align-items: center; gap: 4px; margin-top: 6px; color: var(--color-primary-dark); text-decoration: none; font-weight: 600; }
.podelu-link:hover { text-decoration: underline; }
</style>
