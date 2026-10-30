<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'

/**
 * Модальное окно: закрывается по Esc, крестику и щелчку по фону (если щелчок начался
 * на фоне — выделение текста мышью окно не закрывает). Фокус уходит в первое поле, иначе на
 * основную кнопку. В окне с опасным действием (кнопка .btn-danger) фокус — на «Отмене»:
 * случайный Enter не удаляет.
 */
const props = defineProps({
  title: { type: String, default: '' },
  size: { type: String, default: 'md' },
  closable: { type: Boolean, default: true },
  initialFocus: { type: String, default: '' },
})
const emit = defineEmits(['close'])
const dialog = ref(null)
let pressStartedOnBackdrop = false
let previouslyFocused = null

function onKeydown(event) {
  // Esc сначала закрывает открытое поверх окна меню (выбор цвета и т. п.), а не само окно
  if (event.key === 'Escape' && props.closable && !document.querySelector('.menu')) {
    const stack = document.querySelectorAll('.modal-overlay')
    if (stack[stack.length - 1] === dialog.value?.parentElement) {
      event.stopPropagation()
      emit('close')
    }
  }
}

onMounted(async () => {
  previouslyFocused = document.activeElement
  document.addEventListener('keydown', onKeydown, true)
  await nextTick()
  const root = dialog.value
  const pick = (selector) => root?.querySelector(selector)
  const field = pick('.modal-body input:not([type=hidden]):not([disabled]), .modal-body textarea, .modal-body select')
  const danger = pick('.modal-footer .btn-danger')
  const target = (props.initialFocus && pick(props.initialFocus))
    || field
    || (danger && pick('.modal-footer [data-cancel], .modal-footer .btn-ghost'))
    || pick('.modal-footer .btn-primary')
    || pick('button.btn-primary')
  target?.focus?.()
})
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown, true)
  if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus()
})
</script>

<template>
  <Teleport to="body">
    <div
      class="modal-overlay"
      @mousedown.self="pressStartedOnBackdrop = true"
      @mouseup.self="pressStartedOnBackdrop && closable && emit('close'); pressStartedOnBackdrop = false"
    >
      <div ref="dialog" class="modal" :class="size !== 'md' ? `modal-${size}` : ''" role="dialog" aria-modal="true" :aria-label="title">
        <div v-if="title || $slots.header" class="modal-header">
          <slot name="header"><h2 class="modal-title">{{ title }}</h2></slot>
          <slot name="header-actions" />
          <button v-if="closable" class="btn btn-ghost btn-icon btn-sm" type="button" aria-label="Закрыть" @click="emit('close')">
            <AppIcon name="close" />
          </button>
        </div>
        <div class="modal-body scroll-thin"><slot /></div>
        <div v-if="$slots.footer" class="modal-footer"><slot name="footer" /></div>
      </div>
    </div>
  </Teleport>
</template>
