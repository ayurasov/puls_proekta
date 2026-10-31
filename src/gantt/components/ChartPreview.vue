<script setup>
import { computed, inject, useId } from 'vue'
import { addDays, diffDays, formatDay, formatDayNoYear, isWeekend } from '../domain/dates'
import { BAR_HEIGHT, BAR_HEIGHT_WITH_NOTE, barLabel, fitText, legendEntries } from '../chartModel'
import { readableText, resolveColor, STATUS_BY_KEY, textColorOnWhite } from '../domain/colors'
import { useAuthStore } from '../../stores/auth'

/**
 * Предпросмотр настроек графика: две задачи-образца на двух неделях (выходные, праздник 4 ноября),
 * связь, временной сдвиг, пометка, линия «сегодня», легенда плана и подсказка у полосы — так,
 * как их нарисует диаграмма с выбранными настройками. Только показывает, ничего не сохраняет.
 */
const props = defineProps({ settings: { type: Object, required: true } })
const editor = inject('gantt')
const auth = useAuthStore()
const uid = useId()

const DAY = 20
const DAYS = 14
const HEADER = 20
const WIDTH = DAY * DAYS
const BASE = '2026-11-02' // понедельник
const HOLIDAY = '2026-11-04'
const TODAY_INDEX = 4
const NOTE_INDEX = 3
const A = { name: 'ТЗ', start: BASE, end: addDays(BASE, 5), offset: 1, progress: 60, color: 'blue', status: 'inwork' }
const B = { name: 'Разработка', start: addDays(BASE, 7), end: addDays(BASE, 13), offset: 0, progress: 30, color: 'purple', status: 'new' }

const display = computed(() => props.settings.display)
const rowHeight = computed(() => Math.max(28, Math.min(80, props.settings.rowHeight || 44)))
const height = computed(() => HEADER + rowHeight.value * 2)
const days = Array.from({ length: DAYS }, (_, i) => {
  const date = addDays(BASE, i)
  return { i, x: i * DAY, label: Number(date.slice(8)), weekend: isWeekend(date), holiday: date === HOLIDAY }
})
const shades = computed(() => days.filter((d) => (d.holiday && display.value.showHolidays && display.value.useRuCalendar)
  || (d.weekend && display.value.showWeekends)).map((d) => ({ x: d.x, kind: d.holiday && display.value.showHolidays && display.value.useRuCalendar ? 'holiday' : 'weekend' })))

/** Полоса задачи-образца в строке row — по тем же правилам, что у диаграммы (chartModel.rowModel). */
function barOf(task, row, withNote) {
  const top = HEADER + row * rowHeight.value
  const room = withNote && rowHeight.value >= 40
  const h = room ? BAR_HEIGHT_WITH_NOTE : BAR_HEIGHT
  const x = diffDays(BASE, task.start) * DAY
  const w = (diffDays(task.start, task.end) + 1) * DAY
  const fill = resolveColor(task.color).color
  const tail = task.offset > 0 ? { x: x + w, w: task.offset * DAY } : null
  return {
    x, w, h, y: room ? top + rowHeight.value - h - 6 : top + rowHeight.value / 2 - h / 2, mid: top + rowHeight.value / 2, top,
    fill, text: readableText(fill), label: fitText(barLabel(task, display.value), w),
    progress: display.value.showProgress ? task.progress : 0, tail,
    tailLabel: tail && display.value.showOffsetLabels ? `сдвиг +${task.offset} д (до ${formatDayNoYear(addDays(task.end, task.offset))})` : '',
  }
}
const notes = computed(() => display.value.showNotes)
const bars = computed(() => [barOf(A, 0, notes.value), barOf(B, 1, false)])
const note = computed(() => {
  if (!notes.value) return null
  const color = resolveColor('orange').color
  return { x: NOTE_INDEX * DAY + DAY / 2, y: HEADER + 2, color, textColor: textColorOnWhite(color), text: 'Согласование' }
})
/** Связь «окончание → начало» с обходом, как у диаграммы: начало второй полосы совпадает с концом сдвига первой. */
const arrow = computed(() => {
  if (!display.value.showDependencies) return ''
  const [a, b] = bars.value
  const x1 = a.x + a.w + (a.tail ? a.tail.w : 0)
  const yMid = a.mid + rowHeight.value / 2
  return `M${x1} ${a.mid} H${x1 + 8} V${yMid} H${b.x - 8} V${b.mid} H${b.x - 1}`
})
const todayLine = computed(() => {
  const t = props.settings.today || {}
  const style = t.style || 'dashed'
  return { x: TODAY_INDEX * DAY, color: resolveColor(t.color || 'red', 'red').color, dash: style === 'solid' ? null : style === 'dotted' ? '1.5 3' : '5 4' }
})

// Легенда — настоящая, по строкам плана
const legend = computed(() => {
  if (!display.value.legendEnabled) return null
  const entries = legendEntries(editor.itemList, display.value, editor.settings.categories || [])
  return { shown: entries.slice(0, 2), more: Math.max(0, entries.length - 2) }
})

