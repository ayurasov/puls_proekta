<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppIcon from '../../components/common/AppIcon.vue'
import StructureTransfer from '../../components/org-transfer/StructureTransfer.vue'
import EmployeesTransfer from '../../components/org-transfer/EmployeesTransfer.vue'
import QuickCsvImport from '../../components/org-transfer/QuickCsvImport.vue'
import AdSync from '../../components/org-transfer/AdSync.vue'

/**
 * Структура компании → Импорт и экспорт (ADR-0008): перенос структуры и сотрудников файлами,
 * общими с todo-planner «По Делу», быстрая загрузка из кадровой системы и синхронизация с Active
 * Directory. Везде сначала — проверка без записи, затем применение одной транзакцией.
 */
const TABS = [
  { key: 'structure', label: 'Структура (JSON)', icon: 'hierarchy' },
  { key: 'employees', label: 'Сотрудники (CSV, JSON, ZIP)', icon: 'users' },
  { key: 'csv', label: 'Быстрый импорт CSV', icon: 'file' },
  { key: 'ad', label: 'Active Directory', icon: 'link' },
]
const route = useRoute()
const router = useRouter()
const tab = computed(() => (TABS.some((item) => item.key === route.query.tab) ? route.query.tab : 'structure'))
function selectTab(key) {
  router.replace({ query: { ...route.query, tab: key } })
}
</script>

<template>
  <div class="page narrow">
    <div class="page-header">
      <div>
        <h1 class="page-title">Импорт и экспорт структуры</h1>
        <div class="page-subtitle">Структура компании и сотрудники: перенос файлами в формате «По Делу», загрузка из кадровой системы, синхронизация с Active Directory</div>
      </div>
    </div>

    <div class="segmented tabs" role="tablist" aria-label="Что загружать или выгружать">
      <button v-for="item in TABS" :key="item.key" type="button" role="tab" :aria-selected="tab === item.key" :class="{ 'is-active': tab === item.key }" :data-testid="`transfer-tab-${item.key}`" @click="selectTab(item.key)">
        <AppIcon :name="item.icon" :size="13" /> {{ item.label }}
      </button>
    </div>

    <StructureTransfer v-if="tab === 'structure'" />
    <EmployeesTransfer v-else-if="tab === 'employees'" />
    <QuickCsvImport v-else-if="tab === 'csv'" />
    <AdSync v-else-if="tab === 'ad'" />
  </div>
</template>

<style scoped>
.narrow { max-width: 1000px; }
.tabs { margin-bottom: 14px; flex-wrap: wrap; }
.tabs button { display: inline-flex; align-items: center; gap: 6px; }
</style>
