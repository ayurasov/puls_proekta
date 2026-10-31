<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { api } from '../../../api/client'
import { useUiStore } from '../../../stores/ui'
import AppIcon from '../../../components/common/AppIcon.vue'
import IntegrationHeader from '../../../components/integrations/IntegrationHeader.vue'

/** «Интеграции → Вход через домен»: LDAP / Active Directory (ADR-0003); тот же каталог — для синхронизации структуры с AD. */
const ui = useUiStore()
const ldap = ref(null)
const dirty = ref(false)
const testResult = ref(null)
const testing = ref(false)

const USERNAME = '{' + '{username}' + '}'
const MODES = {
  local: 'Только пароли системы',
  ldap: 'Только учётная запись домена (локальные администраторы сохраняют вход по паролю)',
  hybrid: 'Сначала домен, затем пароль системы',
}

onMounted(async () => {
  try { ldap.value = await api.get('/settings/ldap') } catch (err) { ui.error(err) }
})

function change() { dirty.value = true; testResult.value = null }
// Включили вход через домен — режим «Только пароли системы» не имеет смысла: по умолчанию «сначала домен»
function toggleEnabled() {
  if (ldap.value.enabled && ldap.value.authMode === 'local') ldap.value.authMode = 'hybrid'
  change()
}

// Несохранённые настройки входа не пропадают молча: при уходе со страницы — вопрос, при закрытии вкладки — предупреждение браузера
onBeforeRouteLeave(async () => {
  if (!dirty.value) return true
  const choice = await ui.openDialog({
    type: 'choice', title: 'Сохранить настройки входа?',
    message: 'Изменения настроек входа через домен ещё не сохранены.',
    cancelText: 'Остаться', choices: [{ value: 'discard', label: 'Не сохранять', danger: true }, { value: 'save', label: 'Сохранить' }],
  })
  if (choice === 'save') { await saveLdap(); return !dirty.value }
  return choice === 'discard'
})
function onBeforeUnload(event) {
  if (!dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}
onMounted(() => window.addEventListener('beforeunload', onBeforeUnload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', onBeforeUnload))
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
const stateBadge = computed(() => {
  if (!ldap.value) return null
  if (dirty.value) return { kind: 'warn', text: 'Есть несохранённые изменения' }
  return ldap.value.enabled ? { kind: 'ok', text: 'Включён' } : { kind: 'muted', text: 'Выключен' }
})
</script>

<template>
  <div class="page narrow">
    <IntegrationHeader title="Вход через домен" subtitle="LDAP / Active Directory: сотрудники входят с паролем домена; тот же каталог — для синхронизации структуры" logo="ldap" :badge="stateBadge" help="admin-ldap" />
    <div v-if="ldap" class="card block" data-testid="ldap-settings">
      <label class="switch"><input v-model="ldap.enabled" type="checkbox" @change="toggleEnabled" /><span class="switch-track" /><b>Вход по учётной записи домена (LDAP / Active Directory)</b></label>
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
      <div class="footer" :class="{ dirty }">
        <button class="btn btn-primary" type="button" :disabled="!dirty" data-testid="ldap-save" @click="saveLdap">Сохранить</button>
        <span v-if="dirty" class="unsaved small"><AppIcon name="warning" :size="14" /> Есть несохранённые изменения — сохраните их, прежде чем уходить со страницы</span>
      </div>
    </div>

    <p class="muted small more">Синхронизация структуры и сотрудников с Active Directory — в разделе
      <router-link to="/admin/org-transfer">«Импорт и экспорт»</router-link>; она использует этот же каталог и сервисную учётную запись.</p>
  </div>
</template>

<style scoped>
.narrow { max-width: 920px; }
.block { padding: 18px 20px; display: flex; flex-direction: column; gap: 14px; }
.three { grid-template-columns: 1fr 1fr 1fr; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }
.test { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.ok { color: var(--color-success); font-weight: 600; }
.bad { color: var(--color-danger); }
.footer { display: flex; align-items: center; gap: 10px; border-top: 1px solid var(--color-border); padding-top: 12px; position: sticky; bottom: 0; background: var(--color-surface); padding-bottom: 4px; }
.unsaved { display: inline-flex; align-items: center; gap: 6px; color: #a8670f; font-weight: 600; }
.more { margin-top: 12px; }
@media (max-width: 768px) { .three { grid-template-columns: 1fr; } }
</style>
