<script setup>
import { computed, inject, ref } from 'vue'
import BaseModal from '../../components/common/BaseModal.vue'
import AppIcon from '../../components/common/AppIcon.vue'
import HelpLink from '../../components/common/HelpLink.vue'
import SegmentedControl from '../../components/common/SegmentedControl.vue'
import ToggleSwitch from '../../components/common/ToggleSwitch.vue'
import ColorPicker from './ColorPicker.vue'
import ChartPreview from './ChartPreview.vue'
import { addDays, diffDays, maxDay, minDay } from '../domain/dates'
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
function setDisplay(key, value) {
  patch({ display: { [key]: value } })
}
function setRange(field, value) {
  if (!value) return
  const range = { ...s.value.range, [field]: value }
  if (range.end < range.start) { ui.error('Конец шкалы раньше начала'); return }
  patch({ range }, 'Шкала дат')
}
const rangeDays = computed(() => diffDays(s.value.range.start, s.value.range.end) + 1)
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
function setTodayMode(mode) {
  if (mode === 'auto') setToday({ auto: true })
  else setToday({ auto: false, date: s.value.today?.date || editor.state.today })
}
// Выпадающие списки сохраняли значение только при его смене — сегменты ведут себя так же
function setTodayStyle(style) {
  if (style !== (s.value.today?.style || 'dashed')) setToday({ style })
}
function setLegend(key, value) {
  if (value !== d.value[key]) patch({ display: { [key]: value } })
}

const TODAY_MODES = [{ value: 'auto', label: 'Текущая дата' }, { value: 'fixed', label: 'Фиксированная' }]
const LINE_STYLES = [
  { value: 'dashed', label: 'Пунктирная', sample: 'dashed' },
  { value: 'solid', label: 'Сплошная', sample: 'solid' },
  { value: 'dotted', label: 'Точечная', sample: 'dotted' },
]
const LEGEND_POSITIONS = [{ value: 'bottom', label: 'Снизу' }, { value: 'top', label: 'Сверху' }]
const LEGEND_MODES = [{ value: 'all', label: 'Все строки' }, { value: 'main', label: 'Только верхнего уровня' }]
const BAR_OPTIONS = [
  { key: 'showProgress', label: 'Прогресс на полосах', hint: 'Тёмная полоска по нижнему краю полосы' },
  { key: 'showNotes', label: 'Пометки', hint: 'Флажки с текстом на шкале и в строках' },
  { key: 'showTaskNamesOnBars', label: 'Названия задач на полосах', hint: 'Рядом с датами или своим текстом полосы' },
  { key: 'showOffsetLabels', label: 'Подписи временных сдвигов', hint: '«сдвиг +3 д» справа от полосы' },
  { key: 'showDependencies', label: 'Связи между задачами (стрелки)', hint: '' },
]
const CALENDAR_OPTIONS = [
  { key: 'useRuCalendar', label: 'Производственный календарь РФ', hint: 'Праздники и переносы — по календарю администратора (Настройки → Производственный календарь)' },
  { key: 'showWeekends', label: 'Выделять выходные', hint: '' },
  { key: 'showHolidays', label: 'Выделять праздники', hint: '' },
  { key: 'autoShift', label: 'Автосдвиг задач по связям', hint: 'Последующие задачи сдвигаются вслед за предшественником' },
]

const TOOLTIP = { status: 'Статус', dates: 'Сроки и сдвиг', progress: 'Прогресс', dependency: 'Предшественник', assignee: 'Исполнитель', lastChange: 'Последнее изменение' }
function toggleTooltip(field) {
  const list = [...(d.value.tooltipFields || [])]
  const i = list.indexOf(field)
  if (i >= 0) list.splice(i, 1)
  else list.push(field)
  patch({ display: { tooltipFields: list } })
}

const categoryUsage = computed(() => {
  const counts = {}
  for (const item of editor.itemList) if (item.category) counts[item.category] = (counts[item.category] || 0) + 1
  return counts
})
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

