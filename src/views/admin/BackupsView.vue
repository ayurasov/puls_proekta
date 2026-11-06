<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../../api/client'
import AppIcon from '../../components/common/AppIcon.vue'
import HelpLink from '../../components/common/HelpLink.vue'
import ToggleSwitch from '../../components/common/ToggleSwitch.vue'
import { useSystemHealthStore } from '../../stores/systemHealth'
import { useUiStore } from '../../stores/ui'
import { countLabel, formatBytes, formatDateTime, formatRelative } from '../../utils/format'

/**
 * «Резервные копии» (ADR-0015): расписание и хранение копий, копия по запросу, список копий. Копии делает контейнер
 * backup — он берёт настройки из базы раз в минуту и сообщает о себе; приложение к файлам копий доступа не имеет.
 * Видят администратор и сотрудники с «Смотрит состояние системы» или «Ведёт резервное копирование», меняют —
 * администратор и «Ведёт резервное копирование».
 */
const ui = useUiStore()
const health = useSystemHealthStore()
const data = ref(null)
const loadError = ref(null)
const form = ref(null)
const saving = ref(false)
const requesting = ref(false)
let timer = null

const DAYS = [
  { value: 1, label: 'Пн' }, { value: 2, label: 'Вт' }, { value: 3, label: 'Ср' }, { value: 4, label: 'Чт' },
  { value: 5, label: 'Пт' }, { value: 6, label: 'Сб' }, { value: 7, label: 'Вс' },
]

const canManage = computed(() => Boolean(data.value?.canManage))
const service = computed(() => data.value?.service || {})
const effective = computed(() => service.value.effective || null)
const pending = computed(() => Boolean(data.value?.request || data.value?.running))
const dirty = computed(() => form.value && data.value && JSON.stringify(normalize(form.value)) !== JSON.stringify(normalize(data.value.settings)))
const serviceState = computed(() => {
  if (service.value.alive) return 'ok'
  return service.value.seenAt ? 'error' : 'off'
})
const tzText = computed(() => {
  const e = effective.value
  if (!e?.tz) return ''
  const o = e.utcOffset || ''
  return `${e.tz}${/^[+-]\d{4}$/.test(o) ? ` (UTC${o[0]}${Number(o.slice(1, 3))}${o.slice(3) !== '00' ? `:${o.slice(3)}` : ''})` : ''}`
})

function normalize(s) {
  return { enabled: Boolean(s.enabled), time: s.time, days: [...(s.days || [])].sort(), retentionDays: Number(s.retentionDays), keepManualDays: Number(s.keepManualDays || 0) }
}

async function load({ keepForm = false } = {}) {
  try {
    data.value = await api.get('/system/backups')
    loadError.value = null
    if (!keepForm || !form.value) form.value = normalize(data.value.settings)
  } catch (err) {
    loadError.value = err
  }
  schedule()
}

// Пока копия запрошена или делается — проверяем каждые 5 секунд, иначе раз в минуту
function schedule() {
  clearTimeout(timer)
  timer = setTimeout(() => { if (document.visibilityState === 'visible') load({ keepForm: true }); else schedule() }, pending.value ? 5000 : 60_000)
}
watch(pending, (now, before) => {
  if (before && !now && data.value?.last) {
    if (data.value.last.ok) ui.success('Копия готова', data.value.last.file || '')
    else ui.error('Копия не удалась', data.value.last.error || '')
    health.load()
  }
})

function toggleDay(value) {
  const days = form.value.days.includes(value) ? form.value.days.filter((d) => d !== value) : [...form.value.days, value]
  form.value.days = days.sort()
}

async function save() {
  saving.value = true
  try {
    data.value = await api.put('/system/backups/settings', normalize(form.value))
    form.value = normalize(data.value.settings)
    ui.success('Расписание сохранено', 'Служба копий применит его в течение минуты')
    health.load()
  } catch (err) {
    ui.error(err)
  } finally {
    saving.value = false
  }
}