const assigneeName = computed(() => editor.assigneeList[0]?.name || 'Ковалёва М. А.')
const tooltipRows = computed(() => {
  const fields = display.value.tooltipFields || []
  const rows = []
  if (fields.includes('status')) rows.push(['Статус', STATUS_BY_KEY[A.status].label, STATUS_BY_KEY[A.status].text])
  if (fields.includes('dates')) {
    rows.push(['Срок', `${formatDay(A.start)} — ${formatDay(A.end)} (${diffDays(A.start, A.end) + 1}\u00a0дн.)`])
    rows.push(['Сдвиг', `+${A.offset}\u00a0дн., до ${formatDay(addDays(A.end, A.offset))}`])
  }
  if (fields.includes('progress')) rows.push(['Прогресс', `${A.progress}%`])
  if (fields.includes('dependency')) rows.push(['Зависит от', 'Обследование'])
  if (fields.includes('assignee')) rows.push(['Исполнитель', assigneeName.value])
  if (fields.includes('lastChange')) {
    const who = auth.user?.name
    rows.push(['Изменено', new Date().toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }) + (who ? ` · ${who}` : '')])
  }
  return rows
})
</script>

<template>
  <div class="chart-preview">
    <div class="chart-box">
      <div v-if="legend && settings.display.legendPosition === 'top'" class="legend is-top">
        <template v-if="legend.shown.length">
          <span v-for="entry in legend.shown" :key="entry.kind + entry.label" class="legend-item"><span :class="entry.kind === 'milestone' ? 'legend-diamond' : 'legend-bar'" :style="{ background: entry.color }" /><span class="legend-label">{{ entry.label }}</span></span>
          <span v-if="legend.more" class="legend-more">+{{ legend.more }}</span>
        </template>
        <span v-else class="legend-empty">В плане пока нет категорий и вех</span>
      </div>
      <svg :viewBox="`0 0 ${WIDTH} ${height}`" class="chart-svg" role="img" aria-label="Образец графика с выбранными настройками">
        <defs>
          <pattern :id="`${uid}-hatch`" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="3" height="6" fill="rgba(255,255,255,.35)" /></pattern>
          <marker :id="`${uid}-arrow`" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L8 4 L0 8 z" fill="#94a3b8" /></marker>
        </defs>
        <!-- шапка: дни ноября -->
        <rect v-for="d in days" :key="'h' + d.i" :x="d.x" y="0" :width="DAY" :height="HEADER"
              :class="['h-cell', { weekend: d.weekend && settings.display.showWeekends, holiday: d.holiday && settings.display.showHolidays && settings.display.useRuCalendar, today: d.i === TODAY_INDEX }]" />
        <text v-for="d in days" :key="'t' + d.i" :x="d.x + DAY / 2" y="14" class="h-text" :class="{ today: d.i === TODAY_INDEX }">{{ d.label }}</text>
        <line x1="0" :x2="WIDTH" :y1="HEADER - 0.5" :y2="HEADER - 0.5" class="h-line" />
        <!-- выходные, праздник, сетка -->
        <rect v-for="s in shades" :key="'s' + s.x" :x="s.x" :y="HEADER" :width="DAY" :height="height - HEADER" :class="s.kind" />
        <path class="grid-lines" :d="days.map((d) => `M${d.x + 0.5} ${HEADER}V${height}`).join('')" />
        <line x1="0" :x2="WIDTH" :y1="HEADER + rowHeight - 0.5" :y2="HEADER + rowHeight - 0.5" class="row-line" />
        <line :x1="todayLine.x" :x2="todayLine.x" y1="0" :y2="height" class="today-line" :stroke="todayLine.color" :stroke-dasharray="todayLine.dash" />
        <!-- полосы -->
        <g v-for="(bar, i) in bars" :key="'b' + i">
          <rect v-if="bar.tail" class="tail" :x="bar.tail.x - 3" :y="bar.y + 3" :width="bar.tail.w + 3" :height="bar.h - 6" rx="4" />
          <rect :x="bar.x" :y="bar.y" :width="bar.w" :height="bar.h" rx="6" :fill="bar.fill" />
          <rect v-if="bar.progress" class="progress" :x="bar.x + 2" :y="bar.y + bar.h - 6" :width="(bar.w - 4) * bar.progress / 100" height="4" rx="2" />
          <rect v-if="bar.tail" :x="bar.tail.x" :y="bar.y + 3" :width="bar.tail.w" :height="bar.h - 6" :fill="`url(#${uid}-hatch)`" />
          <text class="bar-text" :class="bar.text === '#ffffff' ? 'on-dark' : 'on-light'" :x="bar.x + bar.w / 2" :y="bar.y + bar.h / 2 + 4" :fill="bar.text">{{ bar.label }}</text>
          <text v-if="bar.tailLabel" class="tail-text" :x="bar.tail.x + bar.tail.w + (settings.display.showDependencies ? 13 : 5)" :y="bar.y + bar.h / 2 + 3.5">{{ bar.tailLabel }}</text>
        </g>
        <path v-if="arrow" class="arrow" :d="arrow" :marker-end="`url(#${uid}-arrow)`" />
        <g v-if="note">
          <path :d="`M${note.x - 5} ${note.y + 5} a5 5 0 1 1 10 0 c0 3 -5 9 -5 9 c0 0 -5 -6 -5 -9 Z`" :fill="note.color" />
          <circle :cx="note.x" :cy="note.y + 5" r="1.8" fill="#fff" />
          <text :x="note.x + 8" :y="note.y + 9" :fill="note.textColor" class="note-text">{{ note.text }}</text>
        </g>
      </svg>
      <div v-if="legend && settings.display.legendPosition !== 'top'" class="legend">
        <template v-if="legend.shown.length">
          <span v-for="entry in legend.shown" :key="entry.kind + entry.label" class="legend-item"><span :class="entry.kind === 'milestone' ? 'legend-diamond' : 'legend-bar'" :style="{ background: entry.color }" /><span class="legend-label">{{ entry.label }}</span></span>
          <span v-if="legend.more" class="legend-more">+{{ legend.more }}</span>
        </template>
        <span v-else class="legend-empty">В плане пока нет категорий и вех</span>
      </div>
    </div>
    <div class="row-height muted">Высота строки — {{ rowHeight }} px</div>

    <div class="tip-preview">
      <div class="tip-caption">Подсказка при наведении</div>
      <div class="tip-card">
        <div class="tip-title">{{ A.name }}</div>
        <dl v-if="tooltipRows.length">
          <template v-for="row in tooltipRows" :key="row[0]">
            <dt>{{ row[0] }}</dt><dd :style="row[2] ? { color: row[2], fontWeight: 600 } : null">{{ row[1] }}</dd>
          </template>
        </dl>
        <div v-else class="tip-empty">Только название</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chart-preview { display: flex; flex-direction: column; gap: 8px; }
