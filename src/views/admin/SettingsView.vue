<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import { useUiStore } from '../../stores/ui'
import { useDialogs } from '../../composables/useDialogs'
import AppIcon from '../../components/common/AppIcon.vue'
import HelpLink from '../../components/common/HelpLink.vue'
import { formatDate } from '../../utils/format'

/** Производственный календарь: праздники и переносы для расчёта рабочих дней на всех планах. Вход через домен — в «Интеграциях». */
const ui = useUiStore()
const { prompt, confirm } = useDialogs()
const days = ref([])
const year = ref(new Date().getFullYear())

onMounted(() => loadDays())
async function loadDays() {
  try { days.value = await api.get('/calendar', { from: `${year.value}-01-01`, to: `${year.value}-12-31` }) } catch (err) { ui.error(err) }
}
const holidays = computed(() => days.value.filter((d) => d.kind === 'holiday'))
const workdays = computed(() => days.value.filter((d) => d.kind === 'workday'))
async function addDay(kind) {
  const date = await prompt({ title: kind === 'holiday' ? 'Нерабочий день' : 'Рабочий выходной', label: 'Дата', inputType: 'date', value: `${year.value}-01-01`, confirmText: 'Далее' })
  if (!date) return
  const title = await prompt({ title: 'Описание', label: 'Например, «Перенос выходного»', value: kind === 'holiday' ? 'Нерабочий день' : 'Рабочая суббота', confirmText: 'Добавить', required: false })
  if (title === null) return
  try {
    await api.put(`/calendar/${date}`, { kind, title })
    if (date.startsWith(String(year.value))) loadDays()
    else { year.value = Number(date.slice(0, 4)); loadDays() }
  } catch (err) { ui.error(err) }
}
async function removeDay(day) {
  if (!await confirm({ title: `Убрать ${formatDate(day.date)} из календаря?`, message: day.title, confirmText: 'Убрать' })) return
  try { await api.delete(`/calendar/${day.date}`); loadDays() } catch (err) { ui.error(err) }
}
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">Производственный календарь</h1>
      <HelpLink topic="admin-calendar" />
    </div>

    <div class="card block" data-testid="calendar-settings">
      <div class="year">
        <button class="btn btn-ghost btn-icon btn-sm" type="button" @click="year--; loadDays()"><AppIcon name="chevronLeft" /></button>
        <b>{{ year }}</b>
        <button class="btn btn-ghost btn-icon btn-sm" type="button" @click="year++; loadDays()"><AppIcon name="chevronRight" /></button>
        <span class="spacer" />
        <button class="btn btn-sm" type="button" @click="addDay('holiday')"><AppIcon name="plus" :size="14" /> Нерабочий день</button>
        <button class="btn btn-sm" type="button" @click="addDay('workday')"><AppIcon name="plus" :size="14" /> Рабочий выходной</button>
      </div>
      <p class="muted small">Официальные дни 2025–2027 годов заложены по постановлениям Правительства РФ. Дни следующих лет добавьте, когда выйдет постановление о переносе выходных. Календарь используется в расчёте рабочих дней и подсветке праздников на всех планах.</p>
      <p v-if="!days.length" class="muted">На {{ year }} год дней нет — считаются только субботы и воскресенья.</p>
      <div class="days">
        <section v-if="holidays.length">
          <h3 class="section-title">Нерабочие дни · {{ holidays.length }}</h3>
          <div v-for="d in holidays" :key="d.date" class="day"><span class="date">{{ formatDate(d.date) }}</span><span class="ellipsis">{{ d.title }}</span><span v-if="d.source === 'custom'" class="badge">своё</span><button class="btn btn-ghost btn-icon btn-sm" type="button" title="Убрать" @click="removeDay(d)"><AppIcon name="close" :size="13" /></button></div>
        </section>
        <section v-if="workdays.length">
          <h3 class="section-title">Рабочие выходные · {{ workdays.length }}</h3>
          <div v-for="d in workdays" :key="d.date" class="day"><span class="date">{{ formatDate(d.date) }}</span><span class="ellipsis">{{ d.title }}</span><span v-if="d.source === 'custom'" class="badge">своё</span><button class="btn btn-ghost btn-icon btn-sm" type="button" title="Убрать" @click="removeDay(d)"><AppIcon name="close" :size="13" /></button></div>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.block { padding: 16px 18px; display: flex; flex-direction: column; gap: 14px; }
.year { display: flex; align-items: center; gap: 8px; }
.days { display: grid; grid-template-columns: 1.4fr 1fr; gap: 20px; }
.day { display: flex; align-items: center; gap: 10px; padding: 4px 0; font-size: 13px; border-bottom: 1px solid #f0f2f6; }
.date { font-variant-numeric: tabular-nums; width: 86px; flex-shrink: 0; }
.day .ellipsis { flex: 1; }
@media (max-width: 768px) { .days { grid-template-columns: 1fr; } }
</style>
