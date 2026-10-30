<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from './stores/auth'
import { useUiStore } from './stores/ui'
import AppSidebar from './components/common/AppSidebar.vue'
import AppIcon from './components/common/AppIcon.vue'
import DialogHost from './components/common/DialogHost.vue'
import ToastHost from './components/common/ToastHost.vue'

const route = useRoute()
const auth = useAuthStore()
const ui = useUiStore()
const isPublic = computed(() => route.meta?.public === true)
const fill = computed(() => route.meta?.layout === 'fill')

// ---------- меню на телефоне: выдвижная панель с фоном, фокус внутри, Esc закрывает ----------
const navButton = ref(null)
let focusBeforeNav = null

function navFocusable() {
  const nav = document.getElementById('mobile-navigation')
  if (!nav) return []
  return [...nav.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])')]
    .filter((el) => el.getClientRects().length > 0)
}
function onGlobalKeydown(event) {
  if (!ui.mobileNavOpen) return
  if (event.key === 'Escape') { event.preventDefault(); ui.closeMobileNav(); return }
  if (event.key !== 'Tab') return
  const nav = document.getElementById('mobile-navigation')
  const focusable = navFocusable()
  if (!nav || !focusable.length) { event.preventDefault(); nav?.focus(); return }
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  const outside = !nav.contains(document.activeElement)
  if (event.shiftKey && (document.activeElement === first || outside)) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && (document.activeElement === last || outside)) { event.preventDefault(); first.focus() }
}

watch(() => route.fullPath, () => ui.closeMobileNav())
watch(() => ui.mobileNavOpen, async (open, wasOpen) => {
  document.body.classList.toggle('mobile-nav-open', open)
  if (open) {
    focusBeforeNav = document.activeElement
    await nextTick()
    navFocusable()[0]?.focus()
  } else if (wasOpen) {
    await nextTick()
    const target = focusBeforeNav?.isConnected ? focusBeforeNav : navButton.value
    target?.focus?.()
    focusBeforeNav = null
  }
})
onMounted(() => window.addEventListener('keydown', onGlobalKeydown, true))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalKeydown, true)
  document.body.classList.remove('mobile-nav-open')
})
</script>

<template>
  <router-view v-if="isPublic || !auth.user" />
  <div v-else class="app-shell">
    <AppSidebar id="mobile-navigation" :class="{ 'mobile-open': ui.mobileNavOpen }" tabindex="-1" />
    <button
      ref="navButton" class="mobile-nav-trigger" type="button" aria-label="Открыть меню" aria-controls="mobile-navigation"
      :aria-expanded="ui.mobileNavOpen" :tabindex="ui.mobileNavOpen ? -1 : 0" data-testid="mobile-nav" @click="ui.openMobileNav()"
    >
      <AppIcon name="menu" :size="22" />
    </button>
    <main class="app-main" :class="{ 'is-fill': fill }" :inert="ui.mobileNavOpen" :aria-hidden="ui.mobileNavOpen ? 'true' : undefined">
      <router-view :key="route.name === 'project' ? route.params.projectId : route.fullPath" />
    </main>
    <button v-if="ui.mobileNavOpen" class="mobile-nav-backdrop" type="button" tabindex="-1" aria-label="Закрыть меню" @click="ui.closeMobileNav()" />
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
