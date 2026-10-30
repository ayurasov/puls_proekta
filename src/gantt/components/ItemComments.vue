<script setup>
import { computed, inject, onMounted, ref, watch } from 'vue'
import { api } from '../../api/client'
import { useAuthStore } from '../../stores/auth'
import { useUiStore } from '../../stores/ui'
import { useDialogs } from '../../composables/useDialogs'
import { formatRelative, formatDateTime } from '../../utils/format'
import AppIcon from '../../components/common/AppIcon.vue'
import UserAvatar from '../../components/common/UserAvatar.vue'

const props = defineProps({ item: { type: Object, required: true } })
const editor = inject('gantt')
const auth = useAuthStore()
const ui = useUiStore()
const { confirm } = useDialogs()
const comments = ref([])
const loading = ref(true)
const text = ref('')
const replyTo = ref(null)
const sending = ref(false)
const editingId = ref(null)
const editText = ref('')

const planId = computed(() => editor.state.planId)
const canWrite = computed(() => editor.canEdit)
const canModerate = computed(() => editor.state.access === 'manage')
const byId = computed(() => Object.fromEntries(comments.value.map((c) => [c.id, c])))
const ordered = computed(() => {
  // Ответы — под своим комментарием
  const roots = comments.value.filter((c) => !c.replyToId || !byId.value[c.replyToId])
  const out = []
  const walk = (comment, depth) => {
    out.push({ comment, depth })
    for (const child of comments.value.filter((c) => c.replyToId === comment.id)) walk(child, Math.min(depth + 1, 3))
  }
  for (const root of roots) walk(root, 0)
  return out
})

async function load() {
  loading.value = true
  try {
    comments.value = await api.get(`/plans/${planId.value}/items/${props.item.key}/comments`)
  } catch (err) { ui.error(err) } finally { loading.value = false }
}
onMounted(load)
watch(() => props.item.commentCount, (n, old) => { if (n !== old && !sending.value) load() })

