import { diffDays } from './dates'
import { buildIndex, groupSpan } from './tree'

/** Статистика плана (окно «Статистика»; показатели 1.0.x и несколько новых). */
export function planStatistics(items, assignees, today) {
  const tasks = items.filter((i) => i.type === 'task')
  const groups = items.filter((i) => i.type === 'group')
  const milestones = items.filter((i) => i.type === 'milestone')
  const total = tasks.length
  const byStatus = { new: 0, inwork: 0, blocked: 0, done: 0 }
  for (const t of tasks) byStatus[t.status || 'new'] = (byStatus[t.status || 'new'] || 0) + 1
  const offsets = tasks.map((t) => Number(t.offset || 0)).filter((o) => o !== 0)
  const positive = offsets.filter((o) => o > 0)
  const negative = offsets.filter((o) => o < 0)
  const avg = (list) => (list.length ? Math.round((list.reduce((a, b) => a + b, 0) / list.length) * 10) / 10 : 0)
  const durations = tasks.map((t) => diffDays(t.start, t.end) + 1)
  const overdueNow = tasks.filter((t) => t.status !== 'done' && t.end < today)
    .sort((a, b) => a.end.localeCompare(b.end))
  const byAssignee = new Map()
  const names = Object.fromEntries((assignees || []).map((a) => [a.key, a.name]))
  for (const t of tasks) {
    const name = t.assigneeKey ? (names[t.assigneeKey] || 'Исполнитель') : 'Не назначен'
    const entry = byAssignee.get(name) || { name, total: 0, done: 0, overdue: 0 }
    entry.total += 1
    if (t.status === 'done') entry.done += 1
    if (t.status !== 'done' && t.end < today) entry.overdue += 1
    byAssignee.set(name, entry)
  }
  const index = buildIndex(items)
  const groupStats = groups.map((g) => {
    const span = groupSpan(index, g.key)
    return { key: g.key, name: g.name, count: span?.count || 0, done: span?.done || 0, progress: span?.progress || 0,
      start: span?.start, end: span?.end, offset: span?.offset || 0 }
  }).filter((g) => g.count)
  return {
    total,
    groups: groups.length,
    milestones: milestones.length,
    byStatus,
    avgProgress: total ? Math.round(tasks.reduce((a, t) => a + Number(t.progress || 0), 0) / total) : 0,
    avgDuration: avg(durations),
    withOffset: offsets.length,
    avgOffset: avg(offsets),
    maxOverdue: positive.length ? Math.max(...positive) : 0,
    avgOverdue: avg(positive),
    avgEarly: avg(negative),
    topShifted: tasks.filter((t) => Number(t.offset || 0) > 0).sort((a, b) => b.offset - a.offset).slice(0, 10),
    overdueNow,
    byAssignee: [...byAssignee.values()].sort((a, b) => b.total - a.total),
    groupStats,
    upcomingMilestones: milestones.filter((m) => m.date >= today).sort((a, b) => a.date.localeCompare(b.date)).slice(0, 8),
  }
}
