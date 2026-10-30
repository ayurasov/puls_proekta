<script setup>
import { computed } from 'vue'

const props = defineProps({
  user: { type: Object, default: null },
  size: { type: Number, default: 28 },
  title: { type: String, default: '' },
  // Свой цвет фона (например, цвет исполнителя плана) вместо цвета по имени
  color: { type: String, default: null },
})

const PALETTE = ['#4f7cff', '#2fa96b', '#e8a13a', '#8b5cf6', '#ec4899', '#0ea5a4', '#ef6c3a', '#64748b']
// Первые буквы двух первых слов; кавычки и знаки пропускаются («АО «Меридиан»» → «АМ», а не «А«»)
const initials = computed(() => {
  const parts = String(props.user?.name || '?').trim().split(/\s+/)
  const letter = (word) => word?.match(/[\p{L}\p{N}]/u)?.[0] || ''
  return (letter(parts[0]) + letter(parts[1])).toUpperCase() || '?'
})
const color = computed(() => {
  if (props.color) return props.color
  const name = props.user?.name || ''
  let hash = 0
  for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0
  return PALETTE[hash % PALETTE.length]
})
</script>

<template>
  <span class="avatar" :style="{ width: size + 'px', height: size + 'px', fontSize: Math.round(size * 0.4) + 'px', background: user?.avatarUrl ? 'transparent' : color, boxShadow: user?.avatarUrl && props.color ? `0 0 0 2px ${props.color}` : null }" :title="title || user?.name">
    <img v-if="user?.avatarUrl" :src="user.avatarUrl" alt="" loading="lazy" />
    <span v-else>{{ initials }}</span>
  </span>
</template>

<style scoped>
.avatar { display: inline-flex; align-items: center; justify-content: center; border-radius: 50%; color: #fff; font-weight: 700; flex-shrink: 0; overflow: hidden; user-select: none; }
.avatar img { width: 100%; height: 100%; object-fit: cover; display: block; }
</style>