function linkify(value) {
  const escaped = value.replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch])
  return escaped.replace(/https?:\/\/[^\s<]+/g, (url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`)
}

async function send() {
  const value = text.value.trim()
  if (!value) return
  sending.value = true
  try {
    const res = await api.post(`/plans/${planId.value}/items/${props.item.key}/comments`, { text: value, replyToId: replyTo.value?.id || null })
    comments.value.push(res.comment)
    text.value = ''
    replyTo.value = null
    const local = editor.state.items[props.item.key]
    if (local) local.commentCount = res.commentCount
  } catch (err) { ui.error(err) } finally { sending.value = false }
}

async function saveEdit(comment) {
  const value = editText.value.trim()
  if (!value) return
  try {
    const updated = await api.patch(`/comments/${comment.id}`, { text: value })
    Object.assign(comment, updated)
    editingId.value = null
  } catch (err) { ui.error(err) }
}

async function remove(comment) {
  if (!await confirm({ title: 'Удалить комментарий?', message: comment.text.slice(0, 200), confirmText: 'Удалить' })) return
  try {
    await api.delete(`/comments/${comment.id}`)
    comment.deleted = true
    comment.text = ''
    const local = editor.state.items[props.item.key]
    if (local) local.commentCount = Math.max(0, (local.commentCount || 1) - 1)
  } catch (err) { ui.error(err) }
}

function onKeydown(event) {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); send() }
}
</script>

<template>
  <div class="comments">
    <div v-if="loading" class="muted small">Загрузка…</div>
    <div v-else-if="!comments.length" class="empty muted">
      <span class="empty-icon"><AppIcon name="message" :size="18" /></span>
      Комментариев пока нет
    </div>
    <div v-for="{ comment, depth } in ordered" :key="comment.id" class="comment" :class="{ reply: depth > 0 }" :style="{ marginLeft: depth * 18 + 'px' }">
      <UserAvatar :user="{ name: comment.authorName, avatarUrl: comment.authorAvatarUrl }" :size="28" />
      <div class="comment-main">
        <div class="comment-head">
          <strong>{{ comment.authorName || 'Автор' }}</strong>
          <span class="muted small" :title="formatDateTime(comment.createdAt)">{{ formatRelative(comment.createdAt) }}</span>
          <span v-if="comment.updatedAt" class="muted small">(изменён)</span>
        </div>
        <div v-if="comment.deleted" class="muted small deleted">Комментарий удалён</div>
        <template v-else-if="editingId === comment.id">
          <textarea v-model="editText" class="textarea" rows="3" @keydown.enter.ctrl.prevent="saveEdit(comment)" />
          <div class="row-actions">
            <button class="btn btn-sm btn-primary" type="button" @click="saveEdit(comment)">Сохранить</button>
            <button class="btn btn-sm btn-ghost" type="button" @click="editingId = null">Отмена</button>
          </div>
        </template>
        <!-- eslint-disable-next-line vue/no-v-html -- текст экранирован в linkify -->
        <div v-else class="comment-text" v-html="linkify(comment.text)" />
        <div v-if="!comment.deleted && editingId !== comment.id && canWrite" class="row-actions">
          <button class="link" type="button" @click="replyTo = comment"><AppIcon name="reply" :size="12" /> Ответить</button>
          <button v-if="comment.authorId === auth.user?.id" class="link" type="button" @click="editingId = comment.id; editText = comment.text">Изменить</button>
          <button v-if="comment.authorId === auth.user?.id || canModerate" class="link danger" type="button" @click="remove(comment)">Удалить</button>
        </div>
      </div>
    </div>
    <form v-if="canWrite" class="composer" @submit.prevent="send">
      <div v-if="replyTo" class="replying small">
        <AppIcon name="reply" :size="12" />
        <span class="replying-text">Ответ на комментарий {{ replyTo.authorName }}: «{{ replyTo.text.slice(0, 60) }}»</span>
        <button class="link" type="button" @click="replyTo = null">отменить</button>
      </div>
      <div class="composer-box">
        <textarea v-model="text" class="composer-input" rows="3" placeholder="Написать комментарий… (Ctrl+Enter — отправить)" maxlength="10000" @keydown="onKeydown" />
        <div class="composer-foot">
          <button class="btn btn-primary btn-sm" type="submit" :disabled="sending || !text.trim()"><AppIcon name="send" :size="13" /> Отправить</button>
        </div>
      </div>
    </form>
  </div>
</template>

<style scoped>
.comments { display: flex; flex-direction: column; gap: 14px; }
.empty { display: flex; flex-direction: column; align-items: center; gap: 8px; text-align: center; padding: 28px 0 12px; font-size: 13px; }
.empty-icon { width: 40px; height: 40px; border-radius: 12px; background: #eef1f7; display: inline-flex; align-items: center; justify-content: center; }
.comment { display: flex; gap: 10px; }
.comment.reply { padding-left: 10px; border-left: 2px solid #dbe4ff; }
.comment-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.comment-head { display: flex; align-items: baseline; gap: 8px; font-size: 12.5px; flex-wrap: wrap; }
.comment-text { font-size: 13px; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.5; padding: 8px 12px; background: var(--color-surface-2); border: 1px solid var(--color-border); border-radius: 4px 12px 12px 12px; }
.row-actions { display: flex; gap: 12px; }
.link { border: 0; background: none; padding: 0; color: var(--color-text-muted); font-size: 12px; display: inline-flex; align-items: center; gap: 4px; transition: color 0.15s; }
.link:hover { color: var(--color-primary-dark); text-decoration: underline; }
.link.danger:hover { color: var(--color-danger); }
/* Поле ответа прижато к низу карточки и не уезжает при прокрутке переписки */
.composer { position: sticky; bottom: 0; margin: auto -18px -24px; padding: 12px 18px 16px; background: linear-gradient(180deg, rgba(255, 255, 255, 0.85), #fff 24%); display: flex; flex-direction: column; gap: 8px; border-top: 1px solid var(--color-border); }
.composer-box { border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-surface); transition: border-color 0.15s, box-shadow 0.15s; }
.composer-box:focus-within { border-color: var(--color-primary); box-shadow: var(--focus-ring); }
.composer-input { display: block; width: 100%; min-height: 64px; max-height: 40vh; padding: 10px 12px 4px; border: 0; border-radius: 12px; resize: vertical; background: transparent; font-family: inherit; font-size: 13px; line-height: 1.45; color: var(--color-text); }
.composer-input:focus { outline: none; }
.composer-foot { display: flex; align-items: center; justify-content: flex-end; gap: 8px; padding: 4px 6px 6px 12px; }
.replying { display: flex; align-items: center; gap: 6px; color: var(--color-primary-dark); background: #f3f6ff; padding: 6px 8px; border-radius: 8px; }
.replying-text { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.replying .link { color: var(--color-primary-dark); }
.deleted { font-style: italic; }
@media (max-width: 768px) {
  .composer { margin: auto -16px -24px; padding: 12px 16px calc(14px + env(safe-area-inset-bottom)); }
  .composer-input { font-size: 16px; }
  .link { min-height: 40px; }
}
</style>
