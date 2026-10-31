<script setup>
import AppIcon from '../common/AppIcon.vue'

/** Шаги диагностики интеграции: [{ id, title, status: ok | warn | error | skip, detail }]. */
defineProps({ steps: { type: Array, required: true } })
const ICONS = { ok: 'check', warn: 'warning', error: 'close', skip: 'minus' }
const LABELS = { ok: 'в порядке', warn: 'внимание', error: 'ошибка', skip: 'пропущено' }
</script>

<template>
  <ol class="diag">
    <li v-for="step in steps" :key="step.id" class="step" :class="`is-${step.status}`">
      <span class="mark" :aria-label="LABELS[step.status]"><AppIcon :name="ICONS[step.status] || 'info'" :size="13" :stroke-width="2.6" /></span>
      <div class="body">
        <div class="title">{{ step.title }}</div>
        <div v-if="step.detail" class="detail">{{ step.detail }}</div>
      </div>
    </li>
  </ol>
</template>

<style scoped>
.diag { list-style: none; margin: 14px 0 0; padding: 0; display: flex; flex-direction: column; border: 1px solid var(--color-border); border-radius: var(--radius-md); overflow: hidden; }
.step { display: flex; gap: 12px; padding: 10px 14px; border-bottom: 1px solid #f0f2f6; }
.step:last-child { border-bottom: 0; }
.mark { width: 22px; height: 22px; border-radius: 50%; display: grid; place-items: center; flex-shrink: 0; color: #fff; margin-top: 1px; }
.is-ok .mark { background: #22a06b; }
.is-warn .mark { background: #e2a03f; }
.is-error .mark { background: #e5484d; }
.is-skip .mark { background: #b6bdc9; }
.is-error { background: #fff8f8; }
.is-warn { background: #fffbf2; }
.body { min-width: 0; }
.title { font-weight: 600; font-size: 13px; }
.detail { font-size: 12.5px; color: var(--color-text-secondary); line-height: 1.5; margin-top: 2px; overflow-wrap: anywhere; }
</style>
