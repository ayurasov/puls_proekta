<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../../api/client'
import IntegrationHeader from '../../../components/integrations/IntegrationHeader.vue'
import PodeluLinksSettings from '../../../components/integrations/PodeluLinksSettings.vue'
import PodeluSettings from '../../../components/integrations/PodeluSettings.vue'

/**
 * «Интеграции → «По Делу»»: ссылки из задач «По Делу» на проекты и строки планов (обмен ключами, ADR-0010) и
 * загрузка структуры и сотрудников (сервисная учётная запись, ADR-0009). Части настраиваются независимо.
 */
const links = ref(null)
const structure = ref(null)
async function load() {
  try {
    [links.value, structure.value] = await Promise.all([api.get('/settings/podelu/links'), api.get('/settings/podelu')])
  } catch { /* ошибки покажут сами разделы */ }
}
onMounted(load)
const badge = computed(() => {
  if (!links.value) return null
  if (links.value.enabled) return { kind: 'ok', text: 'Связь работает в обе стороны' }
  if (links.value.inbound || links.value.hasKey || structure.value) return { kind: 'warn', text: 'Настроена частично' }
  return { kind: 'muted', text: 'Не подключена' }
})
</script>

<template>
  <div class="page narrow">
    <IntegrationHeader title="«По Делу»" subtitle="Задачи сотрудников ссылаются на проекты и строки планов; структура компании и сотрудники — из «По Делу»" logo="podelu" :badge="badge" help="admin-podelu" />
    <PodeluLinksSettings @changed="load" />
    <PodeluSettings />
  </div>
</template>

<style scoped>
.narrow { max-width: 920px; }
</style>
