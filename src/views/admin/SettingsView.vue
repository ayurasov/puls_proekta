<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import { useUiStore } from '../../stores/ui'
import { useDialogs } from '../../composables/useDialogs'
import AppIcon from '../../components/common/AppIcon.vue'
import HelpLink from '../../components/common/HelpLink.vue'
import { formatDate } from '../../utils/format'

const ui = useUiStore()
const { prompt, confirm } = useDialogs()
const tab = ref('ldap')
const ldap = ref(null)
const dirty = ref(false)
const testResult = ref(null)
const testing = ref(false)
const days = ref([])
const year = ref(new Date().getFullYear())

const USERNAME = '{' + '{username}' + '}'
const MODES = {
  local: 'Только пароли системы',
  ldap: 'Только учётная запись домена (локальные администраторы сохраняют вход по паролю)',
  hybrid: 'Сначала домен, затем пароль системы',
}

onMounted(async () => {
  try { ldap.value = await api.get('/settings/ldap') } catch (err) { ui.error(err) }
  loadDays()
})

function change() { dirty.value = true; testResult.value = null }
async function saveLdap() {
  try {
    ldap.value = await api.put('/settings/ldap', ldap.value)
    dirty.value = false
    ui.success('Настройки входа сохранены')
  } catch (err) { ui.error(err) }
}
async function testLdap() {
  testing.value = true
  try { testResult.value = await api.post('/settings/ldap/test', ldap.value) } catch (err) { testResult.value = { ok: false, error: err.message } } finally { testing.value = false }
}

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
  <div class="page narrow">
    <div class="page-header">
      <h1 class="page-title">Настройки</h1>
      <HelpLink :topic="tab === 'calendar' ? 'admin-calendar' : 'admin-ldap'" />
      <div class="spacer" />
      <div class="segmented">
        <button type="button" :class="{ 'is-active': tab === 'ldap' }" @click="tab = 'ldap'">Вход через домен</button>
        <button type="button" :class="{ 'is-active': tab === 'calendar' }" @click="tab = 'calendar'">Производственный календарь</button>
      </div>
    </div>

    <div v-if="tab === 'ldap' && ldap" class="card block">
      <label class="switch"><input v-model="ldap.enabled" type="checkbox" @change="change" /><span class="switch-track" /><b>Вход по учётной записи домена (LDAP / Active Directory)</b></label>
      <template v-if="ldap.enabled">
        <label class="field">
          <span class="field-label">Режим</span>
          <select v-model="ldap.authMode" class="select" @change="change">
            <option v-for="(label, key) in MODES" :key="key" :value="key">{{ label }}</option>
          </select>
        </label>
        <div class="form-grid">
          <label class="field"><span class="field-label">Адрес сервера</span><input v-model="ldap.serverUrl" class="input" placeholder="ldaps://dc.example.ru:636" @input="change" /></label>
          <label class="field"><span class="field-label">Base DN</span><input v-model="ldap.baseDN" class="input" placeholder="dc=example,dc=ru" @input="change" /></label>
          <label class="field"><span class="field-label">Сервисная учётная запись (Bind DN)</span><input v-model="ldap.bindDN" class="input" placeholder="cn=svc-puls,ou=Service,dc=example,dc=ru" @input="change" /></label>
          <label class="field"><span class="field-label">Пароль сервисной учётной записи</span><input v-model="ldap.bindPassword" class="input" type="password" autocomplete="new-password" @input="change" /><span class="field-hint">Хранится на сервере и не показывается</span></label>
        </div>
        <label class="field"><span class="field-label">Фильтр поиска</span><input v-model="ldap.searchFilter" class="input mono" @input="change" /><span class="field-hint">{{ USERNAME }} заменяется на логин. Для Active Directory: (sAMAccountName={{ USERNAME }})</span></label>
        <div class="form-grid three">
          <label class="field"><span class="field-label">Атрибут логина</span><input v-model="ldap.userAttributes.username" class="input mono" @input="change" /></label>
          <label class="field"><span class="field-label">Атрибут ФИО</span><input v-model="ldap.userAttributes.name" class="input mono" @input="change" /></label>
          <label class="field"><span class="field-label">Атрибут email</span><input v-model="ldap.userAttributes.email" class="input mono" @input="change" /></label>
        </div>
        <div class="form-grid">
          <label class="field">
            <span class="field-label">Роль новых сотрудников домена</span>
            <select v-model="ldap.defaultRole" class="select" @change="change"><option value="viewer">Наблюдатель</option><option value="editor">Редактор</option><option value="manager">Менеджер</option></select>
            <span class="field-hint">Сотрудник создаётся при первом входе</span>
          </label>
          <label class="field"><span class="field-label">Таймаут, с</span><input v-model.number="ldap.timeoutSeconds" class="input" type="number" min="1" max="60" @input="change" /></label>
        </div>
        <div class="test">
          <button class="btn" type="button" :disabled="testing || !ldap.serverUrl" @click="testLdap"><AppIcon name="link" :size="14" /> Проверить подключение</button>
          <span v-if="testResult" :class="testResult.ok ? 'ok' : 'bad'">{{ testResult.ok ? 'Подключение успешно' : testResult.error }}</span>
        </div>
      </template>
      <div class="footer">
        <button class="btn btn-primary" type="button" :disabled="!dirty" @click="saveLdap">Сохранить</button>
        <span v-if="dirty" class="muted small">Есть несохранённые изменения</span>
      </div>
    </div>

    <div v-if="tab === 'calendar'" class="card block">
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
.narrow { max-width: 900px; }
.block { padding: 16px 18px; display: flex; flex-direction: column; gap: 14px; }
.three { grid-template-columns: 1fr 1fr 1fr; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }
.test { display: flex; align-items: center; gap: 12px; }
.ok { color: var(--color-success); font-weight: 600; }
.bad { color: var(--color-danger); }
.footer { display: flex; align-items: center; gap: 10px; border-top: 1px solid var(--color-border); padding-top: 12px; }
.year { display: flex; align-items: center; gap: 8px; }
.days { display: grid; grid-template-columns: 1.4fr 1fr; gap: 20px; }
.day { display: flex; align-items: center; gap: 10px; padding: 4px 0; font-size: 13px; border-bottom: 1px solid #f0f2f6; }
.date { font-variant-numeric: tabular-nums; width: 86px; flex-shrink: 0; }
.day .ellipsis { flex: 1; }
@media (max-width: 768px) { .days, .three { grid-template-columns: 1fr; } }
</style>
