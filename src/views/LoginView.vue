<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/common/AppIcon.vue'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const login = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)
const showPassword = ref(false)
const MODE_HINT = { ldap: 'Вход по учётной записи домена', hybrid: 'Вход по учётной записи домена или паролю системы' }

onMounted(() => {
  auth.loadConfig()
  if (route.query.expired) error.value = 'Сессия завершена — войдите снова'
})

async function submit() {
  error.value = ''
  busy.value = true
  try {
    await auth.login(login.value.trim(), password.value)
    const next = typeof route.query.next === 'string' && route.query.next.startsWith('/') ? route.query.next : '/'
    router.replace(next)
  } catch (err) {
    error.value = err.message || 'Не удалось войти'
    password.value = ''
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-screen">
    <form class="login-card card" @submit.prevent="submit">
      <div class="brand">
        <span class="brand-mark"><AppIcon name="pulse" :size="26" :stroke-width="2.2" /></span>
        <h1>Пульс Проекта</h1>
        <p class="muted">Планы и сроки проектов</p>
      </div>
      <label class="field">
        <span class="field-label">Логин или email</span>
        <input v-model="login" class="input" autocomplete="username" autofocus required data-testid="login" />
      </label>
      <label class="field">
        <span class="field-label">Пароль</span>
        <span class="password">
          <input v-model="password" class="input" :type="showPassword ? 'text' : 'password'" autocomplete="current-password" required data-testid="password" />
          <button class="btn btn-ghost btn-icon btn-sm reveal" type="button" :title="showPassword ? 'Скрыть пароль' : 'Показать пароль'" @click="showPassword = !showPassword">
            <AppIcon name="eye" :size="15" />
          </button>
        </span>
      </label>
      <p v-if="error" class="field-error" role="alert" data-testid="login-error">{{ error }}</p>
      <button class="btn btn-primary submit" type="submit" :disabled="busy || !login || !password" data-testid="login-submit">
        {{ busy ? 'Вход…' : 'Войти' }}
      </button>
      <p v-if="MODE_HINT[auth.config.mode]" class="hint muted small">{{ MODE_HINT[auth.config.mode] }}</p>
      <p class="copyright muted">© Alexander Yurasov</p>
    </form>
  </div>
</template>

<style scoped>
.login-screen { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; background: radial-gradient(1200px 600px at 10% -10%, #e6ecff 0%, transparent 60%), radial-gradient(900px 500px at 110% 110%, #e3f6ec 0%, transparent 55%), var(--color-bg); }
.login-card { width: min(380px, 100%); padding: 28px 28px 18px; display: flex; flex-direction: column; gap: 14px; box-shadow: var(--shadow-2); }
.brand { display: flex; flex-direction: column; align-items: center; gap: 6px; margin-bottom: 6px; text-align: center; }
.brand-mark { width: 52px; height: 52px; border-radius: 15px; background: var(--color-primary); color: #fff; display: flex; align-items: center; justify-content: center; box-shadow: 0 8px 20px rgba(79, 124, 255, 0.35); }
.brand h1 { font-size: 21px; margin-top: 6px; }
.brand p { margin: 0; font-size: 13px; }
.password { position: relative; display: block; }
.password .input { padding-right: 38px; }
.reveal { position: absolute; right: 3px; top: 3px; }
.submit { min-height: 38px; font-size: 14px; }
.hint { text-align: center; margin: 0; }
.copyright { text-align: center; font-size: 11px; margin: 6px 0 0; }
</style>
