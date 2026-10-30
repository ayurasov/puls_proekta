<script setup>
import { nextTick, ref } from 'vue'
import { api, saveBlob } from '../../api/client'
import { useUiStore } from '../../stores/ui'
import { useDialogs } from '../../composables/useDialogs'
import AppIcon from '../../components/common/AppIcon.vue'
import PopupMenu from '../../components/common/PopupMenu.vue'

/**
 * Вкладки планов проекта (в 1.0.x — «версии»). Закрытая вкладка уходит в архив — данные
 * не теряются (в 1.0.x закрытая вкладка исчезала при следующем сохранении).
 */
const props = defineProps({
  project: { type: Object, required: true },
  openPlans: { type: Array, required: true },
  archivedPlans: { type: Array, required: true },
  activePlanId: { type: Number, default: null },
  canEdit: { type: Boolean, default: false },
  canManage: { type: Boolean, default: false },
})
const emit = defineEmits(['open', 'created', 'changed'])
const ui = useUiStore()
const { confirm, prompt } = useDialogs()

const menu = ref(null)
const renamingId = ref(null)
const renameValue = ref('')
const fileInput = ref(null)
const dragId = ref(null)
const dropBefore = ref(null)

function openMenu(type, event, plan = null) {
  menu.value = { type, plan, anchor: event.currentTarget, x: event.clientX, y: event.clientY, point: event.type === 'contextmenu' }
}

async function startRename(plan) {
  menu.value = null
  if (!props.canEdit) return
  renamingId.value = plan.id
  renameValue.value = plan.title
  await nextTick()
  document.querySelector('.plan-tab input')?.select()
}

async function finishRename(plan, save = true) {
  const id = renamingId.value
  renamingId.value = null
  const title = renameValue.value.trim()
  if (!save || !id || !title || title === plan.title) return
  try {
    await api.patch(`/plans/${plan.id}`, { title })
    plan.title = title
    emit('changed')
  } catch (err) { ui.error(err) }
}

async function createPlan(mode) {
  menu.value = null
  try {
    if (mode === 'blank') {
      const title = await prompt({ title: 'Новый план', label: 'Название плана', value: 'Новый план', confirmText: 'Создать' })
      if (!title) return
      emit('created', await api.post(`/projects/${props.project.id}/plans`, { title }))
    } else if (mode === 'copy') {
      const source = props.openPlans.find((p) => p.id === props.activePlanId)
      if (!source) return
      emit('created', await api.post(`/projects/${props.project.id}/plans`, { sourcePlanId: source.id }))
      ui.success(`Создана копия «${source.title}»`)
    } else if (mode === 'import') {
      fileInput.value.click()
    }
  } catch (err) { ui.error(err) }
}

async function onImportFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  let document
  try {
    document = JSON.parse(await file.text())
  } catch {
    ui.error('Файл не является JSON')
    return
  }
  try {
    const plan = await api.post(`/projects/${props.project.id}/plans`, { document, title: file.name.replace(/\.json$/i, '') })
    ui.success('План импортирован', `«${plan.title}» — новая вкладка`)
    emit('created', plan)
  } catch (err) { ui.error(err) }
}

async function duplicate(plan) {
  menu.value = null
  try {
    emit('created', await api.post(`/projects/${props.project.id}/plans`, { sourcePlanId: plan.id }))
  } catch (err) { ui.error(err) }
}

async function exportPlan(plan) {
  menu.value = null
  try {
    const state = await api.get(`/plans/${plan.id}/export`)
    const name = `${plan.title.replace(/[\\/:*?"<>|]+/g, '_')}.json`
    saveBlob(new Blob([JSON.stringify(state, null, 2)], { type: 'application/json' }), name)
  } catch (err) { ui.error(err) }
}

async function archive(plan) {
  menu.value = null
  const ok = await confirm({
    title: `Убрать план «${plan.title}» в архив?`,
    message: 'План пропадёт из вкладок, но сохранится полностью. Вернуть его можно из меню «Архив».',
    confirmText: 'В архив', danger: false,
  })
  if (!ok) return
  try {
    await api.post(`/plans/${plan.id}/archive`)
    emit('changed')
    if (plan.id === props.activePlanId) {
      const next = props.openPlans.find((p) => p.id !== plan.id)
      if (next) emit('open', next.id)
    }
    ui.toast({ title: 'План в архиве', kind: 'success', action: { label: 'Вернуть', handler: () => unarchive(plan) } })
  } catch (err) { ui.error(err) }
}

