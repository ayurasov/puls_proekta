<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import AppIcon from '../../components/common/AppIcon.vue'
import HelpLink from '../../components/common/HelpLink.vue'
import IntegrationLogo from '../../components/integrations/IntegrationLogo.vue'
import { formatDateTime } from '../../utils/format'

/**
 * «Интеграции» — отдельный блок администрирования: Jira, «По Делу», вход через домен (LDAP / Active Directory).
 * Каждая карточка показывает состояние подключения и ведёт к его настройке.
 */
const jira = ref(undefined)
const podelu = ref(undefined)
const links = ref(undefined)
const ldap = ref(undefined)

onMounted(async () => {
  const [j, p, l, d] = await Promise.allSettled([
    api.get('/settings/jira'), api.get('/settings/podelu'), api.get('/settings/podelu/links'), api.get('/settings/ldap'),
  ])
  jira.value = j.status === 'fulfilled' ? j.value : null
  podelu.value = p.status === 'fulfilled' ? p.value : null
  links.value = l.status === 'fulfilled' ? l.value : null
  ldap.value = d.status === 'fulfilled' ? d.value : null
})

function host(url) {
  try { return new URL(url).host } catch { return url || '' }
}

const cards = computed(() => [
  {
    id: 'jira', to: '/admin/integrations/jira', logo: 'jira', title: 'Jira',
    text: 'Статусы, сроки и прогресс задач Jira в планах; задачи, релизы и спринты — строками плана; автоперенос и сверка.',
    state: jira.value === undefined ? null : jira.value
      ? { kind: jira.value.hasToken ? 'ok' : 'warn', text: jira.value.hasToken ? 'Подключена' : 'Нет пароля или токена' }
      : { kind: 'muted', text: 'Не подключена' },
    facts: jira.value ? [host(jira.value.baseUrl), { cloud: 'Jira Cloud', server: 'Server/DC · PAT', server_basic: 'Server/DC · логин и пароль' }[jira.value.instanceType]] : [],
  },
  {
    id: 'podelu', to: '/admin/integrations/podelu', logo: 'podelu', title: '«По Делу»',
    text: 'Задачи «По Делу» ссылаются на проекты и строки планов «Пульса»; структура компании и сотрудники — из «По Делу».',
    state: links.value === undefined ? null
      : links.value?.enabled ? { kind: 'ok', text: 'Связь в обе стороны' }
        : (links.value?.inbound || links.value?.hasKey || podelu.value) ? { kind: 'warn', text: 'Настроена частично' }
          : { kind: 'muted', text: 'Не подключена' },
    facts: [
      links.value?.baseUrl || podelu.value?.baseUrl ? host(links.value?.baseUrl || podelu.value?.baseUrl) : null,
      links.value?.inbound ? `ключ «Пульса» ${links.value.inbound.prefix}…` : null,
      links.value?.checkedAt ? `проверено ${formatDateTime(links.value.checkedAt)}` : null,
      podelu.value ? `структура: ${podelu.value.login}` : null,
    ].filter(Boolean),
  },
  {
    id: 'ldap', to: '/admin/integrations/ldap', logo: 'ldap', title: 'Вход через домен',
    text: 'LDAP / Active Directory: сотрудники входят с паролем домена; тот же каталог — для синхронизации структуры.',
    state: ldap.value === undefined ? null : ldap.value?.enabled
      ? { kind: 'ok', text: 'Включён' } : { kind: 'muted', text: 'Выключен' },
    facts: ldap.value?.enabled ? [ldap.value.serverUrl ? host(ldap.value.serverUrl.replace(/^ldaps?:/, 'https:')) : null,
      { local: 'только пароли системы', ldap: 'только домен', hybrid: 'сначала домен, затем пароль' }[ldap.value.authMode]].filter(Boolean) : [],
  },
])
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Интеграции <HelpLink topic="admin-integrations" /></h1>
        <p class="muted small sub">Связь «Пульса» с другими системами компании. Каждое подключение одно на систему, его настраивает администратор;
          пароли и ключи хранятся на сервере зашифрованными.</p>
      </div>
    </div>
    <div class="grid">
      <router-link v-for="card in cards" :key="card.id" :to="card.to" class="card integration" :data-testid="`integration-${card.id}`">
        <div class="top">
          <IntegrationLogo :kind="card.logo" :size="44" />
          <span v-if="card.state" class="state" :class="`state-${card.state.kind}`"><span class="dot" />{{ card.state.text }}</span>
          <span v-else class="state state-muted">…</span>
        </div>
        <h2 class="title">{{ card.title }}</h2>
        <p class="text">{{ card.text }}</p>
        <div v-if="card.facts.length" class="facts">
          <span v-for="fact in card.facts" :key="fact" class="fact">{{ fact }}</span>
        </div>
        <span class="go">Настроить <AppIcon name="arrowRight" :size="14" /></span>
      </router-link>
    </div>
    <div class="card note">
      <AppIcon name="upload" :size="16" class="muted" />
      <span>Загрузка структуры и сотрудников из файлов, из «По Делу» и синхронизация с Active Directory — в разделе
        <router-link to="/admin/org-transfer">«Импорт и экспорт»</router-link>.</span>
    </div>
  </div>
</template>

<style scoped>
.sub { margin: 4px 0 0; max-width: 760px; line-height: 1.5; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr)); gap: 16px; }
.integration { display: flex; flex-direction: column; gap: 10px; padding: 18px; text-decoration: none; color: inherit; transition: box-shadow 0.15s, transform 0.15s, border-color 0.15s; }
.integration:hover { border-color: #c9d6ff; box-shadow: 0 10px 28px rgba(15, 23, 42, 0.1); transform: translateY(-1px); }
.integration:focus-visible { outline: none; box-shadow: var(--focus-ring); }
.top { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.title { margin: 4px 0 0; font-size: 16px; font-weight: 700; }
.text { margin: 0; font-size: 13px; line-height: 1.5; color: var(--color-text-secondary); flex: 1; }
.facts { display: flex; flex-wrap: wrap; gap: 6px; }
.fact { font-size: 11.5px; padding: 2px 8px; border-radius: 6px; background: #f1f3f8; color: var(--color-text-secondary); max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.go { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 600; color: var(--color-primary); margin-top: 4px; }
.state { display: inline-flex; align-items: center; gap: 7px; padding: 4px 11px; border-radius: 999px; font-size: 12px; font-weight: 600; white-space: nowrap; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.state-ok { background: var(--color-success-soft); color: #1d7a4c; }
.state-warn { background: var(--color-warning-soft); color: #8a5a00; }
.state-error { background: var(--color-danger-soft); color: #b42318; }
.state-muted { background: #eef1f7; color: var(--color-text-secondary); }
.note { display: flex; align-items: center; gap: 10px; padding: 12px 16px; margin-top: 16px; font-size: 13px; }
</style>
