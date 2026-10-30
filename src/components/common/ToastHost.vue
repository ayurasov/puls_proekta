<script setup>
import { useUiStore } from '../../stores/ui'
import AppIcon from './AppIcon.vue'

const ui = useUiStore()
const ICONS = { success: 'check', error: 'warning', info: 'bell' }

async function runAction(toast) {
  ui.dismissToast(toast.id)
  await toast.action?.handler?.()
}
</script>

<template>
  <div class="toast-viewport" aria-live="polite">
    <TransitionGroup name="toast">
      <div v-for="toast in ui.toasts" :key="toast.id" class="toast card" :class="`toast-${toast.kind}`" role="status">
        <AppIcon :name="ICONS[toast.kind] || 'bell'" :size="18" class="toast-icon" />
        <div class="toast-copy">
          <strong>{{ toast.title }}</strong>
          <span v-if="toast.body">{{ toast.body }}</span>
        </div>
        <button v-if="toast.action" class="btn btn-sm toast-action" type="button" @click="runAction(toast)">{{ toast.action.label }}</button>
        <button class="toast-close" type="button" aria-label="Закрыть" @click="ui.dismissToast(toast.id)"><AppIcon name="close" :size="14" /></button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
/* Уведомления — справа сверху, как в «По Делу»: не закрывают строку быстрого добавления и полосу прокрутки */
.toast-viewport { position: fixed; top: max(16px, env(safe-area-inset-top)); right: max(16px, env(safe-area-inset-right)); z-index: 600; display: flex; flex-direction: column; gap: 8px; width: min(380px, calc(100vw - 32px)); pointer-events: none; }
.toast { display: flex; align-items: flex-start; gap: 10px; padding: 12px 12px 12px 14px; box-shadow: var(--shadow-2); pointer-events: auto; }
.toast-icon { margin-top: 1px; color: var(--color-primary); }
.toast-success .toast-icon { color: var(--color-success); }
.toast-error { border-color: #f3c3c3; }
.toast-error .toast-icon, .toast-error strong { color: var(--color-danger); }
.toast-copy { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; font-size: 13px; }
.toast-copy span { color: var(--color-text-secondary); font-size: 12.5px; overflow-wrap: anywhere; }
.toast-action { align-self: center; }
.toast-close { border: 0; background: transparent; color: var(--color-text-muted); padding: 2px; border-radius: 4px; }
.toast-close:hover { background: var(--color-hover); color: var(--color-text); }
.toast-enter-active, .toast-leave-active { transition: all 0.18s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(-8px); }
.toast-close { min-width: 24px; min-height: 24px; display: flex; align-items: center; justify-content: center; }
@media (max-width: 768px) {
  .toast-viewport { top: max(8px, env(safe-area-inset-top)); right: max(8px, env(safe-area-inset-right)); left: max(8px, env(safe-area-inset-left)); width: auto; }
  .toast-close { min-width: 40px; min-height: 40px; }
}
</style>
