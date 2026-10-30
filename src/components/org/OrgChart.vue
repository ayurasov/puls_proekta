<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import OrgChartBranch from './OrgChartBranch.vue'
import { ZOOM_RANGE } from '../../utils/orgViews'

/**
 * Холст оргсхемы (как в todo-planner, ADR-0046): лес узлов карточками с линиями подчинения сверху вниз,
 * масштаб (кнопки и Ctrl+колесо), перетаскивание фона мышью, «вписать в экран», прокрутка к узлу.
 * Схема прокручивается внутри своей рамки — страница вбок не уезжает. Карточку рисует родитель через слот.
 * Рисуются только раскрытые ветки — большая структура остаётся лёгкой.
 */
const props = defineProps({
  forest: { type: Array, required: true },
  expanded: { type: Object, required: true },
  path: { type: Object, default: () => new Set() },
  zoom: { type: Number, default: 1 },
  label: { type: String, default: 'Оргсхема' },
})
const emit = defineEmits(['update:zoom'])

const viewport = ref(null)
const canvas = ref(null)
const size = ref({ width: 0, height: 0, view: 0 })
// Узкая схема — по центру рамки, широкая — от левого края с прокруткой
const offset = computed(() => Math.max(0, Math.floor((size.value.view - size.value.width) / 2)))
const canvasStyle = computed(() => ({ transform: `scale(${props.zoom})`, left: `${offset.value}px` }))

let observer = null
function measure() {
  if (!canvas.value || !viewport.value) return
  size.value = { width: canvas.value.offsetWidth * props.zoom, height: canvas.value.offsetHeight * props.zoom, view: viewport.value.clientWidth }
}
watch(canvas, (el) => {
  observer?.disconnect()
  if (el && typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(measure)
    observer.observe(el)
    observer.observe(viewport.value)
  }
  measure()
}, { flush: 'post' })
watch(() => props.zoom, () => nextTick(measure))
onBeforeUnmount(() => observer?.disconnect())

const reduceMotion = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/** Масштаб, при котором схема видна целиком по ширине (но не мельче, чем можно прочесть). */
function fit() {
  if (!viewport.value || !canvas.value) return
  const width = canvas.value.offsetWidth || 1
  const height = canvas.value.offsetHeight || 1
  const scale = Math.min(1, (viewport.value.clientWidth - 16) / width, Math.max(0.5, (viewport.value.clientHeight - 16) / height))
  emit('update:zoom', Math.max(ZOOM_RANGE[0], Math.floor(scale * 100) / 100))
  nextTick(() => { viewport.value?.scrollTo({ left: 0, top: 0 }) })
}

/**
 * Прокрутить к узлу, если его не видно целиком; focus — перевести на него фокус клавиатуры.
 * Вызывается после раскрытия веток: ждём отрисовку и пересчёт размера холста.
 */
async function scrollTo(id, { focus = false } = {}) {
  await nextTick()
  measure()
  await nextTick()
  const el = viewport.value?.querySelector(`[data-node-id="${CSS.escape(String(id))}"]`)
  if (!el) return
  const box = el.getBoundingClientRect()
  const view = viewport.value.getBoundingClientRect()
  const visible = box.left >= view.left && box.right <= view.right && box.top >= view.top && box.bottom <= view.bottom
  if (!visible) {
    viewport.value.scrollTo({
      left: viewport.value.scrollLeft + box.left - view.left - view.width / 2 + box.width / 2,
      top: viewport.value.scrollTop + box.top - view.top - Math.min(view.height / 3, 80),
      behavior: reduceMotion() ? 'auto' : 'smooth',
    })
  }
  if (focus) el.querySelector('.card-main')?.focus({ preventScroll: true })
}

// Перетаскивание холста мышью за фон; на телефоне — обычная прокрутка пальцем
let drag = null
function onPointerDown(event) {
  if (event.pointerType !== 'mouse' || event.button !== 0 || event.target.closest('button, a, input, select, [data-node-id]')) return
  drag = { x: event.clientX, y: event.clientY, left: viewport.value.scrollLeft, top: viewport.value.scrollTop }
  viewport.value.setPointerCapture?.(event.pointerId)
  viewport.value.classList.add('oc-viewport--drag')
}
function onPointerMove(event) {
  if (!drag) return
  viewport.value.scrollLeft = drag.left - (event.clientX - drag.x)
  viewport.value.scrollTop = drag.top - (event.clientY - drag.y)
}
function onPointerUp() {
  drag = null
  viewport.value?.classList.remove('oc-viewport--drag')
}
function onWheel(event) {
  if (!event.ctrlKey && !event.metaKey) return
  event.preventDefault()
  const next = Math.min(ZOOM_RANGE[1], Math.max(ZOOM_RANGE[0], props.zoom - Math.sign(event.deltaY) * 0.1))
  emit('update:zoom', Math.round(next * 10) / 10)
}

defineExpose({ fit, scrollTo })
</script>