async function unarchive(plan) {
  menu.value = null
  try {
    await api.post(`/plans/${plan.id}/unarchive`)
    emit('changed')
    emit('open', plan.id)
  } catch (err) { ui.error(err) }
}

async function removePlan(plan) {
  menu.value = null
  const ok = await confirm({
    title: `Удалить план «${plan.title}»?`,
    message: 'План переместится в корзину. Администратор сможет вернуть его или удалить навсегда.',
    confirmText: 'В корзину',
  })
  if (!ok) return
  try {
    await api.delete(`/plans/${plan.id}`)
    emit('changed')
    ui.success('План в корзине')
  } catch (err) { ui.error(err) }
}

function onDragStart(event, plan) {
  if (!props.canEdit) { event.preventDefault(); return }
  dragId.value = plan.id
  event.dataTransfer.effectAllowed = 'move'
}
function onDragOver(event, plan) {
  if (!dragId.value) return
  event.preventDefault()
  const rect = event.currentTarget.getBoundingClientRect()
  dropBefore.value = { id: plan.id, before: event.clientX < rect.left + rect.width / 2 }
}
async function onDrop() {
  const moving = dragId.value
  const target = dropBefore.value
  dragId.value = null
  dropBefore.value = null
  if (!moving || !target || moving === target.id) return
  const ids = props.openPlans.map((p) => p.id).filter((id) => id !== moving)
  let index = ids.indexOf(target.id)
  if (!target.before) index += 1
  ids.splice(index, 0, moving)
  const all = [...ids, ...props.archivedPlans.map((p) => p.id)]
  try {
    await api.post(`/projects/${props.project.id}/plans/reorder`, { ids: all })
    emit('changed')
  } catch (err) { ui.error(err) }
}
</script>

<template>
  <div class="plan-tabs" role="tablist" aria-label="Планы проекта">
    <div class="tabs-scroll scroll-thin">
      <div
        v-for="plan in openPlans" :key="plan.id" class="plan-tab" role="tab"
        :class="{ 'is-active': plan.id === activePlanId, 'drop-before': dropBefore?.id === plan.id && dropBefore.before, 'drop-after': dropBefore?.id === plan.id && !dropBefore.before }"
        :aria-selected="plan.id === activePlanId" :draggable="canEdit && renamingId !== plan.id"
        :title="plan.title" @dragstart="onDragStart($event, plan)" @dragover="onDragOver($event, plan)" @drop="onDrop"
        @dragend="dragId = null; dropBefore = null" @contextmenu.prevent="openMenu('tab', $event, plan)"
      >
        <input
          v-if="renamingId === plan.id" v-model="renameValue" class="input input-sm" maxlength="500"
          @keydown.enter.prevent="finishRename(plan)" @keydown.esc.prevent="finishRename(plan, false)" @blur="finishRename(plan)"
        />
        <button v-else class="tab-button" type="button" @click="emit('open', plan.id)" @dblclick="startRename(plan)">
          <span class="ellipsis">{{ plan.title }}</span>
        </button>
        <button v-if="plan.id === activePlanId && renamingId !== plan.id" class="tab-menu" type="button" title="Действия с планом" @click.stop="openMenu('tab', $event, plan)">
          <AppIcon name="chevronDown" :size="13" />
        </button>
      </div>
      <button v-if="canEdit" class="tab-add btn btn-ghost btn-sm" type="button" title="Новый план" data-testid="plan-add" @click="openMenu('add', $event)">
        <AppIcon name="plus" :size="14" />
      </button>
    </div>
    <button v-if="archivedPlans.length" class="btn btn-ghost btn-sm archive-btn" type="button" @click="openMenu('archive', $event)">
      <AppIcon name="archive" :size="14" /> Архив · {{ archivedPlans.length }}
    </button>
    <div id="plan-actions-slot" class="plan-actions" />
    <input ref="fileInput" type="file" accept=".json,application/json" hidden @change="onImportFile" />

    <PopupMenu v-if="menu?.type === 'add'" :anchor="menu.anchor" @close="menu = null">
      <button class="menu-item" type="button" @click="createPlan('blank')"><AppIcon name="plus" /> Новый пустой план</button>
      <button class="menu-item" type="button" @click="createPlan('copy')"><AppIcon name="copy" /> Копия текущего плана</button>
      <button class="menu-item" type="button" @click="createPlan('import')"><AppIcon name="upload" /> Импорт из JSON-файла…</button>
    </PopupMenu>

    <PopupMenu v-if="menu?.type === 'tab'" :x="menu.x" :y="menu.y" :anchor="menu.point ? null : menu.anchor" @close="menu = null">
      <button v-if="menu.plan.id !== activePlanId" class="menu-item" type="button" @click="emit('open', menu.plan.id); menu = null"><AppIcon name="folder" /> Открыть</button>
      <button v-if="canEdit" class="menu-item" type="button" @click="startRename(menu.plan)"><AppIcon name="edit" /> Переименовать<span class="menu-hint">двойной щелчок</span></button>
      <button v-if="canEdit" class="menu-item" type="button" @click="duplicate(menu.plan)"><AppIcon name="copy" /> Дублировать</button>
      <button class="menu-item" type="button" @click="exportPlan(menu.plan)"><AppIcon name="download" /> Экспорт JSON</button>
      <template v-if="canEdit && openPlans.length > 1">
        <div class="menu-separator" />
        <button class="menu-item" type="button" @click="archive(menu.plan)"><AppIcon name="archive" /> Убрать в архив</button>
      </template>
    </PopupMenu>

    <PopupMenu v-if="menu?.type === 'archive'" :anchor="menu.anchor" align="end" :min-width="300" @close="menu = null">
      <div class="menu-label">Архивные планы</div>
      <div v-for="plan in archivedPlans" :key="plan.id" class="archived-row">
        <button class="menu-item" type="button" @click="emit('open', plan.id); menu = null">
          <AppIcon name="archive" /> <span class="ellipsis">{{ plan.title }}</span>
        </button>
        <button v-if="canEdit" class="btn btn-ghost btn-sm" type="button" title="Вернуть во вкладки" @click="unarchive(plan)"><AppIcon name="unarchive" :size="14" /></button>
        <button v-if="canManage" class="btn btn-ghost btn-sm btn-text-danger" type="button" title="Удалить" @click="removePlan(plan)"><AppIcon name="trash" :size="14" /></button>
      </div>
    </PopupMenu>
  </div>
