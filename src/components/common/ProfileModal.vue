<script setup>
import { ref } from 'vue'
import { api } from '../../api/client'
import { useAuthStore } from '../../stores/auth'
import { useUiStore } from '../../stores/ui'
import { useDialogs } from '../../composables/useDialogs'
import { resizeImage } from '../../utils/images'
import BaseModal from './BaseModal.vue'
import UserAvatar from './UserAvatar.vue'
import AppIcon from './AppIcon.vue'

const emit = defineEmits(['close'])
const auth = useAuthStore()
const ui = useUiStore()
const { confirm } = useDialogs()
const tab = ref('info')
const fileInput = ref(null)
const uploading = ref(false)
const form = ref({ current: '', next: '', repeat: '' })
const saving = ref(false)
const error = ref('')
const photoOpen = ref(false)

async function onFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (!file.type.startsWith('image/')) { ui.error('Нужно изображение'); return }
  uploading.value = true
  try {
    const blob = await resizeImage(file, 512)
    const formData = new FormData()
    formData.append('file', blob, 'avatar.jpg')
    const data = await api.upload('/profile/avatar', formData)
    auth.setUser(data.user)
    ui.success('Фото обновлено')
  } catch (err) {
    ui.error(err)
  } finally {
    uploading.value = false
  }
}

async function removePhoto() {
  if (!await confirm({ title: 'Удалить фото?', message: 'Вместо фото будут показаны инициалы.', confirmText: 'Удалить' })) return
  try {
    const data = await api.delete('/profile/avatar')
    auth.setUser(data.user)
  } catch (err) { ui.error(err) }
}

async function changePassword() {
  error.value = ''
  if (form.value.next !== form.value.repeat) { error.value = 'Новый пароль и подтверждение не совпадают'; return }
  if (form.value.next.length < 8) { error.value = 'Пароль должен быть не короче 8 символов'; return }
  saving.value = true
  try {
    await api.put('/profile/password', { currentPassword: form.value.current, newPassword: form.value.next })
    form.value = { current: '', next: '', repeat: '' }
    ui.success('Пароль изменён', 'На других устройствах нужно войти заново')
    tab.value = 'info'
  } catch (err) {
    error.value = err.message
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <BaseModal title="Профиль" @close="emit('close')">
    <div class="profile-head">
      <div class="photo">
        <button class="photo-button" type="button" :disabled="!auth.user?.avatarUrl" title="Открыть фото" @click="photoOpen = true">
          <UserAvatar :user="auth.user" :size="96" />
        </button>
        <button class="photo-edit btn btn-primary btn-icon btn-sm" type="button" :disabled="uploading" title="Загрузить фото" @click="fileInput.click()">
          <AppIcon name="upload" :size="14" />
        </button>
        <input ref="fileInput" type="file" accept="image/*" hidden @change="onFile" />
      </div>
      <div class="who">
        <div class="name">{{ auth.user?.name }}</div>
        <div v-if="auth.user?.position" class="muted">{{ auth.user.position }}</div>
        <div class="muted small">@{{ auth.user?.login }} · {{ auth.roleLabel }}</div>
        <button v-if="auth.user?.avatarUrl" class="btn btn-ghost btn-sm btn-text-danger" type="button" @click="removePhoto">Удалить фото</button>
      </div>
    </div>
    <div class="segmented tabs">
      <button type="button" :class="{ 'is-active': tab === 'info' }" @click="tab = 'info'">Данные</button>
      <button v-if="auth.user?.authSource === 'local'" type="button" :class="{ 'is-active': tab === 'password' }" @click="tab = 'password'">Пароль</button>
    </div>
    <dl v-if="tab === 'info'" class="info">
      <dt>ФИО</dt><dd>{{ auth.user?.name }}</dd>
      <dt>Должность</dt><dd>{{ auth.user?.position || '—' }}</dd>
      <dt>Логин</dt><dd>{{ auth.user?.login }}</dd>
      <dt>Email</dt><dd>{{ auth.user?.email || '—' }}</dd>
      <dt>Вход</dt><dd>{{ auth.user?.authSource === 'ldap' ? 'учётная запись домена' : 'пароль системы' }}</dd>
    </dl>
    <p v-if="tab === 'info'" class="muted small">Изменить ФИО, должность или email может администратор.</p>
    <form v-if="tab === 'password'" class="form-stack" @submit.prevent="changePassword">
      <label class="field"><span class="field-label">Текущий пароль</span><input v-model="form.current" class="input" type="password" autocomplete="current-password" /></label>
      <label class="field"><span class="field-label">Новый пароль</span><input v-model="form.next" class="input" type="password" autocomplete="new-password" /><span class="field-hint">Не короче 8 символов</span></label>
      <label class="field"><span class="field-label">Повторите новый пароль</span><input v-model="form.repeat" class="input" type="password" autocomplete="new-password" /></label>
      <p v-if="error" class="field-error">{{ error }}</p>
      <button class="btn btn-primary" type="submit" :disabled="saving || !form.current || !form.next">Сменить пароль</button>
    </form>
  </BaseModal>
  <BaseModal v-if="photoOpen" :title="auth.user?.name" size="wide" @close="photoOpen = false">
    <img class="big-photo" :src="auth.user?.avatarUrl" alt="" />
  </BaseModal>
</template>

<style scoped>
.profile-head { display: flex; align-items: center; gap: 18px; margin-bottom: 16px; }
.photo { position: relative; }
.photo-button { border: 0; padding: 0; background: none; border-radius: 50%; }
.photo-button:disabled { cursor: default; }
.photo-edit { position: absolute; right: -2px; bottom: -2px; border-radius: 50%; }
.who { display: flex; flex-direction: column; gap: 3px; align-items: flex-start; min-width: 0; }
.name { font-size: 17px; font-weight: 700; }
.tabs { margin-bottom: 14px; }
.info { display: grid; grid-template-columns: 110px 1fr; gap: 8px 12px; margin: 0 0 10px; font-size: 13px; }
.info dt { color: var(--color-text-muted); }
.info dd { margin: 0; overflow-wrap: anywhere; }
.big-photo { display: block; max-width: 100%; max-height: 70vh; margin: 0 auto; border-radius: var(--radius-md); }
</style>
