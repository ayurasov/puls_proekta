<script setup>
import { computed, onMounted, provide, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { api } from '../api/client'
import { ACCESS_LABELS, useProjectsStore } from '../stores/projects'
import { localStorageGet, localStorageSet, useUiStore } from '../stores/ui'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/common/AppIcon.vue'
import BaseModal from '../components/common/BaseModal.vue'
import PlanTabs from '../gantt/components/PlanTabs.vue'
import GanttWorkspace from '../gantt/components/GanttWorkspace.vue'
import PodeluTasksModal from '../components/integrations/PodeluTasksModal.vue'
import { hasUnsavedEdits } from '../gantt/editor'
import { createPodeluTasks } from '../integrations/podeluTasks'

const route = useRoute()
const router = useRouter()
const ui = useUiStore()
const auth = useAuthStore()
const projectsStore = useProjectsStore()

const project = ref(null)
const loadError = ref(null)
const workspace = ref(null)
const renaming = ref(null)

const projectId = computed(() => Number(route.params.projectId))
const plans = computed(() => project.value?.plans || [])
const openPlans = computed(() => plans.value.filter((p) => !p.isArchived))
const archivedPlans = computed(() => plans.value.filter((p) => p.isArchived))
const activePlanId = computed(() => {
  const fromRoute = Number(route.params.planId)
  if (fromRoute && plans.value.some((p) => p.id === fromRoute)) return fromRoute
  const remembered = Number(localStorageGet(`puls.lastPlan.${projectId.value}`))
  if (remembered && openPlans.value.some((p) => p.id === remembered)) return remembered
  return openPlans.value[0]?.id || plans.value[0]?.id || null
})
const activePlan = computed(() => plans.value.find((p) => p.id === activePlanId.value) || null)
const canEditProject = computed(() => ['edit', 'manage'].includes(project.value?.access))
const canManage = computed(() => project.value?.access === 'manage')

async function loadProject() {
  loadError.value = null
  try {
    project.value = await api.get(`/projects/${projectId.value}`)
    document.title = `${project.value.title} · Пульс Проекта`
  } catch (err) {
    loadError.value = err
  }
}

onMounted(loadProject)

// Задачи «По Делу», которые ссылаются на проект и строки его планов (ADR-0010): кнопка в заголовке и карточка строки
const podeluTasks = createPodeluTasks(projectId.value)
provide('podeluTasks', podeluTasks)
onMounted(() => podeluTasks.load())
const podeluOpen = ref(false)
const planItems = computed(() => workspace.value?.editor?.state.items || {})
function revealPodeluItem({ planId, itemKey }) {
  if (!itemKey) return
  const target = planId || activePlanId.value
  if (target === activePlanId.value && workspace.value?.editor?.state.items[itemKey]) {
    workspace.value.editor.revealItem(itemKey)
    workspace.value.editor.select(itemKey, { openPanel: true })
  } else {
    router.push({ name: 'project', params: { projectId: projectId.value, planId: target }, query: { item: itemKey } })
  }
}

watch(activePlanId, (id) => { if (id) localStorageSet(`puls.lastPlan.${projectId.value}`, String(id)) })

function openPlan(id) {
  router.push({ name: 'project', params: { projectId: projectId.value, planId: id } })
}

async function planCreated(plan) {
  await loadProject()
  openPlan(plan.id)
}

function startRenameProject() {
  renaming.value = { title: project.value.title, subtitle: project.value.subtitle || '' }
}
async function saveProject() {
  if (!renaming.value.title.trim()) return
  try {
    await projectsStore.update(project.value.id, { title: renaming.value.title.trim(), subtitle: renaming.value.subtitle.trim() })
    project.value.title = renaming.value.title.trim()
    project.value.subtitle = renaming.value.subtitle.trim()
    renaming.value = null
  } catch (err) { ui.error(err) }
}

// Уход с плана не теряет правок: закрытый план досылает их в фоне, а закрыть страницу при
// неотправленных правках не даёт предупреждение браузера (gantt/editor.js, hasUnsavedEdits).
function leaveGuard() {
  if (hasUnsavedEdits() && workspace.value?.editor?.state.offline) {
    ui.toast({ title: 'Изменения отправятся, когда появится связь', body: 'Не закрывайте вкладку до отправки.', kind: 'info', timeout: 6000 })
  }
  return true
}
onBeforeRouteLeave(leaveGuard)
onBeforeRouteUpdate(leaveGuard)
</script>

<template>
  <div class="project-view">
    <div v-if="loadError" class="page">
      <div class="card empty-state">
        <h3>{{ loadError.status === 404 ? 'Проект не найден' : 'Не удалось открыть проект' }}</h3>
        <p>{{ loadError.status === 404 ? 'Возможно, он удалён или у вас нет доступа.' : loadError.message }}</p>
        <div style="display:flex;gap:8px;justify-content:center">
          <button v-if="loadError.status !== 404" class="btn" type="button" @click="loadProject">Повторить</button>
          <router-link to="/" class="btn btn-primary">К проектам</router-link>
        </div>
      </div>
    </div>
    <template v-else-if="project">
      <header class="project-header">
        <router-link to="/" class="btn btn-ghost btn-icon" title="К проектам"><AppIcon name="chevronLeft" /></router-link>
        <div class="titles">
          <div class="title-line">
            <h1 class="project-title" :title="project.title">{{ project.title }}</h1>
            <button v-if="canManage" class="btn btn-ghost btn-icon btn-sm" type="button" title="Переименовать проект" @click="startRenameProject">
              <AppIcon name="edit" :size="14" />
            </button>
          </div>
          <div class="meta muted small" :title="[project.directionName || 'Без направления', ACCESS_LABELS[project.access], project.subtitle].filter(Boolean).join(' · ')">
            <span class="meta-part">{{ project.directionName || 'Без направления' }}</span>
            <span class="dot">·</span>
            <span class="meta-part" :class="{ 'access-view': project.access === 'view' }">{{ ACCESS_LABELS[project.access] }}</span>
            <span v-if="project.subtitle" class="dot">·</span>
            <span v-if="project.subtitle" class="meta-part subtitle">{{ project.subtitle }}</span>
          </div>
        </div>
        <button
          v-if="podeluTasks.state.enabled" class="btn btn-sm podelu-btn" :class="{ 'has-open': podeluTasks.openCount }" type="button"
          :title="podeluTasks.state.error || `Задачи в «По Делу», связанные с проектом: ${podeluTasks.state.tasks.length}`" data-testid="podelu-tasks-open"
          @click="podeluOpen = true"
        >
          <span class="podelu-mark" aria-hidden="true"><AppIcon name="checklist" :size="12" :stroke-width="2.4" /></span>
          <span class="podelu-label">По Делу</span>
          <span v-if="podeluTasks.state.tasks.length" class="podelu-count">{{ podeluTasks.openCount }}<span v-if="podeluTasks.openCount !== podeluTasks.state.tasks.length" class="of">/{{ podeluTasks.state.tasks.length }}</span></span>
          <AppIcon v-if="podeluTasks.state.error" name="warning" :size="13" class="podelu-warn" />
        </button>
        <div id="project-header-slot" class="header-slot" />
      </header>
      <PlanTabs
        :project="project" :open-plans="openPlans" :archived-plans="archivedPlans" :active-plan-id="activePlanId"
        :can-edit="canEditProject && auth.user?.role !== 'viewer'" :can-manage="canManage"
        @open="openPlan" @created="planCreated" @changed="loadProject"
      />
      <GanttWorkspace
        v-if="activePlan" :key="activePlan.id" ref="workspace" :plan-id="activePlan.id" :project="project"
        @plan-meta="loadProject"
      />
      <div v-else class="page"><div class="card empty-state"><h3>В проекте нет планов</h3></div></div>
    </template>
    <div v-else class="loading muted">Загрузка проекта…</div>

    <PodeluTasksModal
      v-if="podeluOpen && project" :tasks="podeluTasks" :items="planItems" :plan-id="activePlanId" :project-title="project.title"
      @close="podeluOpen = false" @reveal="revealPodeluItem"
    />
    <BaseModal v-if="renaming" title="Проект" @close="renaming = null">
      <form class="form-stack" @submit.prevent="saveProject">
        <label class="field"><span class="field-label">Название</span><input v-model="renaming.title" class="input" maxlength="500" /></label>
        <label class="field"><span class="field-label">Описание</span><input v-model="renaming.subtitle" class="input" maxlength="1000" /></label>
        <button type="submit" class="sr-only" tabindex="-1">ok</button>
      </form>
      <template #footer>
        <button class="btn btn-ghost" type="button" @click="renaming = null">Отмена</button>
        <button class="btn btn-primary" type="button" :disabled="!renaming.title.trim()" @click="saveProject">Сохранить</button>
      </template>
    </BaseModal>
  </div>
</template>

<style scoped>
.project-view { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.project-header { display: flex; align-items: center; gap: 8px; padding: 10px 16px 6px 10px; background: var(--color-surface); flex-shrink: 0; }
.titles { min-width: 0; flex: 1 1 auto; display: flex; flex-direction: column; }
.title-line { display: flex; align-items: center; gap: 4px; min-width: 0; }
.project-title { font-size: 17px; font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* Строка под названием не переносится по словам: части целиком, описание обрезается многоточием */
.meta { display: flex; align-items: center; gap: 6px; min-width: 0; white-space: nowrap; overflow: hidden; }
.meta-part { flex-shrink: 0; }
.meta-part.subtitle { flex-shrink: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.dot { opacity: 0.6; flex-shrink: 0; }
.access-view { color: #a8670f; }
.podelu-btn { gap: 6px; border-radius: 999px; padding: 3px 10px 3px 4px; flex-shrink: 0; }
.podelu-mark { width: 20px; height: 20px; border-radius: 50%; display: grid; place-items: center; background: linear-gradient(140deg, #34c38f, #15803d); color: #fff; }
.podelu-label { font-weight: 600; }
.podelu-count { min-width: 20px; padding: 0 7px; border-radius: 999px; background: #eef1f7; color: var(--color-text-secondary); font-size: 11.5px; font-weight: 700; text-align: center; }
.podelu-btn.has-open .podelu-count { background: #dcfff1; color: #216e4e; }
.of { opacity: 0.6; font-weight: 600; }
.podelu-warn { color: #b45309; }
@media (max-width: 768px) { .podelu-label { display: none; } }
.header-slot { display: flex; align-items: center; gap: 10px; flex-shrink: 0; min-width: 0; }
@media (max-width: 768px) {
  .project-header { padding: 8px 10px 6px 60px; min-height: 60px; }
  .project-header > .btn-icon:first-child { display: none; }
  .project-title { font-size: 16px; }
}
.loading { padding: 40px; text-align: center; }
</style>
