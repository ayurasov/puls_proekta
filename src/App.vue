<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from './stores/auth'
import AppSidebar from './components/common/AppSidebar.vue'
import DialogHost from './components/common/DialogHost.vue'
import ToastHost from './components/common/ToastHost.vue'

const route = useRoute()
const auth = useAuthStore()
const isPublic = computed(() => route.meta?.public === true)
const fill = computed(() => route.meta?.layout === 'fill')
</script>

<template>
  <router-view v-if="isPublic || !auth.user" />
  <div v-else class="app-shell">
    <AppSidebar />
    <main class="app-main" :class="{ 'is-fill': fill }">
      <router-view :key="route.name === 'project' ? route.params.projectId : route.fullPath" />
    </main>
  </div>
  <DialogHost />
  <ToastHost />
</template>

<style>
.app-shell { display: flex; height: 100vh; overflow: hidden; }
@supports (height: 100dvh) { .app-shell { height: 100dvh; } }
.app-main { flex: 1; min-width: 0; overflow-y: auto; }
.app-main.is-fill { overflow: hidden; display: flex; flex-direction: column; }
</style>
