<script setup>
import { computed, inject, ref } from 'vue'
import BaseModal from '../../components/common/BaseModal.vue'
import AppIcon from '../../components/common/AppIcon.vue'
import ColorPicker from './ColorPicker.vue'
import { addDays, maxDay, minDay } from '../domain/dates'
import { SCALES } from '../domain/layout'
import { useUiStore } from '../../stores/ui'
import { countLabel } from '../../utils/format'

/** Настройки графика — общие для всех, кто работает с планом (изменения видны коллегам). */
const emit = defineEmits(['close'])
const editor = inject('gantt')
const ui = useUiStore()
const s = computed(() => editor.settings)
const d = computed(() => editor.settings.display)
const readonly = computed(() => !editor.canEdit)
const newCategory = ref('')

function patch(settings, label = 'Настройки графика') {
  editor.updatePlan({ settings }, label)
}
function toggle(key) {
  patch({ display: { [key]: !d.value[key] } })
}
function setRange(field, value) {
  if (!value) return
  const range = { ...s.value.range, [field]: value }
  if (range.end < range.start) { ui.error('Конец шкалы раньше начала'); return }
  patch({ range }, 'Шкала дат')
}
function fitRange() {
  let start = null
  let end = null
  for (const item of editor.itemList) {
    const a = item.type === 'milestone' ? item.date : item.start
    const b = item.type === 'milestone' ? item.date : item.type === 'task' ? addDays(item.end, Math.max(0, item.offset || 0)) : null
    if (a) start = minDay(start, a)
    if (b) end = maxDay(end, b)
  }
  if (!start) { ui.error('В плане нет задач с датами'); return }
  patch({ range: { start: addDays(start, -7), end: addDays(end, 14) } }, 'Шкала по задачам')
}
function setToday(fields) {
  patch({ today: { ...s.value.today, ...fields } }, 'Линия «сегодня»')
}
const TOOLTIP = { status: 'Статус', dates: 'Сроки и сдвиг', progress: 'Прогресс', dependency: 'Предшественник', assignee: 'Исполнитель', lastChange: 'Последнее изменение' }
function toggleTooltip(field) {
  const list = [...(d.value.tooltipFields || [])]
  const i = list.indexOf(field)
  if (i >= 0) list.splice(i, 1)
  else list.push(field)
  patch({ display: { tooltipFields: list } })
}
function addCategory() {
  const name = newCategory.value.trim()
  if (!name) return
  const list = [...(s.value.categories || [])]
  if (!list.includes(name)) patch({ categories: [...list, name] }, 'Новая категория')
  newCategory.value = ''
}
function removeCategory(name) {
  const used = editor.itemList.filter((i) => i.category === name).length
  if (used) { ui.error(`Категория используется в ${countLabel(used, 'задаче', 'задачах', 'задачах')} — сначала смените её у задач`); return }
  patch({ categories: (s.value.categories || []).filter((c) => c !== name) }, 'Удаление категории')
}
function saveDefaultView() {
  const v = editor.state.view
  patch({ scale: v.scale, zoom: v.zoom, gridWidth: v.gridWidth, defaultCollapsed: [...v.collapsed] }, 'Вид по умолчанию')
  ui.success('Ваш вид стал видом по умолчанию', 'Коллеги увидят его при первом открытии плана')
}
</script>