</template>

<style scoped>
.plan-tabs { display: flex; align-items: flex-end; gap: 8px; padding: 0 12px; background: var(--color-surface); border-bottom: 1px solid var(--color-border); flex-shrink: 0; min-height: 38px; }
.plan-actions { display: flex; align-items: center; gap: 2px; align-self: center; flex-shrink: 0; }
.plan-actions:empty { display: none; }
.tabs-scroll { display: flex; align-items: flex-end; gap: 2px; overflow-x: auto; min-width: 0; flex: 1; }
.plan-tab { display: flex; align-items: center; gap: 2px; max-width: 280px; border-bottom: 2px solid transparent; padding: 0 2px; flex-shrink: 0; }
.plan-tab.is-active { border-bottom-color: var(--color-primary); }
.plan-tab.drop-before { box-shadow: inset 2px 0 0 var(--color-primary); }
.plan-tab.drop-after { box-shadow: inset -2px 0 0 var(--color-primary); }
.tab-button { border: 0; background: transparent; padding: 9px 10px 8px; font-size: 13px; color: var(--color-text-secondary); font-weight: 500; max-width: 250px; display: flex; border-radius: 6px 6px 0 0; }
.tab-button:hover { background: var(--color-hover); color: var(--color-text); }
.plan-tab.is-active .tab-button { color: var(--color-primary-dark); font-weight: 650; }
.tab-menu { border: 0; background: transparent; color: var(--color-text-muted); padding: 4px; border-radius: 4px; margin-bottom: 4px; }
.tab-menu:hover { background: var(--color-hover); color: var(--color-text); }
.plan-tab .input { margin: 4px 0 6px; width: 220px; }
.tab-add { margin-bottom: 4px; }
.archive-btn { align-self: center; flex-shrink: 0; color: var(--color-text-secondary); }
.archived-row { display: flex; align-items: center; gap: 2px; }
.archived-row .menu-item { flex: 1; min-width: 0; }
</style>
