<script setup>
import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ACCESS_LABELS, useProjectsStore } from '../stores/projects'
import { useAuthStore } from '../stores/auth'
import { localStorageGet, localStorageSet, useUiStore } from '../stores/ui'
import { useDialogs } from '../composables/useDialogs'
import { api } from '../api/client'
import AppIcon from '../components/common/AppIcon.vue'
import HelpLink from '../components/common/HelpLink.vue'
import BaseModal from '../components/common/BaseModal.vue'
import PopupMenu from '../components/common/PopupMenu.vue'
import { countLabel, formatDate, formatDateTime } from '../utils/format'
import {
  PROJECT_SORTS, PROJECT_STATES, DEFAULT_PROJECT_FILTERS, directionMatches, matchesProjectFilters, normalizeProjectFilters,
  projectFiltersActive, projectProgress, projectState, sortProjects,
} from '../utils/projectFilters'

// Статистика проекта с главной — то же окно, что в проекте; загружается, только когда его открывают
const StatsModal = defineAsyncComponent(() => import('../gantt/components/StatsModal.vue'))

const projects = useProjectsStore()
const auth = useAuthStore()
const ui = useUiStore()
const router = useRouter()
const { confirm, prompt } = useDialogs()

const search = ref('')
const editor = ref(null)
const saving = ref(false)
const menu = ref(null)
const drag = ref(null)
const dropTarget = ref(null)

onMounted(() => projects.load().catch((err) => ui.error(err)))

// ---------- фильтры и сортировка (запоминаются в браузере у каждого сотрудника) ----------
const filtersKey = `puls.dashboard.${auth.user?.id || 0}`
const filters = ref(readFilters())
function readFilters() {
  try { return normalizeProjectFilters(JSON.parse(localStorageGet(filtersKey) || 'null')) } catch { return { ...DEFAULT_PROJECT_FILTERS } }
}
watch(filters, (value) => localStorageSet(filtersKey, JSON.stringify(value)), { deep: true })
// Направление, которого больше нет (удалено или закрыт доступ), список не сужает
const directionFilter = computed(() => {
  const d = filters.value.direction
  return d && d !== 'none' && projects.loaded && !projects.directions.some((x) => String(x.id) === d) ? '' : d
})
const filtersActive = computed(() => projectFiltersActive({ ...filters.value, direction: directionFilter.value }))
function resetFilters() {
  filters.value = { ...DEFAULT_PROJECT_FILTERS, sort: filters.value.sort }
}
const stateCounts = computed(() => {
  const counts = {}
  for (const p of projects.projects) counts[projectState(p)] = (counts[projectState(p)] || 0) + 1
  return counts
})

const groups = computed(() => {
  const q = search.value.trim().toLowerCase()
  const f = { ...filters.value, direction: directionFilter.value }
  const narrowed = Boolean(q || f.state || f.mine)
  return projects.grouped
    .filter((g) => directionMatches(g.direction?.id ?? null, f.direction))
    .map((g) => ({
      ...g,
      projects: sortProjects(g.projects.filter((p) => (!q || p.title.toLowerCase().includes(q)) && matchesProjectFilters(p, f, auth.user)), f.sort),
    }))
    // Пустое направление видно только администратору: остальным направление без их проектов ни к чему
    .filter((g) => g.projects.length || (!narrowed && g.direction && auth.isAdmin))
})
const total = computed(() => projects.projects.length)
const query = computed(() => search.value.trim())
const found = computed(() => groups.value.reduce((n, g) => n + g.projects.length, 0))
// Перетаскивание — только в порядке по умолчанию, без поиска и фильтров: иначе место карточки неочевидно
const canReorder = computed(() => ['admin', 'manager'].includes(auth.user?.role) && !search.value && !filtersActive.value && filters.value.sort === 'order')

// Тот же показатель, что «Средний прогресс» в окне «Статистика» основного плана (первой вкладки)
const progress = projectProgress

