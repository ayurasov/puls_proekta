<script setup>
import AppIcon from '../common/AppIcon.vue'

/** Знак интеграции: Jira, «По Делу», вход через домен (LDAP) — цветная плашка с буквой или значком. */
defineProps({ kind: { type: String, required: true }, size: { type: Number, default: 40 } })
</script>

<template>
  <span class="logo" :class="`logo-${kind}`" :style="{ width: `${size}px`, height: `${size}px` }" aria-hidden="true">
    <span v-if="kind === 'jira'" class="letter">J</span>
    <AppIcon v-else-if="kind === 'podelu'" name="checklist" :size="Math.round(size * 0.48)" :stroke-width="2.2" />
    <AppIcon v-else-if="kind === 'ldap'" name="shield" :size="Math.round(size * 0.48)" :stroke-width="2.2" />
    <AppIcon v-else name="link" :size="Math.round(size * 0.48)" />
  </span>
</template>

<style scoped>
.logo { display: inline-grid; place-items: center; border-radius: 11px; flex-shrink: 0; color: #fff; box-shadow: 0 6px 14px rgba(15, 23, 42, 0.12); }
.logo-jira { background: linear-gradient(140deg, #2684ff, #0052cc); }
.logo-podelu { background: linear-gradient(140deg, #34c38f, #15803d); }
.logo-ldap { background: linear-gradient(140deg, #8b5cf6, #5b21b6); }
.letter { font-weight: 800; font-style: italic; font-size: 18px; letter-spacing: -0.02em; }
</style>