async function runNow() {
  requesting.value = true
  try {
    data.value = await api.post('/system/backups/run')
    ui.toast({ title: 'Копия запрошена', body: service.value.alive ? 'Служба копий сделает её в течение минуты' : 'Служба копий сейчас не отвечает — копия будет сделана, когда она запустится', kind: service.value.alive ? 'success' : 'info' })
    schedule()
  } catch (err) {
    ui.error(err)
  } finally {
    requesting.value = false
  }
}

onMounted(() => load())
onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Резервные копии <HelpLink topic="admin-backups" /></h1>
        <p class="muted small sub">Копии базы делает служба резервного копирования (контейнер backup) по расписанию ниже и по запросу.
          В копии — все проекты, планы, история, сотрудники и настройки.</p>
      </div>
      <button v-if="canManage" class="btn btn-primary" type="button" :disabled="requesting || pending" data-testid="backup-run" @click="runNow">
        <AppIcon name="download" :size="14" /> {{ data?.running ? 'Копия делается…' : data?.request ? 'Копия запрошена…' : 'Сделать копию сейчас' }}
      </button>
    </div>

    <div v-if="!data && loadError" class="card empty">Не удалось загрузить: {{ loadError.message }}</div>
    <div v-else-if="!data" class="card empty muted">Загрузка…</div>

    <template v-else>
      <section class="card service" :class="`is-${serviceState}`" data-testid="backup-service">
        <span class="service-icon"><AppIcon :name="serviceState === 'ok' ? 'check' : 'warning'" :size="18" :stroke-width="2.4" /></span>
        <div class="service-text">
          <strong v-if="serviceState === 'ok'">Служба резервного копирования работает</strong>
          <strong v-else-if="serviceState === 'error'">Служба резервного копирования не отвечает</strong>
          <strong v-else>Служба резервного копирования ещё не подключалась</strong>
          <span v-if="serviceState === 'ok'" class="muted small">Отметилась {{ formatRelative(service.seenAt) }} · копии {{ service.scheduleText }}<template v-if="tzText"> ({{ tzText }})</template></span>
          <span v-else-if="serviceState === 'error'" class="muted small">Последний раз — {{ formatDateTime(service.seenAt) }}. Попросите инженера сопровождения проверить
            контейнер: <code>docker compose ps backup</code>, журнал — <code>docker compose logs backup</code>.</span>
          <span v-else class="muted small">В установке через Docker Compose копии делает контейнер backup — запустите его:
            <code>docker compose up -d backup</code>. Через минуту здесь появятся расписание и копии.</span>
        </div>
      </section>

      <div class="stats">
        <div class="card stat" data-testid="backup-last">
          <span class="stat-label">Последняя копия</span>
          <template v-if="data.last">
            <strong :class="{ bad: data.last.ok === false }">{{ data.last.ok === false ? 'Не удалась' : formatRelative(data.last.lastOkAt) }}</strong>
            <span class="muted small">{{ data.last.ok === false ? `${formatDateTime(data.last.at)} · ${data.last.error}` : `${formatDateTime(data.last.lastOkAt)} · ${formatBytes(data.last.size)}` }}</span>
          </template>
          <template v-else><strong class="muted">—</strong><span class="muted small">Копий ещё не было</span></template>
        </div>
        <div class="card stat" data-testid="backup-next">
          <span class="stat-label">Следующая по расписанию</span>
          <strong>{{ data.nextLocal || (effective && !effective.enabled ? 'Выключено' : '—') }}</strong>
          <span class="muted small">{{ effective ? `Копии ${service.scheduleText}` : 'Расписание появится, когда служба подключится' }}</span>
        </div>
        <div class="card stat">
          <span class="stat-label">Хранится</span>
          <strong>{{ data.count != null ? countLabel(data.count, 'копия', 'копии', 'копий') : '—' }}</strong>
          <span class="muted small">{{ data.totalBytes != null ? `всего ${formatBytes(data.totalBytes)}` : '' }}</span>
        </div>
      </div>

      <p v-if="data.running" class="progress-note" data-testid="backup-running"><span class="spinner" /> Делается копия — начата {{ formatRelative(data.running.startedAt) }}.</p>
      <p v-else-if="data.request" class="progress-note" data-testid="backup-requested"><span class="spinner" /> Копия запрошена ({{ data.request.by }}, {{ formatRelative(data.request.requestedAt) }}) —
        служба сделает её в течение минуты.</p>

      <div class="columns">
        <section class="card block" aria-labelledby="bk-schedule">
          <h2 id="bk-schedule" class="block-title">Расписание и хранение</h2>
          <p v-if="!data.saved" class="muted small note" data-testid="backup-env-note">
            Сейчас действует расписание из настроек сервера (<code>.env</code>){{ effective?.source === 'env' ? `: копии ${service.scheduleText}, хранятся ${effective.retentionDays} дн` : '' }}.
            <template v-if="canManage">Сохраните расписание здесь — служба будет брать его из «Пульса».</template>
          </p>
          <form v-if="form" class="form-stack" @submit.prevent="save">
            <ToggleSwitch v-model="form.enabled" label="Делать копии по расписанию" hint="Выключите, только если копии делаются другим способом" :disabled="!canManage" />
            <div class="form-grid">
              <label class="field">
                <span class="field-label">Время копии</span>
                <input v-model="form.time" class="input" type="time" required :disabled="!canManage || !form.enabled" data-testid="backup-time" />
                <span class="field-hint">{{ tzText ? `По часовому поясу службы: ${tzText}` : 'По часовому поясу сервера' }}</span>
              </label>
              <div class="field">
                <span class="field-label">Дни недели</span>
                <div class="days" role="group" aria-label="Дни недели">
                  <button
                    v-for="d in DAYS" :key="d.value" type="button" class="day" :class="{ 'is-on': form.days.includes(d.value) }"
                    :aria-pressed="form.days.includes(d.value)" :disabled="!canManage || !form.enabled" :data-day="d.value" @click="toggleDay(d.value)"
                  >{{ d.label }}</button>
                </div>
              </div>
            </div>
            <div class="form-grid">
              <label class="field">
                <span class="field-label">Хранить копии по расписанию, дней</span>
                <input v-model.number="form.retentionDays" class="input" type="number" min="1" max="365" required :disabled="!canManage" data-testid="backup-retention" />
                <span class="field-hint">Старые удаляются после очередной копии</span>
              </label>
              <label class="field">
                <span class="field-label">Хранить остальные копии, дней</span>
                <input v-model.number="form.keepManualDays" class="input" type="number" min="0" max="3650" :disabled="!canManage" />
                <span class="field-hint">Сделанные вручную и перед обновлением; 0 — хранить всегда</span>
              </label>
            </div>
            <div v-if="canManage" class="form-actions">
              <button class="btn btn-primary" type="submit" :disabled="saving || !dirty || (form.enabled && !form.days.length)" data-testid="backup-save">Сохранить</button>
              <span v-if="form.enabled && !form.days.length" class="field-error">Отметьте хотя бы один день</span>
              <span v-else class="muted small">Служба применит изменения в течение минуты</span>
            </div>
          </form>
        </section>

        <section class="card block" aria-labelledby="bk-files">
          <h2 id="bk-files" class="block-title">Копии <span v-if="data.count" class="pill">{{ data.count }}</span></h2>
          <p v-if="!data.files.length" class="muted small">Копий пока нет.</p>
          <table v-else class="table files" data-testid="backup-files">
            <thead><tr><th>Когда</th><th>Как сделана</th><th class="num">Размер</th></tr></thead>
            <tbody>
              <tr v-for="f in data.files" :key="f.name" :title="f.name">
                <td>{{ formatDateTime(f.at) }}</td>
                <td><span class="kind" :class="`kind-${f.kind === 'По расписанию' ? 'daily' : 'other'}`">{{ f.kind }}</span></td>
                <td class="num">{{ formatBytes(f.size) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="data.count > data.files.length" class="muted small">Показаны последние {{ data.files.length }}.</p>
          <details class="restore">
            <summary>Как восстановить базу из копии</summary>
            <p class="small">Восстанавливает инженер сопровождения на сервере — перед восстановлением служба сама сделает копию текущей базы:</p>
            <pre class="mono small">docker compose stop backend
docker compose exec backup sh /usr/local/bin/backup.sh list
docker compose exec backup sh /usr/local/bin/backup.sh restore &lt;файл&gt;
docker compose start backend</pre>
            <p class="small muted">Копии хранятся на том же сервере — копируйте каталог копий на другой сервер (руководство по развёртыванию).</p>
          </details>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.sub { margin: 4px 0 0; max-width: 760px; line-height: 1.5; }
.empty { padding: 28px; text-align: center; }
.service { display: flex; align-items: center; gap: 12px; padding: 12px 16px; margin-bottom: 14px; border-left: 4px solid var(--c); }
.service.is-ok { --c: var(--color-success); --soft: var(--color-success-soft); --text: #1d7a4c; }
.service.is-error { --c: var(--color-danger); --soft: var(--color-danger-soft); --text: #b42318; }
.service.is-off { --c: #e8a317; --soft: var(--color-warning-soft); --text: #8a5a00; }
.service-icon { width: 34px; height: 34px; border-radius: 10px; flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center; background: var(--soft); color: var(--text); }
.service-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.service-text strong { color: var(--text); font-size: 14px; }
.service code, .note code { font-size: 12px; padding: 1px 5px; border-radius: 5px; background: #eef1f7; }
.stats { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(240px, 100%), 1fr)); gap: 12px; margin-bottom: 14px; }
.stat { display: flex; flex-direction: column; gap: 4px; padding: 12px 16px; min-width: 0; }
.stat-label { font-size: 11.5px; font-weight: 600; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: 0.04em; }
.stat strong { font-size: 18px; font-weight: 700; }
.stat strong.bad { color: #b42318; }
.stat .small { overflow-wrap: anywhere; }
.progress-note { display: flex; align-items: center; gap: 8px; margin: 0 0 14px; padding: 9px 12px; border-radius: 10px; background: var(--color-primary-soft); color: var(--color-primary-dark); font-size: 13px; }
.spinner { width: 14px; height: 14px; border-radius: 50%; border: 2px solid currentColor; border-right-color: transparent; animation: spin 0.8s linear infinite; flex-shrink: 0; }
@keyframes spin { to { transform: rotate(360deg); } }
.columns { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr); gap: 14px; align-items: start; }
.block { padding: 16px; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.block-title { margin: 0; font-size: 15px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
.pill { font-size: 11.5px; font-weight: 600; padding: 1px 8px; border-radius: 999px; background: #eef1f7; color: var(--color-text-secondary); }
.note { margin: 0; line-height: 1.5; padding: 9px 12px; border-radius: 10px; background: var(--color-warning-soft); color: #8a5a10; }
.days { display: flex; flex-wrap: wrap; gap: 6px; }
.day { min-width: 40px; height: 34px; padding: 0 10px; border-radius: 9px; border: 1px solid var(--color-border); background: var(--color-surface); color: var(--color-text-secondary); font-size: 13px; font-weight: 600; transition: background 0.15s, border-color 0.15s, color 0.15s; }
.day.is-on { background: var(--color-primary-soft); border-color: #c9d6ff; color: var(--color-primary-dark); }
.day:disabled { cursor: not-allowed; opacity: 0.6; }
.day:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }
.form-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.files td, .files th { font-size: 12.5px; }
.num { text-align: right; white-space: nowrap; }
.kind { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11.5px; font-weight: 600; background: #eef1f7; color: var(--color-text-secondary); }
.kind-daily { background: var(--color-success-soft); color: #1d7a4c; }
.restore { border-top: 1px solid var(--color-border); padding-top: 10px; }
.restore summary { cursor: pointer; font-size: 13px; font-weight: 600; color: var(--color-primary); }
.restore pre { margin: 8px 0; padding: 10px 12px; border-radius: 9px; background: #0f172a; color: #e2e8f0; overflow-x: auto; line-height: 1.6; }
@media (max-width: 960px) { .columns { grid-template-columns: minmax(0, 1fr); } }
</style>