// ---------- статистика основного плана, не входя в проект ----------
const statsPlanId = ref(null)
function mainPlanId(project) {
  return project.stats?.planId || project.plans[0]?.id || null
}
function openStats(project) {
  menu.value = null
  statsPlanId.value = mainPlanId(project)
}
function progressTitle(project) {
  const s = project.stats
  if (!s?.total) return 'Задач пока нет'
  const plan = s.planTitle && project.plans.length > 1 ? ` плана «${s.planTitle}»` : ''
  return `Средний прогресс задач${plan}: ${progress(project)}% · завершено ${s.done} из ${s.total}`
}

// Менеджер создаёт направления прямо из окна проекта (страница «Направления» — только администратору)
const NEW_DIRECTION = 'new'
async function pickDirection(event) {
  const value = event.target.value
  if (value !== NEW_DIRECTION) {
    editor.value.directionId = value === '' ? null : Number(value)
    return
  }
  event.target.value = editor.value.directionId ?? ''
  const name = await prompt({ title: 'Новое направление', label: 'Название', value: '', confirmText: 'Создать' })
  if (!name?.trim()) return
  try {
    const direction = await projects.createDirection(name.trim())
    if (editor.value) editor.value.directionId = direction.id
  } catch (err) {
    ui.error(err)
  }
}

function openCreate(directionId = null) {
  editor.value = { mode: 'create', title: '', subtitle: '', directionId }
}
function openEdit(project) {
  menu.value = null
  editor.value = { mode: 'edit', id: project.id, title: project.title, subtitle: project.subtitle, directionId: project.directionId }
}

async function saveEditor() {
  const e = editor.value
  if (!e.title.trim()) return
  saving.value = true
  try {
    if (e.mode === 'create') {
      const project = await projects.create({ title: e.title.trim(), subtitle: e.subtitle.trim(), directionId: e.directionId })
      editor.value = null
      router.push(`/projects/${project.id}`)
    } else {
      await projects.update(e.id, {
        title: e.title.trim(), subtitle: e.subtitle.trim(),
        directionId: e.directionId, moveToNoDirection: e.directionId === null,
      })
      editor.value = null
      ui.success('Проект сохранён')
    }
  } catch (err) {
    ui.error(err)
  } finally {
    saving.value = false
  }
}

async function removeProject(project) {
  menu.value = null
  const ok = await confirm({
    title: `Удалить проект «${project.title}»?`,
    message: 'Проект со всеми планами переместится в корзину. Администратор сможет вернуть его оттуда или удалить навсегда.',
    confirmText: 'В корзину',
  })
  if (!ok) return
  try {
    await projects.remove(project.id)
    ui.toast({
      title: `Проект «${project.title}» в корзине`, kind: 'success', timeout: 8000,
      action: { label: 'Отменить', handler: () => restoreProject(project) },
    })
  } catch (err) { ui.error(err) }
}

async function restoreProject(project) {
  try {
    await api.post(`/projects/${project.id}/restore`)
    await projects.load()
    ui.success('Проект восстановлен', project.title)
  } catch (err) { ui.error(err) }
}

function openMenu(event, project) {
  menu.value = { anchor: event.currentTarget, project }
}