<template>
  <BaseModal title="Настройки графика" size="wide" initial-focus=".modal-footer .btn-primary" @close="emit('close')">
    <p v-if="readonly" class="muted small">Только просмотр — изменить настройки может сотрудник с правом редактирования.</p>
    <div class="settings">
      <section>
        <h3>Шкала дат</h3>
        <div class="form-grid">
          <label class="field"><span class="field-label">Начало</span><input class="input" type="date" :value="s.range.start" :disabled="readonly" @change="setRange('start', $event.target.value)" /></label>
          <label class="field"><span class="field-label">Конец</span><input class="input" type="date" :value="s.range.end" :disabled="readonly" @change="setRange('end', $event.target.value)" /></label>
        </div>
        <button class="btn btn-sm" type="button" :disabled="readonly" @click="fitRange"><AppIcon name="fit" :size="14" /> По срокам задач</button>
      </section>
      <section>
        <h3>Линия «сегодня»</h3>
        <div class="segmented">
          <button type="button" :class="{ 'is-active': s.today?.auto !== false }" :disabled="readonly" @click="setToday({ auto: true })">Текущая дата</button>
          <button type="button" :class="{ 'is-active': s.today?.auto === false }" :disabled="readonly" @click="setToday({ auto: false, date: s.today?.date || editor.state.today })">Фиксированная</button>
        </div>
        <label v-if="s.today?.auto === false" class="field"><span class="field-label">Дата</span><input class="input" type="date" :value="s.today.date" :disabled="readonly" @change="$event.target.value && setToday({ date: $event.target.value })" /></label>
        <div class="form-grid">
          <label class="field">
            <span class="field-label">Линия</span>
            <select class="select" :value="s.today?.style || 'dashed'" :disabled="readonly" @change="setToday({ style: $event.target.value })">
              <option value="dashed">Пунктирная</option><option value="solid">Сплошная</option><option value="dotted">Точечная</option>
            </select>
          </label>
          <div class="field"><span class="field-label">Цвет</span><ColorPicker :model-value="s.today?.color || 'red'" :disabled="readonly" @update:model-value="(c) => setToday({ color: c })" /></div>
        </div>
      </section>
      <section>
        <h3>Отображение</h3>
        <label v-for="(label, key) in { showProgress: 'Прогресс на полосах', showNotes: 'Пометки', showTaskNamesOnBars: 'Названия задач на полосах', showOffsetLabels: 'Подписи временных сдвигов', showDependencies: 'Связи между задачами (стрелки)' }" :key="key" class="switch">
          <input type="checkbox" :checked="d[key]" :disabled="readonly" @change="toggle(key)" /><span class="switch-track" />{{ label }}
        </label>
        <label class="field narrow">
          <span class="field-label">Высота строки, px</span>
          <input class="input" type="number" min="28" max="80" :value="s.rowHeight || 44" :disabled="readonly" @change="patch({ rowHeight: Math.max(28, Math.min(80, parseInt($event.target.value, 10) || 44)) })" />
        </label>
      </section>
      <section>
        <h3>Календарь и связи</h3>
        <label class="switch"><input type="checkbox" :checked="d.useRuCalendar" :disabled="readonly" @change="toggle('useRuCalendar')" /><span class="switch-track" />Производственный календарь РФ</label>
        <label class="switch"><input type="checkbox" :checked="d.showWeekends" :disabled="readonly" @change="toggle('showWeekends')" /><span class="switch-track" />Выделять выходные</label>
        <label class="switch"><input type="checkbox" :checked="d.showHolidays" :disabled="readonly" @change="toggle('showHolidays')" /><span class="switch-track" />Выделять праздники</label>
        <label class="switch"><input type="checkbox" :checked="d.autoShift" :disabled="readonly" @change="toggle('autoShift')" /><span class="switch-track" />Автосдвиг задач по связям</label>
        <p class="muted small">Праздники и переносы — по календарю администратора (Настройки → Производственный календарь).</p>
      </section>
      <section>
        <h3>Легенда</h3>
        <label class="switch"><input type="checkbox" :checked="d.legendEnabled" :disabled="readonly" @change="toggle('legendEnabled')" /><span class="switch-track" />Показывать легенду</label>
        <div class="form-grid">
          <label class="field"><span class="field-label">Где</span>
            <select class="select" :value="d.legendPosition" :disabled="readonly || !d.legendEnabled" @change="patch({ display: { legendPosition: $event.target.value } })"><option value="bottom">Снизу</option><option value="top">Сверху</option></select>
          </label>
          <label class="field"><span class="field-label">Состав</span>
            <select class="select" :value="d.legendMode" :disabled="readonly || !d.legendEnabled" @change="patch({ display: { legendMode: $event.target.value } })"><option value="all">Все строки</option><option value="main">Только верхнего уровня</option></select>
          </label>
        </div>
      </section>
      <section>
        <h3>Подсказка у полосы</h3>
        <label v-for="(label, key) in TOOLTIP" :key="key" class="checkbox"><input type="checkbox" :checked="(d.tooltipFields || []).includes(key)" :disabled="readonly" @change="toggleTooltip(key)" /> {{ label }}</label>
      </section>
      <section class="wide">
        <h3>Категории задач</h3>
        <div class="categories">
          <span v-for="c in s.categories" :key="c" class="category">{{ c }}<button v-if="!readonly" type="button" title="Удалить" @click="removeCategory(c)"><AppIcon name="close" :size="11" /></button></span>
        </div>
        <form v-if="!readonly" class="add-category" @submit.prevent="addCategory">
          <input v-model="newCategory" class="input input-sm" maxlength="255" placeholder="Новая категория" />
          <button class="btn btn-sm" type="submit" :disabled="!newCategory.trim()">Добавить</button>
        </form>
      </section>
      <section class="wide">
        <h3>Вид по умолчанию</h3>
        <p class="muted small">Масштаб, шкала ({{ SCALES.find((x) => x.key === editor.state.view.scale)?.label }}, {{ Math.round(editor.state.view.zoom * 100) }}%), ширина колонки и свёрнутые группы у каждого свои. Можно сделать ваш текущий вид видом по умолчанию для коллег.</p>
        <button class="btn btn-sm" type="button" :disabled="readonly" @click="saveDefaultView">Сделать мой вид видом по умолчанию</button>
      </section>
    </div>
    <template #footer><button class="btn btn-primary" type="button" @click="emit('close')">Готово</button></template>
  </BaseModal>
</template>

<style scoped>
.settings { display: grid; grid-template-columns: 1fr 1fr; gap: 18px 28px; }
section { display: flex; flex-direction: column; gap: 10px; }
section.wide { grid-column: 1 / -1; }
h3 { font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-text-muted); }
.narrow { max-width: 180px; }
.categories { display: flex; flex-wrap: wrap; gap: 6px; }
.category { display: inline-flex; align-items: center; gap: 4px; background: #eef1f7; border-radius: 999px; padding: 3px 6px 3px 10px; font-size: 12.5px; }
.category button { border: 0; background: transparent; color: var(--color-text-muted); padding: 2px; border-radius: 50%; display: flex; }
.category button:hover { background: #dfe3ec; color: var(--color-danger); }
.add-category { display: flex; gap: 6px; max-width: 360px; }
.segmented { align-self: flex-start; }
@media (max-width: 768px) { .settings { grid-template-columns: 1fr; } }
</style>