const view = computed(() => editor.state.view)
const scaleLabel = computed(() => SCALES.find((x) => x.key === view.value.scale)?.label)
const collapsedCount = computed(() => [...view.value.collapsed].length)
function saveDefaultView() {
  const v = editor.state.view
  patch({ scale: v.scale, zoom: v.zoom, gridWidth: v.gridWidth, defaultCollapsed: [...v.collapsed] }, 'Вид по умолчанию')
  ui.success('Ваш вид стал видом по умолчанию', 'Коллеги увидят его при первом открытии плана')
}
</script>

<template>
  <BaseModal title="Настройки графика" size="wide" initial-focus=".modal-footer .btn-primary" @close="emit('close')">
    <template #header-actions><HelpLink topic="plan-settings" /></template>
    <div class="settings">
      <div class="settings-main">
        <p v-if="readonly" class="readonly-note"><AppIcon name="lock" :size="14" /> Только просмотр — изменить настройки может сотрудник с правом редактирования.</p>

        <section class="block" aria-labelledby="ps-range">
          <header class="block-head">
            <span class="block-icon"><AppIcon name="calendar" :size="15" /></span>
            <div class="block-titles"><h3 id="ps-range">Шкала дат</h3><p>Период, который охватывает график</p></div>
            <span class="pill">{{ countLabel(rangeDays, 'день', 'дня', 'дней') }}</span>
          </header>
          <div class="form-grid">
            <label class="field"><span class="field-label">Начало</span><input class="input" type="date" :value="s.range.start" :disabled="readonly" @change="setRange('start', $event.target.value)" /></label>
            <label class="field"><span class="field-label">Конец</span><input class="input" type="date" :value="s.range.end" :disabled="readonly" @change="setRange('end', $event.target.value)" /></label>
          </div>
          <div class="inline-action">
            <button class="btn btn-sm" type="button" :disabled="readonly" @click="fitRange"><AppIcon name="fit" :size="14" /> По срокам задач</button>
            <span class="field-hint">С запасом: неделя до первой задачи и две — после последней</span>
          </div>
        </section>

        <section class="block" aria-labelledby="ps-today">
          <header class="block-head">
            <span class="block-icon is-red"><AppIcon name="today" :size="15" /></span>
            <div class="block-titles"><h3 id="ps-today">Линия «сегодня»</h3><p>Вертикальная отметка текущего дня</p></div>
          </header>
          <div class="field">
            <span class="field-label">Дата линии</span>
            <SegmentedControl block aria-label="Дата линии «сегодня»" :model-value="s.today?.auto === false ? 'fixed' : 'auto'" :options="TODAY_MODES" :disabled="readonly" @update:model-value="setTodayMode" />
          </div>
          <label v-if="s.today?.auto === false" class="field"><span class="field-label">Дата</span><input class="input" type="date" :value="s.today.date" :disabled="readonly" @change="$event.target.value && setToday({ date: $event.target.value })" /></label>
          <div class="field">
            <span class="field-label">Линия</span>
            <SegmentedControl block stacked aria-label="Вид линии «сегодня»" :model-value="s.today?.style || 'dashed'" :options="LINE_STYLES" :disabled="readonly" @update:model-value="setTodayStyle" />
          </div>
          <div class="field"><span class="field-label">Цвет</span><ColorPicker :model-value="s.today?.color || 'red'" :disabled="readonly" @update:model-value="(c) => setToday({ color: c })" /></div>
        </section>

        <section class="block" aria-labelledby="ps-bars">
          <header class="block-head">
            <span class="block-icon is-violet"><AppIcon name="gantt" :size="15" /></span>
            <div class="block-titles"><h3 id="ps-bars">Отображение</h3><p>Что рисовать на полосах задач</p></div>
          </header>
          <div class="rows">
            <ToggleSwitch v-for="o in BAR_OPTIONS" :key="o.key" :model-value="Boolean(d[o.key])" :label="o.label" :hint="o.hint" :disabled="readonly" @update:model-value="(v) => setDisplay(o.key, v)" />
            <label class="setting-row">
              <span class="row-text"><span class="row-label">Высота строки, px</span><span class="row-hint">От 28 до 80</span></span>
              <input class="input row-number" type="number" min="28" max="80" :value="s.rowHeight || 44" :disabled="readonly" @change="patch({ rowHeight: Math.max(28, Math.min(80, parseInt($event.target.value, 10) || 44)) })" />
            </label>
          </div>
        </section>

        <section class="block" aria-labelledby="ps-calendar">
          <header class="block-head">
            <span class="block-icon is-green"><AppIcon name="calendarCheck" :size="15" /></span>
            <div class="block-titles"><h3 id="ps-calendar">Календарь и связи</h3><p>Рабочие дни и поведение связанных задач</p></div>
          </header>
          <div class="rows">
            <ToggleSwitch v-for="o in CALENDAR_OPTIONS" :key="o.key" :model-value="Boolean(d[o.key])" :label="o.label" :hint="o.hint" :disabled="readonly" @update:model-value="(v) => setDisplay(o.key, v)" />
          </div>
        </section>

        <section class="block" aria-labelledby="ps-legend">
          <header class="block-head">
            <span class="block-icon is-amber"><AppIcon name="list" :size="15" /></span>
            <div class="block-titles"><h3 id="ps-legend">Легенда</h3><p>Цвета категорий и вехи рядом с графиком</p></div>
          </header>
          <div class="rows">
            <ToggleSwitch :model-value="Boolean(d.legendEnabled)" label="Показывать легенду" :disabled="readonly" @update:model-value="(v) => setDisplay('legendEnabled', v)" />
            <div class="setting-row" :class="{ 'is-off': !d.legendEnabled }">
              <span class="row-text"><span class="row-label">Где</span></span>
              <SegmentedControl size="sm" aria-label="Где легенда" :model-value="d.legendPosition" :options="LEGEND_POSITIONS" :disabled="readonly || !d.legendEnabled" @update:model-value="(v) => setLegend('legendPosition', v)" />
            </div>
            <div class="setting-row" :class="{ 'is-off': !d.legendEnabled }">
              <span class="row-text"><span class="row-label">Состав</span></span>
              <SegmentedControl size="sm" aria-label="Состав легенды" :model-value="d.legendMode" :options="LEGEND_MODES" :disabled="readonly || !d.legendEnabled" @update:model-value="(v) => setLegend('legendMode', v)" />
            </div>
          </div>
        </section>

        <section class="block" aria-labelledby="ps-tooltip">
          <header class="block-head">
            <span class="block-icon is-cyan"><AppIcon name="message" :size="15" /></span>
            <div class="block-titles"><h3 id="ps-tooltip">Подсказка у полосы</h3><p>Что показывать при наведении на полосу задачи</p></div>
          </header>
          <div class="chips" role="group" aria-labelledby="ps-tooltip">
            <label v-for="(label, key) in TOOLTIP" :key="key" class="chip-check" :class="{ 'is-on': (d.tooltipFields || []).includes(key) }">
              <input class="sr-only" type="checkbox" :checked="(d.tooltipFields || []).includes(key)" :disabled="readonly" @change="toggleTooltip(key)" />
              <span class="chip-box" aria-hidden="true"><AppIcon name="check" :size="11" :stroke-width="3" /></span>{{ label }}
            </label>
          </div>
        </section>

        <section class="block" aria-labelledby="ps-categories">
          <header class="block-head">
            <span class="block-icon is-pink"><AppIcon name="tag" :size="15" /></span>
            <div class="block-titles"><h3 id="ps-categories">Категории задач</h3><p>Справочник для карточки задачи и легенды</p></div>
            <span class="pill">{{ (s.categories || []).length }}</span>
          </header>
          <div v-if="(s.categories || []).length" class="categories">
            <span v-for="c in s.categories" :key="c" class="category">
              <span class="category-name">{{ c }}</span>
              <span v-if="categoryUsage[c]" class="category-count" :title="countLabel(categoryUsage[c], 'задача', 'задачи', 'задач')">{{ categoryUsage[c] }}</span>
              <button v-if="!readonly" type="button" title="Удалить" :aria-label="`Удалить категорию «${c}»`" @click="removeCategory(c)"><AppIcon name="close" :size="11" /></button>
            </span>
          </div>
          <p v-else class="field-hint">Категорий пока нет.</p>
          <form v-if="!readonly" class="add-category" @submit.prevent="addCategory">
            <input v-model="newCategory" class="input" maxlength="255" placeholder="Новая категория" aria-label="Новая категория" />
            <button class="btn" type="submit" :disabled="!newCategory.trim()"><AppIcon name="plus" :size="14" /> Добавить</button>
          </form>
        </section>

        <section class="block" aria-labelledby="ps-view">
          <header class="block-head">
            <span class="block-icon is-slate"><AppIcon name="layers" :size="15" /></span>
            <div class="block-titles"><h3 id="ps-view">Вид по умолчанию</h3><p>С ним коллеги впервые открывают план</p></div>
          </header>
          <div class="view-pills">
            <span class="pill">Шкала: {{ scaleLabel }}</span>
            <span class="pill">Масштаб: {{ Math.round(view.zoom * 100) }}%</span>
            <span class="pill">Свёрнуто групп: {{ collapsedCount }}</span>
          </div>
          <p class="field-hint">Масштаб, шкала, ширина колонки и свёрнутые группы у каждого свои. Можно сделать ваш текущий вид видом по умолчанию для коллег.</p>
          <div><button class="btn btn-sm" type="button" :disabled="readonly" @click="saveDefaultView"><AppIcon name="check" :size="14" /> Сделать мой вид видом по умолчанию</button></div>
        </section>
      </div>

      <aside class="settings-preview" aria-label="Предпросмотр">
        <div class="preview-head">
          <span class="preview-title">Предпросмотр</span>
          <span class="preview-badge"><span class="live-dot" />вживую</span>
        </div>
        <ChartPreview :settings="s" />
      </aside>
    </div>
    <template #footer>
      <span v-if="!readonly" class="footer-note"><AppIcon name="users" :size="14" /> Настройки общие — изменения сразу видны всем, кто работает с планом</span>
      <button class="btn btn-primary" type="button" @click="emit('close')">Готово</button>
    </template>
  </BaseModal>