// ---------- перетаскивание карточек между направлениями ----------
function canDrag(project) {
  return canReorder.value && project.access === 'manage'
}
function onDragStart(event, project) {
  if (!canDrag(project)) { event.preventDefault(); return }
  drag.value = project
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('text/plain', String(project.id))
}
function onDragOver(event, group, project = null) {
  if (!drag.value) return
  event.preventDefault()
  let before = true
  if (project) {
    const rect = event.currentTarget.getBoundingClientRect()
    before = event.clientX < rect.left + rect.width / 2
  }
  dropTarget.value = { groupId: group.direction?.id ?? null, projectId: project?.id ?? null, before }
}
async function onDrop(event, group) {
  event.preventDefault()
  const moving = drag.value
  const target = dropTarget.value
  drag.value = null
  dropTarget.value = null
  if (!moving || !target) return
  const list = (projects.grouped.find((g) => (g.direction?.id ?? null) === target.groupId)?.projects || [])
    .filter((p) => p.id !== moving.id)
  let index = list.length
  if (target.projectId) {
    index = list.findIndex((p) => p.id === target.projectId)
    if (!target.before) index += 1
  }
  list.splice(index, 0, moving)
  const items = list.map((p, i) => ({ id: p.id, directionId: target.groupId, position: i + 1 }))
    .filter((item) => item.id === moving.id || projects.byId[item.id]?.access === 'manage')
  try {
    await projects.reorder(items)
    if ((moving.directionId ?? null) !== target.groupId) {
      ui.success(`Проект перенесён в «${group.direction ? group.direction.name : 'Без направления'}»`)
    }
  } catch (err) {
    ui.error(err)
    projects.load()
  }
}
function onDragEnd() {
  drag.value = null
  dropTarget.value = null
}
</script>

