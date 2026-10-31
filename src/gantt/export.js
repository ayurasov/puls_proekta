/**
 * Экспорт плана: PNG, PDF, SVG (векторная схема из той же модели, что и экран), CSV для
 * Excel и JSON. В 1.0.x PNG/PDF делались снимком страницы (html2canvas с CDN) — без
 * интернета не работали, а длинные планы обрезались.
 */
import { api, saveBlob } from '../api/client'
import { createChartContext, dependencyPaths, legendEntries, rowModel } from './chartModel'
import { diffDays, formatDay, formatDayNoYear } from './domain/dates'
import { STATUS_BY_KEY, DEPENDENCY_TYPES, resolveColor } from './domain/colors'
import { filterChips } from './domain/filters'
import { formatDateTime } from '../utils/format'

const FONT = '-apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif'

function esc(value) {
  return String(value ?? '').replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[ch])
}

function fileBase(editor) {
  return (editor.state.plan?.title || 'План').replace(/[\\/:*?"<>|]+/g, '_').slice(0, 120)
}

function dash(style) {
  return style === 'solid' ? '' : style === 'dotted' ? ' stroke-dasharray="1.5 3"' : ' stroke-dasharray="5 4"'
}

/** Что отобрано поиском и фильтрами — в подписи выгрузки, чтобы часть плана не выглядела всем планом. */
function filterNote(editor) {
  if (!editor.filtersActive) return ''
  const view = editor.state.view
  const parts = filterChips(view, { assignees: editor.assigneeList })
    .filter((c) => c.group !== 'jira' || editor.filterContext.jira).map((c) => c.label)
  if ((view.search || '').trim()) parts.unshift(`поиск «${view.search.trim()}»`)
  const text = `фильтры: ${parts.join(', ')}`
  return text.length > 140 ? `${text.slice(0, 139)}…` : text
}

/** SVG всего плана (с учётом фильтров и свёрнутых групп, но по всей длине шкалы). */
export function buildSvg(editor, { projectTitle = '' } = {}) {
  const view = editor.state.view
  const rowHeight = editor.settings.rowHeight || 44
  const display = editor.settings.display
  const ctx = createChartContext({
    range: editor.range, scale: view.scale, zoom: view.zoom, rowHeight, display, calendar: editor.calendar,
    today: editor.todayMarker, index: editor.index, notes: editor.noteList, assignees: editor.assigneeList,
  })
  const rows = editor.visibleRows
  const models = rows.map((row, i) => rowModel(row, i, ctx))
  const gridW = Math.max(260, view.gridWidth)
  const titleH = 56
  const legend = display.legendEnabled ? legendEntries(editor.itemList, display, editor.settings.categories || []) : []
  const bodyH = rows.length * rowHeight
  const width = gridW + ctx.width
  // Легенда в выгрузке — строками по ширине изображения
  const legendRows = []
  let legendX = 16
  for (const entry of legend) {
    const label = entry.label.length > 60 ? entry.label.slice(0, 59) + '…' : entry.label
    const w = 34 + label.length * 6.5
    if (!legendRows.length || (legendX + w > width - 16 && legendX > 16)) { legendRows.push([]); legendX = 16 }
    legendRows[legendRows.length - 1].push({ ...entry, label, x: legendX })
    legendX += w
  }
  const legendH = legendRows.length ? 12 + legendRows.length * 20 : 0
  const height = titleH + ctx.headerHeight + bodyH + legendH + 12
  const assigneeName = (key) => editor.assigneeList.find((a) => a.key === key)?.name || ''
  const out = []
  out.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" font-family="${FONT}">`)
  out.push('<defs><marker id="ar" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L8 4 L0 8 z" fill="#94a3b8"/></marker>'
    + '<pattern id="hl" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="3" height="6" fill="rgba(255,255,255,.35)"/></pattern></defs>')
  out.push(`<rect width="${width}" height="${height}" fill="#ffffff"/>`)
  out.push(`<text x="16" y="26" font-size="17" font-weight="700" fill="#1c2331">${esc(editor.state.plan?.title)}</text>`)
  out.push(`<text x="16" y="45" font-size="11.5" fill="#6b7280">${esc([projectTitle, editor.state.plan?.subtitle, `шкала ${formatDay(editor.range.start)} — ${formatDay(editor.range.end)}`, filterNote(editor), `выгружено ${formatDateTime(new Date())}`].filter(Boolean).join(' · '))}</text>`)

  // Заголовок шкалы
  const hy = titleH
  out.push(`<g transform="translate(${gridW},${hy})">`)
  for (const cell of ctx.header.top) {
    out.push(`<rect x="${cell.x}" y="0" width="${cell.w}" height="22" fill="#fafbfd" stroke="#e3e6ee" stroke-width="0.5"/>`)
    out.push(`<text x="${cell.x + 6}" y="15" font-size="11" font-weight="700" fill="#4b5568">${esc(cell.label)}</text>`)
  }
  for (const cell of ctx.header.bottom) {
    const fill = cell.today ? '#e6ecff' : cell.holiday && display.showHolidays ? '#fde8e8' : cell.weekend && display.showWeekends && !cell.workingWeekend ? '#eef0f4' : '#fafbfd'
    out.push(`<rect x="${cell.x}" y="22" width="${cell.w}" height="${ctx.bottomRow}" fill="${fill}" stroke="#eceef4" stroke-width="0.5"/>`)
    if (cell.label) out.push(`<text x="${cell.x + cell.w / 2}" y="${22 + (cell.sub ? 13 : 16)}" font-size="11.5" font-weight="650" fill="#4b5568" text-anchor="middle">${esc(cell.label)}</text>`)
    if (cell.sub) out.push(`<text x="${cell.x + cell.w / 2}" y="${22 + 26}" font-size="10" fill="#6b7280" text-anchor="middle">${esc(cell.sub)}</text>`)
  }
  if (ctx.laneHeight) {
    const ly = 22 + ctx.bottomRow
    out.push(`<rect x="0" y="${ly}" width="${ctx.width}" height="${ctx.laneHeight}" fill="#fbfcfe"/>`)
    for (const label of ctx.lane) {
      const text = label.kind === 'note' ? (label.showDate ? `${formatDayNoYear(label.date)} · ` : '') + label.text : label.text
      out.push(`<text x="${label.x + (label.kind === 'note' ? 7 : 0)}" y="${ly + 16 + label.level * 15}" font-size="10" font-weight="700" fill="${label.textColor || label.color}" text-anchor="${label.kind === 'note' ? 'start' : 'middle'}" paint-order="stroke" stroke="#fbfcfe" stroke-width="3">${esc(text)}</text>`)
      if (label.kind === 'note') out.push(`<path d="M${label.x - 4} ${ly + 6 + label.level * 15} l4 9 l4 -9 a4 4 0 1 0 -8 0 Z" fill="${label.color}"/>`)
    }
  }
  out.push('</g>')

  // Левая колонка
  const by = titleH + ctx.headerHeight
  out.push(`<rect x="0" y="${titleH}" width="${gridW}" height="${ctx.headerHeight}" fill="#fafbfd"/>`)
  out.push(`<text x="12" y="${titleH + ctx.headerHeight - 10}" font-size="11" font-weight="700" fill="#6b7280">ЗАДАЧА</text>`)
  rows.forEach((row, i) => {
    const y = by + i * rowHeight
    const item = row.item
    if (i % 2) out.push(`<rect x="0" y="${y}" width="${width}" height="${rowHeight}" fill="#fcfcfe"/>`)
    out.push(`<line x1="0" x2="${width}" y1="${y + rowHeight}" y2="${y + rowHeight}" stroke="#f0f2f6"/>`)
    const x = 12 + row.depth * 16
    const meta = item.type === 'task'
      ? [STATUS_BY_KEY[item.status || 'new'].label, assigneeName(item.assigneeKey), item.category].filter(Boolean).join(' · ')
      : item.type === 'milestone' ? formatDay(item.date) : ''
    const mark = item.type === 'task'
      ? `<circle cx="${x + 4}" cy="${y + rowHeight / 2}" r="4" fill="${STATUS_BY_KEY[item.status || 'new'].color}"/>`
      : item.type === 'milestone'
        ? `<path d="M${x + 4} ${y + rowHeight / 2 - 5} l5 5 l-5 5 l-5 -5 Z" fill="#14b8a6"/>`
        : `<rect x="${x}" y="${y + rowHeight / 2 - 4}" width="9" height="8" rx="2" fill="#94a3b8"/>`
    out.push(mark)
    const nameY = meta && rowHeight >= 36 ? y + rowHeight / 2 - 2 : y + rowHeight / 2 + 4
    const maxChars = Math.floor((gridW - x - 24) / 6.4)
    const name = item.name.length > maxChars ? item.name.slice(0, maxChars - 1) + '…' : item.name
    out.push(`<text x="${x + 14}" y="${nameY}" font-size="12" font-weight="${item.type === 'group' ? 700 : 500}" fill="#1c2331">${esc(name)}</text>`)
    if (meta && rowHeight >= 36) out.push(`<text x="${x + 14}" y="${nameY + 14}" font-size="11" fill="#6b7280">${esc(meta.length > maxChars + 6 ? meta.slice(0, maxChars + 5) + '…' : meta)}</text>`)
  })
  out.push(`<line x1="${gridW}" x2="${gridW}" y1="${titleH}" y2="${by + bodyH}" stroke="#e3e6ee"/>`)

  // Тело шкалы
  out.push(`<g transform="translate(${gridW},${by})">`)
  for (const s of ctx.shades) out.push(`<rect x="${s.x}" y="0" width="${s.w}" height="${bodyH}" fill="${s.kind === 'holiday' ? 'rgba(239,68,68,0.08)' : 'rgba(120,128,150,0.07)'}"/>`)
  out.push(`<path d="${ctx.gridLines.map((x) => `M${x + 0.5} 0V${bodyH}`).join('')}" stroke="#f0f2f6"/>`)
  if (ctx.todayX !== null) {
    const color = resolveColor(editor.settings.today?.color || 'red', 'red').color
    out.push(`<line x1="${ctx.todayX}" x2="${ctx.todayX}" y1="${-ctx.headerHeight + 22}" y2="${bodyH}" stroke="${color}" stroke-width="2"${dash(editor.settings.today?.style)}/>`)
  }
  if (display.showDependencies) {
    for (const a of dependencyPaths(models, ctx)) out.push(`<path d="${a.d}" fill="none" stroke="#a7b0c2" stroke-width="1.3" marker-end="url(#ar)"/>`)
  }
  for (const m of models) {
    if (m.bar) {
      const b = m.bar
      if (b.tail?.kind === 'late') out.push(`<rect x="${b.tail.x - 3}" y="${b.y + 3}" width="${b.tail.w + 3}" height="${b.h - 6}" rx="4" fill="#ef4444" opacity="0.6"/><rect x="${b.tail.x}" y="${b.y + 3}" width="${b.tail.w}" height="${b.h - 6}" fill="url(#hl)"/>`)
      out.push(`<rect x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="6" fill="${b.fill}"${b.status === 'done' ? ' opacity="0.78"' : b.status === 'blocked' ? ' opacity="0.7"' : ''}/>`)
      if (b.progress) out.push(`<rect x="${b.x + 2}" y="${b.y + b.h - 6}" width="${Math.max(0, (b.w - 4) * b.progress / 100)}" height="4" rx="2" fill="rgba(0,0,0,0.3)"/>`)
      if (b.tail?.kind === 'early') out.push(`<rect x="${b.tail.x}" y="${b.y}" width="${b.tail.w}" height="${b.h}" rx="6" fill="#22c55e" opacity="0.7"/>`)
      if (b.status === 'done') out.push(`<rect x="${b.x + 1}" y="${b.y + 1}" width="${b.w - 2}" height="${b.h - 2}" rx="5" fill="none" stroke="#22c55e" stroke-width="2.5"/>`)
      if (b.status === 'blocked') out.push(`<rect x="${b.x + 1}" y="${b.y + 1}" width="${b.w - 2}" height="${b.h - 2}" rx="5" fill="none" stroke="#e5484d" stroke-width="2" stroke-dasharray="4 3"/>`)
      const halo = b.text === '#ffffff' ? 'rgba(15,23,42,0.32)' : 'rgba(255,255,255,0.55)'
      if (b.label) out.push(`<text x="${b.x + b.w / 2}" y="${b.y + b.h / 2 + 4}" font-size="11.5" font-weight="700" fill="${b.text}" text-anchor="middle" paint-order="stroke" stroke="${halo}" stroke-width="2.4" stroke-linejoin="round">${esc(b.label)}</text>`)
      if (b.tailLabel && b.tail?.kind === 'late') out.push(`<text x="${b.tail.x + b.tail.w + 5}" y="${b.y + b.h / 2 + 4}" font-size="10.5" font-weight="700" fill="#c53030" paint-order="stroke" stroke="#fff" stroke-width="3">${esc(b.tailLabel)}</text>`)
    }
    if (m.group) {
      const g = m.group
      out.push(`<path d="M${g.x} ${g.y} h${g.w} v${g.h + 5} l-5 -5 h-${Math.max(0, g.w - 10)} l-5 5 Z" fill="${g.fill}" opacity="0.6"/>`)
      if (g.w > 70) out.push(`<text x="${g.x + g.w / 2}" y="${g.y - 4}" font-size="10.5" font-weight="700" fill="#4b5568" text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="3">${esc(g.label)}</text>`)
      if (g.tail?.kind === 'late') out.push(`<rect x="${g.x + g.w}" y="${g.y + 2}" width="${g.tail.w}" height="${g.h - 4}" rx="2" fill="#ef4444" opacity="0.6"/>`)
    }
    if (m.milestone) {
      const ms = m.milestone
      out.push(`<path d="M${ms.x} ${ms.y - 9} l9 9 l-9 9 l-9 -9 Z" fill="${ms.fill}" stroke="#fff" stroke-width="1.5"/>`)
      out.push(`<text x="${ms.x + 13}" y="${ms.y + 4}" font-size="11.5" font-weight="600" fill="#1c2331" paint-order="stroke" stroke="#fff" stroke-width="3">${esc(ms.label)}</text>`)
    }
    for (const n of m.notes) {
      out.push(`<path d="M${n.x - 5} ${n.y + 5} a5 5 0 1 1 10 0 c0 3 -5 9 -5 9 c0 0 -5 -6 -5 -9 Z" fill="${n.color}"/>`)
      out.push(`<text x="${n.x + 8}" y="${n.y + 9}" font-size="10.5" font-weight="650" fill="${n.textColor || n.color}" paint-order="stroke" stroke="#fff" stroke-width="3">${esc((n.showDate ? `${formatDayNoYear(n.date)} · ` : '') + n.text.slice(0, 60))}</text>`)
    }
  }
  for (const line of ctx.milestoneLines) out.push(`<line x1="${line.x}" x2="${line.x}" y1="${-ctx.laneHeight}" y2="${bodyH}" stroke="${line.color}" stroke-width="2"${dash(line.style)}/>`)
  out.push('</g>')

  legendRows.forEach((line, row) => {
    const y = by + bodyH + 20 + row * 20
    for (const entry of line) {
      const x = entry.x
      out.push(entry.kind === 'milestone'
        ? `<path d="M${x + 4} ${y - 8} l4 4 l-4 4 l-4 -4 Z" fill="${entry.color}"/>`
        : `<rect x="${x}" y="${y - 8}" width="16" height="8" rx="2" fill="${entry.color}"/>`)
      out.push(`<text x="${x + 22}" y="${y}" font-size="11" fill="#4b5568">${esc(entry.label)}</text>`)
    }
  })
  out.push('</svg>')
  return { svg: out.join(''), width, height }
}

async function svgToCanvas(svg, width, height) {
  const scale = Math.min(2, 16000 / width, 16000 / height)
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(width * scale)
  canvas.height = Math.round(height * scale)
  const ctx = canvas.getContext('2d')
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }))
  try {
    const img = new Image()
    await new Promise((resolve, reject) => {
      img.onload = resolve
      img.onerror = () => reject(new Error('Не удалось построить изображение'))
      img.src = url
    })
    ctx.fillStyle = '#fff'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
  } finally {
    URL.revokeObjectURL(url)
  }
  return canvas
}

