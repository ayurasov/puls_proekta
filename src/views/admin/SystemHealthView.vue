<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import AppIcon from '../../components/common/AppIcon.vue'
import HelpLink from '../../components/common/HelpLink.vue'
import IntegrationLogo from '../../components/integrations/IntegrationLogo.vue'
import { useAuthStore } from '../../stores/auth'
import { useSystemHealthStore } from '../../stores/systemHealth'
import { useUiStore } from '../../stores/ui'
import { formatDateTime } from '../../utils/format'

/**
 * «Состояние системы» (ADR-0014): всё, от чего зависит работа «Пульса», — карточками «в порядке», «требует
 * внимания», «неполадка», «не настроено». Администратору — всегда, остальным — с полномочием «Смотрит состояние
 * системы». Сводка обновляется раз в минуту, пока страница открыта.
 */
const auth = useAuthStore()
const health = useSystemHealthStore()
const ui = useUiStore()
const checking = ref(false)
const lastChecks = ref(null)
let timer = null

const STATUS = {
  ok: { label: 'В порядке', icon: 'check' },
  warn: { label: 'Требует внимания', icon: 'warning' },
  error: { label: 'Неполадка', icon: 'warning' },
  off: { label: 'Не настроено', icon: 'minus' },
}
const HEADLINES = { ok: 'Всё работает', warn: 'Есть замечания', error: 'Есть неполадки' }
const ICONS = { app: 'pulse', backups: 'archive', errors: 'warning', calendar: 'calendar', users: 'users', data: 'folder' }
const LOGOS = { jira: 'jira', podelu: 'podelu', ldap: 'ldap', keys: 'keys' }

const data = computed(() => health.data)
const items = computed(() => data.value?.items || [])
const counts = computed(() => {
  const out = { ok: 0, warn: 0, error: 0, off: 0 }
  for (const item of items.value) out[item.status] = (out[item.status] || 0) + 1
  return out
})
// Сначала неполадки и замечания — их видно без прокрутки; порядок внутри — как на сервере
const sorted = computed(() => {
  const rank = { error: 0, warn: 1, ok: 2, off: 3 }
  return items.value.map((item, i) => ({ item, i })).sort((a, b) => rank[a.item.status] - rank[b.item.status] || a.i - b.i)
    .map(({ item }) => item)
})
const appItem = computed(() => items.value.find((i) => i.key === 'app'))

function canOpen(item) {
  return item.link && item.capability && auth.can(item.capability)
}

async function refresh() {
  await health.load()
}

async function check() {
  checking.value = true
  try {
    const result = await health.check()
    lastChecks.value = result.checks || []
    if (!lastChecks.value.length) ui.toast({ title: 'Проверять нечего', body: 'Jira, «По Делу» и вход через домен не подключены', kind: 'info' })
    else {
      const failed = lastChecks.value.filter((c) => !c.ok)
      if (failed.length) ui.error('Есть неполадки подключений', failed.map((c) => `${c.title}: ${c.message}`).join('\n'))
      else ui.success('Подключения работают', lastChecks.value.map((c) => c.title).join(', '))
    }
  } catch (err) {
    ui.error(err)
  } finally {
    checking.value = false
  }
}