<template>
  <div
    ref="viewport" class="oc-viewport scroll-thin" tabindex="0" role="region" :aria-label="`${label}: прокрутка стрелками, перетаскивание мышью, Ctrl+колесо — масштаб`"
    @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp" @pointercancel="onPointerUp" @wheel="onWheel"
  >
    <div class="oc-spacer" :style="{ width: `${size.width + offset}px`, height: `${size.height}px` }">
      <div ref="canvas" class="oc-canvas" :style="canvasStyle">
        <ul class="oc-level oc-root">
          <OrgChartBranch v-for="node in forest" :key="node.id" :node="node" :expanded="expanded" :path="path">
            <template #default="slotProps"><slot v-bind="slotProps" /></template>
          </OrgChartBranch>
        </ul>
      </div>
    </div>
  </div>
</template>

<style>
/* Линии рисует CSS (как в todo-planner): у каждого узла — уголки к соседям и ствол от родителя */
.oc-viewport {
  --oc-line: var(--color-border-strong); --oc-path: var(--color-primary); --oc-gap: 24px;
  position: relative; overflow: auto; cursor: grab; overscroll-behavior: contain;
  background: radial-gradient(circle, var(--color-border) 1px, transparent 1px) 0 0 / 22px 22px, var(--color-surface-2);
  border: 1px solid var(--color-border); border-radius: var(--radius-md);
}
.oc-viewport:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }
.oc-viewport--drag { cursor: grabbing; user-select: none; }
.oc-spacer { position: relative; min-width: 100%; }
.oc-canvas { position: absolute; top: 0; left: 0; transform-origin: 0 0; padding: 20px; width: max-content; }
.oc-level { display: flex; margin: 0; padding: var(--oc-gap) 0 0; position: relative; list-style: none; }
.oc-root { padding-top: 0; gap: 18px; }
.oc-node { list-style: none; position: relative; display: flex; flex-direction: column; align-items: center; padding: var(--oc-gap) 8px 0; }
.oc-card-slot { position: relative; z-index: 1; cursor: default; }

.oc-node::before, .oc-node::after { content: ''; position: absolute; top: 0; right: 50%; width: 50%; height: var(--oc-gap); border-top: 1.5px solid var(--oc-line); }
.oc-node::after { right: auto; left: 50%; border-left: 1.5px solid var(--oc-line); }
.oc-node:only-child::before, .oc-node:only-child::after { border-top: 0; }
.oc-node:first-child::before, .oc-node:last-child::after { border: 0; }
/* Правая граница последнего узла — ровно по центру, как левые у остальных: без ступеньки на линии */
.oc-node:last-child::before { right: calc(50% - 1.5px); width: calc(50% + 1.5px); border-right: 1.5px solid var(--oc-line); border-radius: 0 10px 0 0; }
.oc-node:first-child::after { border-radius: 10px 0 0 0; }
/* Единственный дочерний — прямая линия вниз от ствола родителя */
.oc-node:only-child::before { display: none; }
.oc-node:only-child::after { border: 0; border-left: 1.5px solid var(--oc-line); border-radius: 0; }
.oc-level .oc-level::before { content: ''; position: absolute; top: 0; left: 50%; height: var(--oc-gap); border-left: 1.5px solid var(--oc-line); }
.oc-root > .oc-node { padding: 0; }
.oc-root > .oc-node::before, .oc-root > .oc-node::after { display: none; }
/* Путь к выбранному — по вертикалям: ствол родителя и спуск к узлу; общая шина соседей остаётся серой
   (иначе подсветка обрывалась бы на чужих отрезках шины) */
.oc-level--path::before, .oc-node--path::after { border-left-color: var(--oc-path) !important; border-left-width: 2px; }
.oc-node--path:last-child:not(:only-child)::before { border-right-color: var(--oc-path) !important; border-right-width: 2px; }

/* Нижний уровень — столбиком справа от ствола: схема не растёт вширь */
.oc-level--stack { display: grid; grid-template-columns: 1fr 28px 1fr; align-items: start; }
.oc-level--stack > .oc-node { grid-column: 3; padding: 10px 0 0; align-items: flex-start; }
.oc-level--stack > .oc-node:first-child { padding-top: 0; }
.oc-level--stack > .oc-node::before { top: -10px; right: auto; left: -14px; width: 14px; height: calc(50% + 10px); border: 0; border-left: 1.5px solid var(--oc-line); border-bottom: 1.5px solid var(--oc-line); border-radius: 0 0 0 8px; }
.oc-level--stack > .oc-node:first-child::before { top: 0; height: 50%; }
.oc-level--stack > .oc-node::after { display: none; }
.oc-level--stack > .oc-node--path::before, .oc-level--stack > .oc-node:has(~ .oc-node--path)::before { border-left-color: var(--oc-path) !important; }
.oc-level--stack > .oc-node--path::before { border-bottom-color: var(--oc-path) !important; }
</style>
