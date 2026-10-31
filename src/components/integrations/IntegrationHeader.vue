<script setup>
import AppIcon from '../common/AppIcon.vue'
import HelpLink from '../common/HelpLink.vue'
import IntegrationLogo from './IntegrationLogo.vue'

/** Заголовок страницы интеграции: «Интеграции /» (назад к обзору), знак, название, пояснение и состояние. */
defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
  logo: { type: String, default: '' },
  badge: { type: Object, default: null }, // { kind: 'ok' | 'warn' | 'error' | 'muted', text }
  help: { type: String, default: '' },
})
</script>

<template>
  <header class="integration-header">
    <router-link to="/admin/integrations" class="crumb" data-testid="integrations-back"><AppIcon name="chevronLeft" :size="14" /> Интеграции</router-link>
    <div class="row">
      <IntegrationLogo v-if="logo" :kind="logo" :size="44" />
      <div class="titles">
        <h1 class="page-title">{{ title }} <HelpLink v-if="help" :topic="help" /></h1>
        <p v-if="subtitle" class="muted small">{{ subtitle }}</p>
      </div>
      <span v-if="badge" class="state" :class="`state-${badge.kind}`" data-testid="integration-state"><span class="dot" />{{ badge.text }}</span>
    </div>
  </header>
</template>

<style scoped>
.integration-header { margin-bottom: 18px; display: flex; flex-direction: column; gap: 10px; }
.crumb { display: inline-flex; align-items: center; gap: 4px; font-size: 12.5px; color: var(--color-text-muted); text-decoration: none; width: fit-content; }
.crumb:hover { color: var(--color-primary); }
.row { display: flex; align-items: center; gap: 14px; }
.titles { flex: 1; min-width: 0; }
.titles h1 { margin: 0; display: flex; align-items: center; gap: 8px; }
.titles p { margin: 3px 0 0; }
.state { display: inline-flex; align-items: center; gap: 7px; padding: 5px 12px; border-radius: 999px; font-size: 12.5px; font-weight: 600; white-space: nowrap; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.state-ok { background: var(--color-success-soft); color: #1d7a4c; }
.state-warn { background: var(--color-warning-soft); color: #8a5a00; }
.state-error { background: var(--color-danger-soft); color: #b42318; }
.state-muted { background: #eef1f7; color: var(--color-text-secondary); }
@media (max-width: 640px) { .row { flex-wrap: wrap; } .state { order: 3; } }
</style>