</template>

<style scoped>
.settings { display: grid; grid-template-columns: minmax(0, 1fr) 296px; gap: 20px; align-items: start; }
.settings-main { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.readonly-note { display: flex; align-items: center; gap: 8px; margin: 0; padding: 9px 12px; border-radius: 10px; background: var(--color-warning-soft); color: #8a5a10; font-size: 12.5px; }

/* Раздел — мягкая карточка: значок, заголовок, пояснение в одну строку */
.block { display: flex; flex-direction: column; gap: 14px; padding: 16px; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-surface-2); }
.block-head { display: flex; align-items: flex-start; gap: 12px; }
.block-icon { width: 30px; height: 30px; border-radius: 9px; flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center; background: var(--color-primary-soft); color: var(--color-primary-dark); }
.block-icon.is-red { background: #fde8e8; color: #c62933; }
.block-icon.is-violet { background: #efe9fe; color: #6d3fd8; }
.block-icon.is-green { background: var(--color-success-soft); color: #1f7a4c; }
.block-icon.is-amber { background: var(--color-warning-soft); color: #a8670f; }
.block-icon.is-cyan { background: #dff6fa; color: #0e7490; }
.block-icon.is-pink { background: #fde7f3; color: #be185d; }
.block-icon.is-slate { background: #eef1f7; color: #475569; }
.block-titles { flex: 1; min-width: 0; }
.block-titles h3 { font-size: 13.5px; font-weight: 650; color: var(--color-text); line-height: 1.3; }
.block-titles p { margin: 2px 0 0; font-size: 12px; color: var(--color-text-muted); line-height: 1.4; }
.pill { display: inline-flex; align-items: center; height: 22px; padding: 0 9px; border-radius: 999px; background: #eef1f7; color: var(--color-text-secondary); font-size: 11.5px; font-weight: 600; white-space: nowrap; }
.block-head .pill { margin-top: 4px; }
.inline-action { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }

/* Список настроек-строк в белой карточке с разделителями */
.rows { display: flex; flex-direction: column; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 10px; padding: 2px 4px; }
.rows > * + * { border-top: 1px solid #eef0f5; border-radius: 0; }
.setting-row { display: flex; align-items: center; gap: 16px; min-height: 44px; padding: 8px 12px; flex-wrap: wrap; }
.setting-row.is-off .row-label { color: var(--color-text-muted); }
.row-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.row-label { font-size: 13px; color: var(--color-text); }
.row-hint { font-size: 11.5px; color: var(--color-text-muted); }
.row-number { width: 84px; flex-shrink: 0; text-align: center; }

/* Поля подсказки — отмечаемые «чипы» с галочкой */
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip-check { display: inline-flex; align-items: center; gap: 7px; min-height: 32px; padding: 5px 12px 5px 8px; border: 1px solid var(--color-border); border-radius: 999px; background: var(--color-surface); font-size: 12.5px; color: var(--color-text-secondary); cursor: pointer; user-select: none; transition: background 0.15s, border-color 0.15s, color 0.15s; }
.chip-check:hover { border-color: var(--color-border-strong); color: var(--color-text); }
.chip-check.is-on { background: var(--color-primary-soft); border-color: #c9d6ff; color: var(--color-primary-dark); font-weight: 600; }
.chip-box { width: 16px; height: 16px; border-radius: 5px; border: 1.5px solid var(--color-border-strong); display: inline-flex; align-items: center; justify-content: center; color: transparent; background: var(--color-surface); transition: background 0.15s, border-color 0.15s; }
.chip-check.is-on .chip-box { background: var(--color-primary); border-color: var(--color-primary); color: #fff; }
.chip-check:has(input:focus-visible) { outline: 2px solid var(--color-primary); outline-offset: 2px; }
.chip-check:has(input:disabled) { cursor: not-allowed; opacity: 0.7; }
.chip-check.is-on:has(input:disabled) .chip-box { background: #9aa6c4; border-color: #9aa6c4; }

/* Категории */
.categories { display: flex; flex-wrap: wrap; gap: 6px; }
.category { display: inline-flex; align-items: center; gap: 6px; min-height: 28px; padding: 3px 4px 3px 11px; border: 1px solid var(--color-border); border-radius: 999px; background: var(--color-surface); font-size: 12.5px; }
.category-count { min-width: 18px; height: 18px; padding: 0 5px; border-radius: 999px; background: #eef1f7; color: var(--color-text-muted); font-size: 10.5px; font-weight: 700; display: inline-flex; align-items: center; justify-content: center; }
.category button { border: 0; background: transparent; color: var(--color-text-muted); width: 22px; height: 22px; padding: 0; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; transition: background 0.15s, color 0.15s; }
.category button:hover { background: var(--color-danger-soft); color: var(--color-danger); }
.category:not(:has(button)) { padding-right: 11px; }
.add-category { display: flex; gap: 8px; }
.add-category .input { flex: 1; min-width: 0; }

.view-pills { display: flex; flex-wrap: wrap; gap: 6px; }
.view-pills + .field-hint { margin: 0; line-height: 1.45; }

/* Предпросмотр: справа и не уезжает при прокрутке */
.settings-preview { position: sticky; top: 0; min-width: 0; display: flex; flex-direction: column; gap: 10px; padding: 14px; border-radius: 12px; background: linear-gradient(180deg, #f3f6ff 0%, #f8f9fc 100%); border: 1px solid #e2e8fb; }
.preview-head { display: flex; align-items: center; justify-content: space-between; }
.preview-title { font-size: 11px; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; color: var(--color-text-secondary); }
.preview-badge { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: var(--color-text-muted); }
.live-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--color-success); box-shadow: 0 0 0 3px var(--color-success-soft); }

.footer-note { display: inline-flex; align-items: center; gap: 6px; margin-right: auto; font-size: 12px; color: var(--color-text-muted); min-width: 0; }

@media (max-width: 820px) {
  .settings { grid-template-columns: minmax(0, 1fr); }
  /* Узко: предпросмотр — сверху и прокручивается вместе с настройками */
  .settings-preview { position: static; order: -1; }
  .settings-preview :deep(.tip-preview) { display: none; }
}
@media (max-width: 768px) {
  .block { padding: 14px; }
  .footer-note { display: none; }
  .row-number { width: 96px; }
  .chip-check { min-height: 40px; }
  .category { min-height: 40px; padding-top: 0; padding-bottom: 0; padding-right: 0; }
  .category button { width: 40px; height: 40px; }
}
</style>