onMounted(() => {
  refresh()
  timer = setInterval(() => { if (document.visibilityState === 'visible') health.load() }, 60_000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Состояние системы <HelpLink topic="admin-health" /></h1>
        <p class="muted small sub">Работает ли всё, от чего зависят планы: сервер и база данных, резервные копии, ошибки,
          подключения к Jira, «По Делу» и каталогу, производственный календарь.</p>
      </div>
      <div class="header-actions">
        <button class="btn" type="button" :disabled="checking" data-testid="health-check" @click="check">
          <AppIcon name="link" :size="14" /> {{ checking ? 'Проверяем…' : 'Проверить подключения' }}
        </button>
        <button class="btn btn-ghost" type="button" :disabled="health.loading" title="Обновить" data-testid="health-refresh" @click="refresh">
          <AppIcon name="refresh" :size="14" /> Обновить
        </button>
      </div>
    </div>

    <div v-if="!data && health.loading" class="card empty muted">Проверяем…</div>
    <div v-else-if="!data && health.error" class="card empty">Не удалось проверить состояние: {{ health.error.message }}</div>

    <template v-else-if="data">
      <section class="card summary" :class="`is-${data.status}`" data-testid="health-summary" aria-live="polite">
        <span class="summary-icon"><AppIcon :name="data.status === 'ok' ? 'check' : 'warning'" :size="20" :stroke-width="2.4" /></span>
        <div class="summary-text">
          <h2 class="summary-title">{{ HEADLINES[data.status] }}</h2>
          <p class="muted small">Проверено {{ formatDateTime(data.checkedAt) }} · {{ appItem?.summary }}</p>
        </div>
        <div class="summary-counts">
          <span v-if="counts.error" class="count is-error">Неполадки: {{ counts.error }}</span>
          <span v-if="counts.warn" class="count is-warn">Замечания: {{ counts.warn }}</span>
          <span class="count is-ok">В порядке: {{ counts.ok }}</span>
          <span v-if="counts.off" class="count is-off">Не настроено: {{ counts.off }}</span>
        </div>
      </section>

      <ul v-if="lastChecks?.length" class="checks" data-testid="health-checks">
        <li v-for="c in lastChecks" :key="c.key" :class="c.ok ? 'is-ok' : 'is-error'">
          <AppIcon :name="c.ok ? 'check' : 'warning'" :size="13" :stroke-width="2.4" />
          <strong>{{ c.title }}</strong><span class="muted">{{ c.ok ? 'подключение работает' : c.message }}</span>
        </li>
      </ul>

      <div class="grid">
        <article v-for="item in sorted" :key="item.key" class="card component" :class="`is-${item.status}`" :data-testid="`health-${item.key}`">
          <header class="component-head">
            <IntegrationLogo v-if="LOGOS[item.key]" :kind="LOGOS[item.key]" :size="32" />
            <span v-else class="component-icon"><AppIcon :name="ICONS[item.key] || 'info'" :size="16" /></span>
            <h3 class="component-title">{{ item.title }}</h3>
            <span class="badge-status" :class="`is-${item.status}`">
              <AppIcon :name="STATUS[item.status].icon" :size="12" :stroke-width="2.6" /> {{ STATUS[item.status].label }}
            </span>
          </header>
          <p class="component-summary">{{ item.summary }}</p>
          <ul v-if="item.details.length" class="details">
            <li v-for="(d, i) in item.details" :key="i">{{ d }}</li>
          </ul>
          <router-link v-if="canOpen(item)" :to="item.link" class="go">
            {{ item.status === 'off' ? 'Настроить' : 'Открыть' }} <AppIcon name="arrowRight" :size="13" />
          </router-link>
        </article>
      </div>
      <p class="muted small foot">Сводка обновляется раз в минуту, пока страница открыта. «Проверить подключения» обращается к
        Jira, «По Делу» и каталогу сохранёнными настройками и показывает итог сразу.</p>
    </template>
  </div>
</template>

<style scoped>
.sub { margin: 4px 0 0; max-width: 760px; line-height: 1.5; }
.header-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.empty { padding: 28px; text-align: center; }

.summary { display: flex; align-items: center; gap: 14px; padding: 14px 18px; margin-bottom: 14px; border-left: 4px solid var(--c); flex-wrap: wrap; }
.summary.is-ok { --c: var(--color-success); --soft: var(--color-success-soft); --text: #1d7a4c; }
.summary.is-warn { --c: #e8a317; --soft: var(--color-warning-soft); --text: #8a5a00; }
.summary.is-error { --c: var(--color-danger); --soft: var(--color-danger-soft); --text: #b42318; }
.summary-icon { width: 38px; height: 38px; border-radius: 11px; display: inline-flex; align-items: center; justify-content: center; background: var(--soft); color: var(--text); flex-shrink: 0; }
.summary-text { flex: 1; min-width: 220px; }
.summary-title { margin: 0; font-size: 16px; font-weight: 700; color: var(--text); }
.summary-text p { margin: 2px 0 0; }
.summary-counts { display: flex; flex-wrap: wrap; gap: 6px; }
.count { font-size: 12px; font-weight: 600; padding: 3px 10px; border-radius: 999px; white-space: nowrap; }
.count.is-ok { background: var(--color-success-soft); color: #1d7a4c; }
.count.is-warn { background: var(--color-warning-soft); color: #8a5a00; }
.count.is-error { background: var(--color-danger-soft); color: #b42318; }
.count.is-off { background: #eef1f7; color: var(--color-text-secondary); }

.checks { list-style: none; margin: 0 0 14px; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.checks li { display: flex; align-items: baseline; gap: 8px; font-size: 13px; padding: 8px 12px; border-radius: 10px; background: var(--color-surface); border: 1px solid var(--color-border); flex-wrap: wrap; }
.checks li.is-ok :deep(svg) { color: #1d7a4c; }
.checks li.is-error { border-color: #f5c2c0; background: #fff8f7; }
.checks li.is-error :deep(svg) { color: #b42318; }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(300px, 100%), 1fr)); gap: 14px; }
.component { display: flex; flex-direction: column; gap: 8px; padding: 14px 16px; border-left: 4px solid var(--c, var(--color-border)); min-width: 0; }
.component.is-ok { --c: var(--color-success); }
.component.is-warn { --c: #e8a317; }
.component.is-error { --c: var(--color-danger); }
.component.is-off { --c: #d6dbe6; }
.component-head { display: flex; align-items: center; gap: 10px; }
.component-icon { width: 32px; height: 32px; border-radius: 9px; background: #eef1f7; color: #475569; display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0; }
.component-title { margin: 0; flex: 1; min-width: 0; font-size: 14px; font-weight: 700; }
.badge-status { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; font-weight: 600; padding: 3px 9px; border-radius: 999px; white-space: nowrap; }
.badge-status.is-ok { background: var(--color-success-soft); color: #1d7a4c; }
.badge-status.is-warn { background: var(--color-warning-soft); color: #8a5a00; }
.badge-status.is-error { background: var(--color-danger-soft); color: #b42318; }
.badge-status.is-off { background: #eef1f7; color: var(--color-text-secondary); }
.component-summary { margin: 0; font-size: 13px; line-height: 1.45; color: var(--color-text); overflow-wrap: anywhere; }
.details { margin: 0; padding-left: 16px; display: flex; flex-direction: column; gap: 3px; font-size: 12px; line-height: 1.45; color: var(--color-text-secondary); overflow-wrap: anywhere; }
.go { margin-top: auto; padding-top: 2px; display: inline-flex; align-items: center; gap: 5px; font-size: 12.5px; font-weight: 600; color: var(--color-primary); text-decoration: none; align-self: flex-start; }
.go:hover { text-decoration: underline; }
.foot { margin: 14px 0 0; max-width: 760px; line-height: 1.5; }

@media (max-width: 640px) {
  .header-actions { width: 100%; }
  .header-actions .btn { flex: 1; justify-content: center; }
}
</style>