.chart-box { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 10px; overflow: hidden; box-shadow: var(--shadow-1); }
.chart-svg { display: block; width: 100%; height: auto; transition: height 0.15s; }
.h-cell { fill: var(--color-surface-2); }
.h-cell.weekend { fill: #eef0f4; }
.h-cell.holiday { fill: #fde8e8; }
.h-cell.today { fill: var(--color-primary-soft); }
.h-text { font-size: 10px; fill: var(--color-text-muted); text-anchor: middle; font-weight: 600; }
.h-text.today { fill: var(--color-primary-dark); }
.h-line, .row-line { stroke: var(--color-border); stroke-width: 1; }
.row-line { stroke: #f0f2f6; }
.weekend { fill: rgba(120, 128, 150, 0.09); }
.holiday { fill: rgba(239, 68, 68, 0.1); }
.grid-lines { stroke: #f0f2f6; stroke-width: 1; }
.today-line { stroke-width: 2; }
.progress { fill: rgba(0, 0, 0, 0.3); }
.tail { fill: #ef4444; opacity: 0.6; }
.bar-text { font-size: 11px; font-weight: 700; text-anchor: middle; font-variant-numeric: tabular-nums; paint-order: stroke; stroke-linejoin: round; }
.bar-text.on-dark { stroke: rgba(15, 23, 42, 0.32); stroke-width: 2.2px; }
.bar-text.on-light { stroke: rgba(255, 255, 255, 0.55); stroke-width: 2.2px; }
.tail-text { font-size: 10px; font-weight: 700; fill: #b91c1c; }
.arrow { fill: none; stroke: #a7b0c2; stroke-width: 1.3; }
.note-text { font-size: 10px; font-weight: 650; paint-order: stroke; stroke: #fff; stroke-width: 3px; }
.legend { display: flex; align-items: center; gap: 10px; padding: 5px 10px; border-top: 1px solid var(--color-border); font-size: 10.5px; color: var(--color-text-secondary); overflow: hidden; white-space: nowrap; }
.legend.is-top { border-top: 0; border-bottom: 1px solid var(--color-border); }
.legend-item { display: inline-flex; align-items: center; gap: 5px; min-width: 0; }
.legend-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.legend-bar { width: 12px; height: 6px; border-radius: 2px; flex-shrink: 0; }
.legend-diamond { width: 7px; height: 7px; transform: rotate(45deg); flex-shrink: 0; }
.legend-more { color: var(--color-text-muted); font-weight: 600; flex-shrink: 0; margin-left: auto; }
.legend-empty { color: var(--color-text-muted); }
.row-height { font-size: 11px; text-align: right; }
.tip-preview { display: flex; flex-direction: column; gap: 6px; margin-top: 4px; }
.tip-caption { font-size: 11px; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; color: var(--color-text-muted); }
.tip-card { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 10px; box-shadow: var(--shadow-2); padding: 10px 12px; font-size: 11.5px; }
.tip-title { font-weight: 700; font-size: 12px; margin-bottom: 4px; }
.tip-card dl { display: grid; grid-template-columns: auto 1fr; gap: 3px 10px; margin: 0; }
.tip-card dt { color: var(--color-text-muted); }
.tip-card dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.tip-empty { color: var(--color-text-muted); }
</style>
