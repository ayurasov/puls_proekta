<script setup>
import { useId } from 'vue'

/**
 * Настройка «вкл/выкл» одной строкой: подпись и пояснение слева, тумблер справа (как в настройках
 * «По Делу»). Внутри — настоящий флажок role="switch": Tab, пробел и щелчок по подписи работают как
 * у обычного флажка, пояснение озвучивается как описание.
 *
 *   <ToggleSwitch :model-value="on" label="Пометки" hint="Флажки с текстом на шкале" @update:model-value="set" />
 */
defineProps({
  modelValue: { type: Boolean, default: false },
  label: { type: String, default: '' },
  hint: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])
const hintId = useId()
</script>

<template>
  <label class="toggle" :class="{ 'is-disabled': disabled, 'is-on': modelValue }">
    <span class="toggle-text">
      <span class="toggle-label"><slot>{{ label }}</slot></span>
      <span v-if="hint" :id="hintId" class="toggle-hint">{{ hint }}</span>
    </span>
    <input
      class="toggle-input" type="checkbox" role="switch" :checked="modelValue" :disabled="disabled"
      :aria-describedby="hint ? hintId : undefined" @change="emit('update:modelValue', $event.target.checked)"
    />
    <span class="toggle-track" aria-hidden="true" />
  </label>
</template>

<style scoped>
.toggle { position: relative; display: flex; align-items: center; gap: 16px; min-height: 44px; padding: 8px 12px; border-radius: 8px; cursor: pointer; user-select: none; transition: background 0.15s; }
.toggle:hover:not(.is-disabled) { background: rgba(28, 35, 49, 0.035); }
.toggle-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.toggle-label { font-size: 13px; color: var(--color-text); line-height: 1.35; }
.toggle-hint { font-size: 11.5px; color: var(--color-text-muted); line-height: 1.4; }
.toggle-input { position: absolute; opacity: 0; width: 1px; height: 1px; margin: 0; pointer-events: none; }
.toggle-track { position: relative; flex-shrink: 0; width: 36px; height: 20px; border-radius: 999px; background: #d3d8e4; transition: background 0.18s, box-shadow 0.15s; }
.toggle-track::after { content: ''; position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 50%; background: #fff; box-shadow: 0 1px 2px rgba(20, 25, 40, 0.25); transition: transform 0.18s ease; }
.toggle-input:checked + .toggle-track { background: var(--color-primary); }
.toggle-input:checked + .toggle-track::after { transform: translateX(16px); }
.toggle-input:focus-visible + .toggle-track { box-shadow: var(--focus-ring); outline: 2px solid var(--color-primary); outline-offset: 2px; }
/* Недоступный тумблер не выглядит включаемым: бледный, серый вместо синего, курсор «нельзя» */
.toggle.is-disabled { cursor: not-allowed; }
.toggle.is-disabled .toggle-label { color: var(--color-text-muted); }
.toggle-input:disabled + .toggle-track { opacity: 0.55; }
.toggle-input:checked:disabled + .toggle-track { background: #9aa6c4; }

@media (max-width: 768px) {
  .toggle { min-height: 48px; }
  .toggle-label { font-size: 14px; }
  .toggle-track { width: 42px; height: 24px; }
  .toggle-track::after { width: 20px; height: 20px; }
  .toggle-input:checked + .toggle-track::after { transform: translateX(18px); }
}
</style>
