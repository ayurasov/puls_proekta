<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

/**
 * Всплывающее меню в точке (контекстное меню) или под элементом.
 * Закрывается по щелчку вне меню и Esc; не выходит за края окна. Меню под элементом следует за ним,
 * когда раскладка меняется (например, под панелью появилась строка фильтров) — иначе фильтры
 * закрывались бы после первой же отметки. Меню в точке закрывается при прокрутке и изменении окна.
 */
const props = defineProps({
  x: { type: Number, default: 0 },
  y: { type: Number, default: 0 },
  anchor: { type: Object, default: null },
  align: { type: String, default: 'start' },
  minWidth: { type: Number, default: 200 },
})
const emit = defineEmits(['close'])
const menu = ref(null)
const style = ref({ left: '0px', top: '0px', visibility: 'hidden' })

async function place(initial = false) {
  await nextTick()
  const el = menu.value
  if (!el) return
  let left = props.x
  let top = props.y
  if (props.anchor) {
    const rect = props.anchor.getBoundingClientRect()
    left = props.align === 'end' ? rect.right - el.offsetWidth : rect.left
    top = rect.bottom + 4
  }
  const maxLeft = window.innerWidth - el.offsetWidth - 8
  const maxTop = window.innerHeight - el.offsetHeight - 8
  if (top > maxTop && props.anchor) top = props.anchor.getBoundingClientRect().top - el.offsetHeight - 4
  style.value = { left: Math.max(8, Math.min(left, maxLeft)) + 'px', top: Math.max(8, Math.min(top, maxTop)) + 'px', minWidth: props.minWidth + 'px' }
  if (initial === true) el.querySelector('.menu-item:not(:disabled)')?.focus({ preventScroll: true })
}

function onPointer(event) {
  if (menu.value && !menu.value.contains(event.target) && !(props.anchor && props.anchor.contains(event.target))) emit('close')
}
function onKey(event) {
  if (event.key === 'Escape') { event.stopPropagation(); emit('close'); return }
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    const items = [...(menu.value?.querySelectorAll('.menu-item:not(:disabled)') || [])]
    const index = items.indexOf(document.activeElement)
    const next = event.key === 'ArrowDown' ? (index + 1) % items.length : (index - 1 + items.length) % items.length
    items[next]?.focus()
  }
}
function onScroll(event) {
  if (!props.anchor && menu.value && !menu.value.contains(event.target)) emit('close')
}
function onResize() {
  if (props.anchor) place()
  else emit('close')
}

// Следим за положением элемента, к которому привязано меню, пока меню открыто
let frame = 0
let lastRect = ''
function follow() {
  const rect = props.anchor?.getBoundingClientRect()
  if (rect) {
    if (!props.anchor.isConnected || (rect.width === 0 && rect.height === 0)) { emit('close'); return }
    const key = `${rect.left}:${rect.top}:${rect.right}:${rect.bottom}`
    if (lastRect && key !== lastRect) place()
    lastRect = key
  }
  frame = requestAnimationFrame(follow)
}

watch(() => [props.x, props.y, props.anchor], () => place(true))
let listenTimer = 0
onMounted(() => {
  place(true)
  // Щелчок, открывший меню, не должен сразу его закрыть — подписка со следующего такта
  listenTimer = setTimeout(() => {
    document.addEventListener('mousedown', onPointer, true)
    document.addEventListener('contextmenu', onPointer, true)
  })
  document.addEventListener('keydown', onKey, true)
  window.addEventListener('scroll', onScroll, true)
  window.addEventListener('resize', onResize)
  if (props.anchor) frame = requestAnimationFrame(follow)
})
onBeforeUnmount(() => {
  clearTimeout(listenTimer)
  cancelAnimationFrame(frame)
  document.removeEventListener('mousedown', onPointer, true)
  document.removeEventListener('contextmenu', onPointer, true)
  document.removeEventListener('keydown', onKey, true)
  window.removeEventListener('scroll', onScroll, true)
  window.removeEventListener('resize', onResize)
})
</script>

<template>
  <Teleport to="body">
    <div ref="menu" class="menu" role="menu" :style="style" @click.stop @contextmenu.prevent>
      <slot />
    </div>
  </Teleport>
</template>
