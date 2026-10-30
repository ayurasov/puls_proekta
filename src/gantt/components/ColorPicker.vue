<script setup>
import { computed } from 'vue'
import AppIcon from '../../components/common/AppIcon.vue'
import { PALETTE, readableText, resolveColor } from '../domain/colors'

/** Палитра 1.0.x + свой цвет (сохраняется как #rrggbb — в 1.0.x свой цвет терялся после перезагрузки). */
const props = defineProps({
  modelValue: { type: String, default: null },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])
const custom = computed(() => (props.modelValue && props.modelValue.startsWith('#') ? props.modelValue : resolveColor(props.modelValue).color))
const isCustom = computed(() => Boolean(props.modelValue && props.modelValue.startsWith('#')))
</script>

<template>
  <div class="color-picker" role="radiogroup">
    <button
      v-for="c in PALETTE" :key="c.key" type="button" class="swatch" :class="{ on: modelValue === c.key }" :style="{ '--c': c.color, '--t': c.text }"
      :title="c.label" :aria-label="c.label" :aria-checked="modelValue === c.key" role="radio" :disabled="disabled"
      @click="emit('update:modelValue', c.key)"
    >
      <AppIcon v-if="modelValue === c.key" name="check" :size="13" :stroke-width="3" />
    </button>
    <label class="swatch custom" :class="{ on: isCustom }" :style="isCustom ? { '--c': modelValue, '--t': readableText(modelValue) } : null" title="Свой цвет">
      <input type="color" :value="custom" :disabled="disabled" aria-label="Свой цвет" @change="emit('update:modelValue', $event.target.value)" />
      <AppIcon v-if="isCustom" name="check" :size="13" :stroke-width="3" />
      <span v-else>+</span>
    </label>
  </div>
</template>

<style scoped>
.color-picker { display: flex; flex-wrap: wrap; gap: 6px; }
.swatch {
  position: relative; width: var(--swatch-size, 24px); height: var(--swatch-size, 24px); padding: 0; border: 0; border-radius: 7px; flex-shrink: 0;
  display: inline-flex; align-items: center; justify-content: center; background: var(--c); color: var(--t);
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.08); transition: transform 0.12s, box-shadow 0.12s;
}
.swatch:hover:not(:disabled) { transform: translateY(-1px); box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.08), 0 3px 8px -2px color-mix(in srgb, var(--c) 60%, transparent); }
.swatch.on { box-shadow: 0 0 0 2px var(--color-surface), 0 0 0 4px var(--c); }
.swatch:focus-visible, .custom:has(input:focus-visible) { outline: 2px solid var(--color-primary); outline-offset: 4px; }
.swatch:disabled { cursor: not-allowed; opacity: 0.45; }
.swatch.on:disabled { opacity: 0.8; }
.custom:has(input:disabled) { cursor: not-allowed; opacity: 0.45; }
.custom input:disabled { cursor: not-allowed; }
.custom { background: conic-gradient(#ef4444, #f59e0b, #22c55e, #06b6d4, #3b82f6, #8b5cf6, #ec4899, #ef4444); color: #fff; font-weight: 800; font-size: 14px; cursor: pointer; overflow: hidden; }
.custom.on { background: var(--c); color: var(--t); overflow: visible; }
.custom input { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%; }
.custom span { text-shadow: 0 1px 2px rgba(0, 0, 0, 0.4); pointer-events: none; }
.swatch :deep(svg) { pointer-events: none; }
/* Телефон: цель касания не меньше 40 px */
@media (max-width: 768px) {
  .color-picker { gap: 6px; }
  .swatch { width: 40px; height: 40px; border-radius: 10px; }
}
</style>