<template>
  <div class="page projects-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Проекты</h1>
        <div class="page-subtitle">
          <template v-if="query || filtersActive">Найдено {{ found }} из {{ countLabel(total, 'проекта', 'проектов', 'проектов') }}</template>
          <template v-else>{{ countLabel(total, 'проект', 'проекта', 'проектов') }}</template>
        </div>
      </div>
      <HelpLink topic="projects" />
      <div class="spacer" />
      <label class="search">
        <AppIcon name="search" :size="15" class="search-icon" />
        <input v-model="search" class="input" type="search" placeholder="Поиск проекта" />
      </label>
      <button v-if="auth.canCreateProjects" class="btn btn-primary" type="button" data-testid="new-project" @click="openCreate()">
        <AppIcon name="plus" /> Новый проект
      </button>
    </div>

    <div v-if="total" class="dash-filters" data-testid="dashboard-filters">
      <select v-model="filters.direction" class="select" :class="{ 'is-set': directionFilter }" aria-label="Направление" data-testid="filter-direction">
        <option value="">Все направления</option>
        <option v-for="d in projects.directions" :key="d.id" :value="String(d.id)">{{ d.name }}</option>
        <option value="none">Без направления</option>
      </select>
      <select v-model="filters.state" class="select" :class="{ 'is-set': filters.state }" aria-label="Состояние" data-testid="filter-state">
        <option value="">Любое состояние</option>
        <option v-for="s in PROJECT_STATES" :key="s.value" :value="s.value" :title="s.hint">{{ s.label }} · {{ stateCounts[s.value] || 0 }}</option>
      </select>
      <button
        class="btn" :class="{ 'is-active': filters.mine }" type="button" :aria-pressed="filters.mine"
        :title="auth.isAdmin ? 'Проекты, которые вы создали' : 'Проекты, которые вы создали или можете редактировать'" data-testid="filter-mine-projects" @click="filters.mine = !filters.mine"
      >
        <AppIcon name="users" :size="15" /> Мои
      </button>
      <button v-if="filtersActive" class="btn btn-ghost" type="button" data-testid="dashboard-reset" @click="resetFilters">Сбросить</button>
      <span class="spacer" />
      <label class="sort">
        <AppIcon name="sort" :size="15" class="muted" />
        <select v-model="filters.sort" class="select" aria-label="Сортировка" data-testid="projects-sort">
          <option v-for="s in PROJECT_SORTS" :key="s.value" :value="s.value" :title="s.hint">{{ s.label }}</option>
        </select>
      </label>
    </div>

    <div v-if="projects.loading && !projects.loaded" class="skeleton-grid">
      <div v-for="n in 6" :key="n" class="card skeleton" />
    </div>

    <div v-else-if="!total" class="card empty-state">
      <h3>Проектов пока нет</h3>
      <p v-if="auth.canCreateProjects">Создайте первый проект — в нём появится план-диаграмма.</p>
      <p v-else>Администратор ещё не открыл вам доступ ни к одному проекту.</p>
      <button v-if="auth.canCreateProjects" class="btn btn-primary" type="button" @click="openCreate()">Новый проект</button>
    </div>

    <div v-else-if="(query || filtersActive) && !found" class="card empty-state" data-testid="projects-not-found">
      <h3>Ничего не найдено</h3>
      <p v-if="query">Нет проектов, в названии которых есть «{{ query }}»{{ filtersActive ? ', с выбранными фильтрами' : '' }}.</p>
      <p v-else>Нет проектов с выбранными фильтрами.</p>
      <div class="empty-actions">
        <button v-if="query" class="btn" type="button" @click="search = ''">Сбросить поиск</button>
        <button v-if="filtersActive" class="btn" type="button" @click="resetFilters">Сбросить фильтры</button>
      </div>
    </div>

    <section
      v-for="group in groups" :key="group.direction ? group.direction.id : 'none'" class="direction"
      @dragover="onDragOver($event, group)" @drop="onDrop($event, group)"
    >
      <header class="direction-header">
        <span class="direction-dot" :class="`c-${group.direction?.color || 'slate'}`" />
        <h2>{{ group.direction ? group.direction.name : 'Без направления' }}</h2>
        <span class="badge">{{ group.projects.length }}</span>
        <button v-if="auth.canCreateProjects" class="btn btn-ghost btn-sm add-here" type="button" title="Новый проект в этом направлении" @click="openCreate(group.direction?.id ?? null)">
          <AppIcon name="plus" :size="14" />
        </button>
      </header>
      <div class="grid">
        <article
          v-for="project in group.projects" :key="project.id" class="project-card card"
          :class="{
            'is-dragging': drag?.id === project.id,
            'drop-before': dropTarget?.projectId === project.id && dropTarget.before,
            'drop-after': dropTarget?.projectId === project.id && !dropTarget.before,
          }"
          :draggable="canDrag(project)" :data-testid="`project-${project.id}`"
          @dragstart="onDragStart($event, project)" @dragover.stop="onDragOver($event, group, project)"
          @drop.stop="onDrop($event, group)" @dragend="onDragEnd"
        >
          <router-link :to="`/projects/${project.id}`" class="card-link" :aria-label="project.title" draggable="false" />
          <div class="card-top">
            <h3 class="card-title">{{ project.title }}</h3>
            <button
              v-if="mainPlanId(project)" class="btn btn-ghost btn-icon btn-sm card-action" type="button" title="Статистика проекта"
              aria-label="Статистика проекта" :data-testid="`project-stats-${project.id}`" @click.stop="openStats(project)"
            >
              <AppIcon name="chart" />
            </button>
            <button v-if="project.access === 'manage'" class="btn btn-ghost btn-icon btn-sm card-menu" type="button" title="Действия" @click.stop="openMenu($event, project)">
              <AppIcon name="more" />
            </button>
          </div>
          <p v-if="project.subtitle" class="card-subtitle ellipsis">{{ project.subtitle }}</p>
          <div class="progress" :title="progressTitle(project)">
            <div class="progress-bar"><span :style="{ width: progress(project) + '%' }" /></div>
            <span class="progress-value">{{ project.stats?.total ? progress(project) + '%' : '—' }}</span>
          </div>
          <div class="card-stats">
            <span>{{ countLabel(project.stats?.total || 0, 'задача', 'задачи', 'задач') }}</span>
            <span v-if="project.stats?.inwork" class="stat stat-inwork">{{ project.stats.inwork }} в работе</span>
            <span v-if="project.stats?.overdue" class="stat stat-overdue">{{ countLabel(project.stats.overdue, 'просрочена', 'просрочены', 'просрочено') }}</span>
            <span v-if="project.stats?.blocked" class="stat stat-blocked">{{ countLabel(project.stats.blocked, 'заблокирована', 'заблокированы', 'заблокировано') }}</span>
          </div>
          <div v-if="project.stats?.nextMilestone" class="milestone" :title="project.stats.nextMilestone.name">
            <AppIcon name="diamond" :size="12" /> <span class="ellipsis">{{ formatDate(project.stats.nextMilestone.date) }} · {{ project.stats.nextMilestone.name }}</span>
          </div>
          <footer class="card-footer">
            <span class="badge" :class="project.access === 'view' ? '' : 'badge-primary'">{{ ACCESS_LABELS[project.access] }}</span>
            <span v-if="project.plans.length > 1" class="muted small">{{ countLabel(project.plans.length, 'план', 'плана', 'планов') }}</span>
            <span class="spacer" />
            <span class="muted small" :title="'Изменён ' + formatDateTime(project.updatedAt)">Изменён {{ formatDate(project.updatedAt) }}</span>
          </footer>
        </article>
        <div v-if="!group.projects.length" class="empty-drop muted small">
          {{ canReorder ? 'Перетащите сюда проект' : 'Нет проектов' }}
        </div>
      </div>
    </section>

    <PopupMenu v-if="menu" :anchor="menu.anchor" align="end" @close="menu = null">
      <button v-if="mainPlanId(menu.project)" class="menu-item" type="button" @click="openStats(menu.project)"><AppIcon name="chart" /> Статистика</button>
      <button class="menu-item" type="button" @click="openEdit(menu.project)"><AppIcon name="edit" /> Изменить</button>
      <button v-if="auth.isAdmin" class="menu-item is-danger" type="button" @click="removeProject(menu.project)"><AppIcon name="trash" /> Удалить</button>
    </PopupMenu>

    <StatsModal v-if="statsPlanId" :key="statsPlanId" :plan-id="statsPlanId" @close="statsPlanId = null" />

    <BaseModal v-if="editor" :title="editor.mode === 'create' ? 'Новый проект' : 'Проект'" @close="editor = null">
      <form class="form-stack" @submit.prevent="saveEditor">
        <label class="field">
          <span class="field-label">Название</span>
          <input v-model="editor.title" class="input" maxlength="500" placeholder="Например, «ИСУП — развитие 2027»" data-testid="project-title" />
        </label>
        <label class="field">
          <span class="field-label">Описание <span class="muted">(необязательно)</span></span>
          <input v-model="editor.subtitle" class="input" maxlength="1000" />
        </label>
        <label class="field">
          <span class="field-label">Направление</span>
          <select :value="editor.directionId ?? ''" class="select" data-testid="project-direction" @change="pickDirection">
            <option value="">Без направления</option>
            <option v-for="d in projects.directions" :key="d.id" :value="d.id">{{ d.name }}</option>
            <option v-if="auth.canCreateProjects" :value="NEW_DIRECTION">+ Новое направление…</option>
          </select>
        </label>
        <button type="submit" class="sr-only" tabindex="-1">ok</button>
      </form>
      <template #footer>
        <button class="btn btn-ghost" type="button" @click="editor = null">Отмена</button>
        <button class="btn btn-primary" type="button" :disabled="saving || !editor.title.trim()" data-testid="project-save" @click="saveEditor">
          {{ editor.mode === 'create' ? 'Создать' : 'Сохранить' }}
        </button>
      </template>
    </BaseModal>
  </div>