function csvCell(value) {
  let text = String(value ?? '')
  // Текст, который Excel принял бы за формулу (=, +, -, @), — с апострофом; числа («-3») не трогаем
  if (/^[=+\-@\t\r]/.test(text) && !/^-?\d+([.,]\d+)?$/.test(text)) text = `'${text}`
  return /[;"\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

export function buildCsv(editor) {
  const assignee = (key) => editor.assigneeList.find((a) => a.key === key)?.name || ''
  const header = ['Уровень', 'Тип', 'Название', 'Статус', 'Начало', 'Окончание', 'Длительность, дн.', 'Прогресс, %', 'Сдвиг, дн.',
    'Дата завершения', 'Исполнитель', 'Категория', 'Предшественник', 'Тип связи', 'Лаг, дн.', 'Jira', 'Комментариев']
  const lines = [header.map(csvCell).join(';')]
  const types = { task: 'Задача', milestone: 'Веха', group: 'Группа' }
  for (const { item, depth } of editor.visibleRows) {
    const task = item.type === 'task'
    lines.push([
      depth + 1, types[item.type], '  '.repeat(depth) + item.name,
      task ? STATUS_BY_KEY[item.status || 'new'].label : '',
      task ? formatDay(item.start) : item.type === 'milestone' ? formatDay(item.date) : '',
      task ? formatDay(item.end) : '',
      task ? diffDays(item.start, item.end) + 1 : '',
      task ? item.progress || 0 : '', task ? item.offset || 0 : '',
      task && item.completedDate ? formatDay(item.completedDate) : '',
      assignee(item.assigneeKey), item.category || '',
      item.dependencyKey ? editor.state.items[item.dependencyKey]?.name || '' : '',
      item.dependencyKey ? DEPENDENCY_TYPES.find((t) => t.key === item.dependencyType)?.label || '' : '',
      item.dependencyKey ? item.dependencyLag || 0 : '', item.jiraUrl || '', item.commentCount || 0,
    ].map(csvCell).join(';'))
  }
  return '﻿' + lines.join('\r\n')
}

export async function exportPlan(editor, format, { projectTitle = '' } = {}) {
  const base = fileBase(editor)
  if (format === 'json') {
    const state = await api.get(`/plans/${editor.state.planId}/export`)
    saveBlob(new Blob([JSON.stringify(state, null, 2)], { type: 'application/json' }), `${base}.json`)
    return
  }
  if (format === 'csv') {
    saveBlob(new Blob([buildCsv(editor)], { type: 'text/csv;charset=utf-8' }), `${base}.csv`)
    return
  }
  const { svg, width, height } = buildSvg(editor, { projectTitle })
  if (format === 'svg') {
    saveBlob(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }), `${base}.svg`)
    return
  }
  const canvas = await svgToCanvas(svg, width, height)
  if (format === 'png') {
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'))
    saveBlob(blob, `${base}.png`)
    return
  }
  if (format === 'pdf') {
    const { jsPDF } = await import('jspdf')
    const mmW = width * 0.264583
    const mmH = height * 0.264583
    const pdf = new jsPDF({ orientation: mmW > mmH ? 'landscape' : 'portrait', unit: 'mm', format: [mmW, mmH], compress: true })
    pdf.addImage(canvas.toDataURL('image/jpeg', 0.92), 'JPEG', 0, 0, mmW, mmH)
    pdf.save(`${base}.pdf`)
  }
}
