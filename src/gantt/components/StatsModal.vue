<script setup>
import { computed, inject } from 'vue'
import BaseModal from '../../components/common/BaseModal.vue'
import { planStatistics } from '../domain/stats'
import { STATUSES } from '../domain/colors'
import { formatDay } from '../domain/dates'
import { formatNumber } from '../../utils/format'

const emit = defineEmits(['close'])
const editor = inject('gantt')
const stats = computed(() => planStatistics(editor.itemList, editor.assigneeList, editor.state.today))
const pct = (n, total) => (total ? Math.round((n / total) * 100) : 0)
function select(key) {
  editor.revealItem(key)
  editor.select(key, { openPanel: true })
  emit('close')
}
</script>

<template>
  <BaseModal :title="`Статистика: ${editor.state.plan?.title}`" size="wide" @close="emit('close')">
    <div class="cards">
      <div class="card stat"><span class="muted small">Задач</span><strong>{{ stats.total }}</strong></div>
      <div class="card stat"><span class="muted small">Средний прогресс</span><strong>{{ stats.avgProgress }}%</strong></div>
      <div class="card stat"><span class="muted small">Просрочено сейчас</span><strong :class="{ bad: stats.overdueNow.length }">{{ stats.overdueNow.length }}</strong></div>
      <div class="card stat"><span class="muted small">Групп / вех</span><strong>{{ stats.groups }} / {{ stats.milestones }}</strong></div>
      <div class="card stat"><span class="muted small">Средняя длительность</span><strong>{{ formatNumber(stats.avgDuration) }} дн.</strong></div>
    </div>

    <section>
      <h3>Статусы</h3>
      <div class="stack">
        <span v-for="s in STATUSES" :key="s.key" :style="{ width: pct(stats.byStatus[s.key], stats.total) + '%', background: s.color }" :title="`${s.label}: ${stats.byStatus[s.key]}`" />
      </div>
      <div class="legend">
        <span v-for="s in STATUSES" :key="s.key"><span class="status-dot" :style="{ background: s.color }" /> {{ s.label }}: <b>{{ stats.byStatus[s.key] }}</b> ({{ pct(stats.byStatus[s.key], stats.total) }}%)</span>
      </div>
    </section>

    <div class="two">
      <section>
        <h3>Сдвиги сроков</h3>
        <dl class="kv">
          <dt>Задач со сдвигом</dt><dd>{{ stats.withOffset }} из {{ stats.total }}</dd>
          <dt>Среднее отклонение</dt><dd>{{ stats.avgOffset > 0 ? '+' : '' }}{{ formatNumber(stats.avgOffset) }} дн.</dd>
          <dt>Максимальная просрочка</dt><dd>{{ stats.maxOverdue ? '+' + formatNumber(stats.maxOverdue) : 0 }} дн.</dd>
          <dt>Средняя просрочка</dt><dd>{{ stats.avgOverdue ? '+' + formatNumber(stats.avgOverdue) : 0 }} дн.</dd>
          <dt>Среднее опережение</dt><dd>{{ formatNumber(stats.avgEarly) }} дн.</dd>
        </dl>
        <template v-if="stats.topShifted.length">
          <div class="muted small sub">Больше всего сдвинуты</div>
          <button v-for="t in stats.topShifted" :key="t.key" class="link-row" type="button" @click="select(t.key)"><b class="bad">+{{ t.offset }} д</b> {{ t.name }}</button>
        </template>
      </section>
      <section>
        <h3>Просрочено на сегодня</h3>
        <p v-if="!stats.overdueNow.length" class="muted small">Незавершённых задач с прошедшим сроком нет.</p>
        <button v-for="t in stats.overdueNow.slice(0, 12)" :key="t.key" class="link-row" type="button" @click="select(t.key)">
          <span class="muted">{{ formatDay(t.end) }}</span> {{ t.name }}
        </button>
        <template v-if="stats.upcomingMilestones.length">
          <h3 class="sub">Ближайшие вехи</h3>
          <button v-for="m in stats.upcomingMilestones" :key="m.key" class="link-row" type="button" @click="select(m.key)">◆ <span class="muted">{{ formatDay(m.date) }}</span> {{ m.name }}</button>
        </template>
      </section>
    </div>

    <div class="two">
      <section v-if="stats.byAssignee.length">
        <h3>По исполнителям</h3>
        <div v-for="a in stats.byAssignee" :key="a.name" class="bar-row">
          <div class="bar-head"><span>{{ a.name }}</span><span><b>{{ a.total }}</b> · завершено {{ a.done }}<span v-if="a.overdue" class="bad"> · просрочено {{ a.overdue }}</span></span></div>
          <div class="bar"><span :style="{ width: pct(a.total, stats.total) + '%' }" /></div>
        </div>
      </section>
      <section v-if="stats.groupStats.length">
        <h3>По группам</h3>
        <div v-for="g in stats.groupStats" :key="g.key" class="bar-row">
          <div class="bar-head"><button class="link" type="button" @click="select(g.key)">{{ g.name }}</button><span>{{ g.done }}/{{ g.count }} · {{ g.progress }}%</span></div>
          <div class="bar green"><span :style="{ width: g.progress + '%' }" /></div>
        </div>
      </section>
    </div>
  </BaseModal>
</template>

<style scoped>
.cards { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-bottom: 16px; }
.stat { padding: 12px; display: flex; flex-direction: column; gap: 4px; box-shadow: none; }
.stat strong { font-size: 22px; }
.bad { color: #c62933; }
section { margin-bottom: 16px; display: flex; flex-direction: column; gap: 6px; }
h3 { font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-text-muted); }
.sub { margin-top: 8px; }
.stack { display: flex; height: 10px; border-radius: 5px; overflow: hidden; background: #eef1f7; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 12.5px; }
.legend > span { display: inline-flex; align-items: center; gap: 5px; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.kv { display: grid; grid-template-columns: 1fr auto; gap: 4px 12px; margin: 0; font-size: 13px; }
.kv dt { color: var(--color-text-secondary); }
.kv dd { margin: 0; font-weight: 600; }
.link-row { border: 0; background: none; text-align: left; padding: 3px 0; font-size: 12.5px; color: var(--color-text); display: flex; gap: 6px; }
.link-row:hover { color: var(--color-primary-dark); }
.link { border: 0; background: none; padding: 0; font-size: inherit; color: var(--color-primary-dark); }
.bar-row { display: flex; flex-direction: column; gap: 3px; font-size: 12.5px; }
.bar-head { display: flex; justify-content: space-between; gap: 8px; }
.bar { height: 5px; background: #eef1f7; border-radius: 3px; overflow: hidden; }
.bar span { display: block; height: 100%; background: var(--color-primary); }
.bar.green span { background: var(--color-success); }
@media (max-width: 768px) { .cards { grid-template-columns: 1fr 1fr; } .two { grid-template-columns: 1fr; } }
</style>
