<script setup>
import { computed } from 'vue'
import { PALETTE, resolveColor } from '../domain/colors'

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
      v-for="c in PALETTE" :key="c.key" type="button" class="swatch" :class="{ on: modelValue === c.key }" :style="{ background: c.color }"
      :title="c.label" :aria-label="c.label" :aria-checked="modelValue === c.key" role="radio" :disabled="disabled"
      @click="emit('update:modelValue', c.key)"
    />
    <label class="swatch custom" :class="{ on: isCustom }" :style="isCustom ? { background: modelValue } : null" title="Свой цвет">
      <input type="color" :value="custom" :disabled="disabled" @change="emit('update:modelValue', $event.target.value)" />
      <span v-if="!isCustom">+</span>
    </label>
  </div>
</template>

<style scoped>
.color-picker { display: flex; flex-wrap: wrap; gap: 6px; }
.swatch { width: 22px; height: 22px; border-radius: 6px; border: 2px solid transparent; padding: 0; box-shadow: inset 0 0 0 1px rgba(0,0,0,.08); position: relative; }
.swatch.on { border-color: #1c2331; box-shadow: 0 0 0 2px #fff inset; }
.swatch:disabled { cursor: not-allowed; opacity: 0.45; }
.swatch.on:disabled { opacity: 0.8; }
.custom:has(input:disabled) { cursor: not-allowed; opacity: 0.45; }
.custom input:disabled { cursor: not-allowed; }
.custom { display: flex; align-items: center; justify-content: center; background: conic-gradient(#ef4444, #f59e0b, #22c55e, #06b6d4, #3b82f6, #8b5cf6, #ec4899, #ef4444); color: #fff; font-weight: 800; font-size: 14px; cursor: pointer; overflow: hidden; }
.custom input { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%; }
.custom span { text-shadow: 0 1px 2px rgba(0,0,0,.4); pointer-events: none; }
</style>
