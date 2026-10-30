<script setup>
import AppIcon from './AppIcon.vue'

/**
 * Сегментный выбор (как переключатель вида в «По Делу»): серая дорожка, выбранный вариант —
 * белая «таблетка» с тенью; у варианта с цветом (статус задачи) — цветная точка и оттенок цвета.
 * Варианты — кнопки: Tab проходит по ним, Enter и пробел выбирают. Щелчок по уже выбранному
 * варианту тоже сообщается (update:modelValue) — что с ним делать, решает владелец.
 *
 *   <SegmentedControl :model-value="mode" :options="MODES" aria-label="Окончание задаётся" @update:model-value="setMode" />
 *
 * options: [{ value, label, title?, color?, text?, icon?, sample?, badge?, disabled? }]
 *   color/text — точка и цвет подписи выбранного варианта; sample — образец линии (dashed, solid, dotted);
 *   badge — счётчик (например, комментариев).
 * kind="tabs" — вкладки (role="tablist"/"tab", aria-selected) вместо выбора значения (radiogroup/radio).
 * block — на всю ширину: варианты поровну, fit="content" — по ширине подписи (длинные подписи не обрезаются);
 * columns — варианты сеткой в столько колонок (длинные подписи не обрезаются при любой ширине);
 * stacked — образец над подписью (плитки).
 */
defineProps({
  modelValue: { type: [String, Number, Boolean], default: null },
  options: { type: Array, required: true },
  disabled: { type: Boolean, default: false },
  block: { type: Boolean, default: false },
  stacked: { type: Boolean, default: false },
  fit: { type: String, default: 'equal' },
  columns: { type: Number, default: 0 },
  size: { type: String, default: 'md' },
  kind: { type: String, default: 'radio' },
})
const emit = defineEmits(['update:modelValue'])
</script>

<template>
  <div
    class="segmented seg" :class="[`seg-${size}`, { 'is-block': block, 'is-fit-content': fit === 'content', 'is-stacked': stacked, 'is-grid': columns > 0 }]"
    :style="columns > 0 ? { '--cols': columns } : null" :role="kind === 'tabs' ? 'tablist' : 'radiogroup'"
  >
    <button
      v-for="o in options" :key="String(o.value)" type="button" class="seg-option"
      :class="{ 'is-active': modelValue === o.value, 'has-color': Boolean(o.color) }"
      :role="kind === 'tabs' ? 'tab' : 'radio'"
      :aria-checked="kind === 'tabs' ? undefined : modelValue === o.value"
      :aria-selected="kind === 'tabs' ? modelValue === o.value : undefined"
      :title="o.title || undefined" :disabled="disabled || o.disabled"
      :style="o.color ? { '--c': o.color, '--c-text': o.text || o.color } : null"
      @click="emit('update:modelValue', o.value)"
    >
      <span v-if="o.color" class="seg-dot" aria-hidden="true" />
      <AppIcon v-if="o.icon" :name="o.icon" :size="14" />
      <span v-if="o.sample" class="seg-sample" :class="`is-${o.sample}`" aria-hidden="true" />
      <span class="seg-label">{{ o.label }}</span>
      <span v-if="o.badge" class="seg-badge">{{ o.badge }}</span>
    </button>
  </div>
</template>

<style scoped>
/* Класс .segmented оставлен для селекторов тестов и руководства; вид задают правила ниже */
.seg { display: inline-flex; align-items: stretch; gap: 2px; padding: 3px; max-width: 100%; border: 0; border-radius: 10px; background: #eef1f7; }
.seg.is-block { display: flex; width: 100%; }
.seg.is-block .seg-option { flex: 1 1 0; min-width: 0; }
.seg.is-block.is-fit-content { flex-wrap: wrap; }
.seg.is-block.is-fit-content .seg-option { flex: 1 1 auto; padding-left: 9px; padding-right: 9px; }
.seg.is-grid { display: grid; grid-template-columns: repeat(var(--cols), minmax(0, 1fr)); width: 100%; }
.seg-option {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 30px; padding: 5px 12px;
  border: 0; border-radius: 7px; background: transparent; color: var(--color-text-secondary);
  font-size: 12.5px; font-weight: 500; line-height: 1.2; white-space: nowrap;
  transition: background 0.15s, color 0.15s, box-shadow 0.15s;
}
.seg-sm .seg-option { min-height: 26px; padding: 3px 10px; font-size: 12px; }
.seg-option:hover:not(:disabled):not(.is-active) { background: rgba(255, 255, 255, 0.65); color: var(--color-text); }
.seg-option.is-active { background: var(--color-surface); color: var(--color-text); font-weight: 600; box-shadow: 0 1px 2px rgba(20, 25, 40, 0.1), 0 0 0 1px rgba(20, 25, 40, 0.04); }
.seg-option:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 1px; position: relative; z-index: 1; }
.seg-option:disabled { cursor: not-allowed; color: var(--color-text-muted); }
.seg-option:disabled:hover { background: transparent; color: var(--color-text-muted); }
.seg-option.is-active:disabled { background: #f7f8fb; color: var(--color-text-secondary); box-shadow: 0 0 0 1px var(--color-border); }
.seg-label { overflow: hidden; text-overflow: ellipsis; }

/* Вариант с цветом: точка; выбранный — оттенок цвета и цветная подпись */
.seg-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; background: var(--c); box-shadow: 0 0 0 2px color-mix(in srgb, var(--c) 18%, transparent); }
.seg-option.has-color.is-active { background: color-mix(in srgb, var(--c) 12%, white); color: var(--c-text); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--c) 50%, white); }
.seg-option.has-color.is-active:disabled { background: #f7f8fb; color: var(--color-text-secondary); box-shadow: 0 0 0 1px var(--color-border); }
.seg-option.has-color.is-active:disabled .seg-dot { filter: grayscale(0.6); }

/* Счётчик на вкладке */
.seg-badge { min-width: 18px; height: 18px; padding: 0 5px; border-radius: 999px; background: var(--color-primary); color: #fff; font-size: 10.5px; font-weight: 700; display: inline-flex; align-items: center; justify-content: center; }
.seg-option:not(.is-active) .seg-badge { background: #d9dfeb; color: var(--color-text-secondary); }

/* Образец линии: пунктир, сплошная, точки */
.seg-sample { width: 18px; height: 0; border-top: 2px dashed currentColor; flex-shrink: 0; opacity: 0.8; }
.seg-sample.is-solid { border-top-style: solid; }
.seg-sample.is-dotted { border-top-style: dotted; }
.is-stacked .seg-option { flex-direction: column; gap: 5px; padding-top: 8px; padding-bottom: 7px; }
.is-stacked .seg-sample { width: 28px; }

@media (max-width: 768px) {
  .seg-option, .seg-sm .seg-option { min-height: 40px; font-size: 13px; }
}
</style>
