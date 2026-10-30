import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { legacyHashTarget, router } from './router'
import { useAuthStore } from './stores/auth'
import { setAuthLostHandler } from './api/client'
import './style.css'

const legacy = legacyHashTarget(window.location.hash)
if (legacy) window.history.replaceState(null, '', legacy)

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)

const auth = useAuthStore()
setAuthLostHandler(() => {
  if (!auth.user) return
  auth.setUser(null)
  router.push({ name: 'login', query: { next: router.currentRoute.value.fullPath, expired: '1' } })
})

// Старая ссылка, открытая в уже загруженном приложении (меняется только #…)
window.addEventListener('hashchange', () => {
  const target = legacyHashTarget(window.location.hash)
  if (target) router.replace(target)
})

auth.bootstrap().finally(() => {
  app.use(router)
  app.mount('#app')
})