</template>

<style scoped>
.search { position: relative; width: min(280px, 100%); }
.search .input { padding-left: 30px; }
.search-icon { position: absolute; left: 9px; top: 9px; color: var(--color-text-muted); }
.direction { margin-bottom: 26px; }
.direction-header { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.direction-header h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-text-secondary); font-weight: 700; }
.direction-dot { width: 10px; height: 10px; border-radius: 3px; background: #94a3b8; }
.add-here { opacity: 0; transition: opacity 0.15s; }
.direction:hover .add-here { opacity: 1; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr)); gap: 12px; }
.project-card { position: relative; padding: 14px 16px 12px; display: flex; flex-direction: column; gap: 8px; transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s; }
.project-card:hover { border-color: #c9d6ff; box-shadow: var(--shadow-2); }
.project-card.is-dragging { opacity: 0.4; }
.project-card.drop-before { box-shadow: -3px 0 0 0 var(--color-primary); }
.project-card.drop-after { box-shadow: 3px 0 0 0 var(--color-primary); }
.card-link { position: absolute; inset: 0; z-index: 0; border-radius: inherit; }
.card-top { display: flex; align-items: flex-start; gap: 8px; }
.card-title { font-size: 14.5px; font-weight: 650; line-height: 1.3; flex: 1; min-width: 0; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.card-menu { position: relative; z-index: 1; margin: -4px -6px 0 0; }
.card-action { position: relative; z-index: 1; margin: -4px 0 0; color: var(--color-text-muted); }
.card-action:hover { color: var(--color-primary-dark); }
.dash-filters { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: -6px 0 18px; }
.dash-filters .select { width: auto; max-width: 240px; }
.dash-filters .btn.is-active:hover:not(:disabled) { background: var(--color-primary-soft); border-color: #a9bdff; }
.dash-filters .select.is-set { border-color: #c9d6ff; background-color: var(--color-primary-soft); color: var(--color-primary-dark); }
.sort { display: inline-flex; align-items: center; gap: 6px; }
.empty-actions { display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; }
.card-subtitle { margin: -4px 0 0; color: var(--color-text-muted); font-size: 12.5px; }
.progress { display: flex; align-items: center; gap: 8px; }
.progress-bar { flex: 1; height: 6px; border-radius: 999px; background: #eef1f7; overflow: hidden; }
.progress-bar span { display: block; height: 100%; background: linear-gradient(90deg, #2fa96b, #4cc38a); border-radius: inherit; }
.progress-value { font-size: 12px; font-weight: 600; color: var(--color-text-secondary); min-width: 32px; text-align: right; }
.card-stats { display: flex; flex-wrap: wrap; gap: 4px 10px; font-size: 12px; color: var(--color-text-secondary); }
.stat-inwork { color: #a8670f; }
.stat-overdue { color: #c62933; font-weight: 600; }
.stat-blocked { color: #c62933; }
.milestone { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #0f766e; }
.card-footer { display: flex; align-items: center; gap: 8px; margin-top: 2px; }
.empty-drop { border: 1px dashed var(--color-border-strong); border-radius: var(--radius-md); padding: 22px; text-align: center; }
.skeleton-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr)); gap: 12px; }
.skeleton { height: 150px; background: linear-gradient(90deg, #fff 0%, #f3f5fa 50%, #fff 100%); background-size: 200% 100%; animation: shimmer 1.2s infinite; }
@keyframes shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }
@media (max-width: 768px) {
  .search { width: auto; flex: 1 1 200px; }
  .grid, .skeleton-grid { grid-template-columns: 1fr; }
  .dash-filters { gap: 6px; }
  .dash-filters .select { flex: 1 1 150px; min-width: 0; max-width: none; min-height: 40px; }
  .dash-filters .btn { min-height: 40px; }
  .dash-filters .spacer { display: none; }
  .sort { flex: 1 1 150px; }
  .sort .select { flex: 1; }
}
@media (hover: none) { .add-here { opacity: 1; } }
.c-blue { background: #3b82f6; } .c-teal { background: #14b8a6; } .c-green { background: #22c55e; } .c-orange { background: #f97316; }
.c-red { background: #ef4444; } .c-purple { background: #8b5cf6; } .c-amber { background: #f59e0b; } .c-pink { background: #ec4899; }
.c-slate { background: #64748b; } .c-indigo { background: #6366f1; } .c-cyan { background: #06b6d4; } .c-lime { background: #84cc16; }
</style>
