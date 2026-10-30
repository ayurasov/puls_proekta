<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import { canReadGuide, GUIDES } from '../../docs/guides'
import { helpTopic } from '../../docs/helpTopics'
import AppIcon from './AppIcon.vue'

/**
 * Контекстная справка: знак «?» открывает раздел руководства о том, что на экране
 * (темы — docs/helpTopics.js). Открывается в новой вкладке, чтобы не терять открытый план,
 * фильтры и несохранённые поля формы. Без доступа к руководству (раздел администратора
 * у остальных) знак не показывается.
 *
 *   <HelpLink topic="plan-settings" />            — только значок
 *   <HelpLink topic="admin-users" label="Справка" /> — значок с подписью
 */
const props = defineProps({
  topic: { type: String, required: true },
  label: { type: String, default: '' },
  size: { type: [Number, String], default: 15 },
})

const router = useRouter()
const auth = useAuthStore()

const target = computed(() => {
  const topic = helpTopic(props.topic)
  if (!topic || !canReadGuide(topic.guide, auth)) return null
  const guide = GUIDES[topic.guide]
  return {
    href: router.resolve({ path: guide.path, hash: `#${topic.id}` }).href,
    title: topic.title,
    guide: guide.title,
  }
})
</script>

<template>
  <a
    v-if="target" class="btn btn-ghost btn-sm help-link" :class="{ 'btn-icon': !label }" :href="target.href"
    target="_blank" rel="noopener" :title="`${target.guide}: «${target.title}» — откроется в новой вкладке`"
    :aria-label="`Справка: ${target.title} (новая вкладка)`" data-testid="help-link" :data-help-topic="topic"
  >
    <AppIcon name="help" :size="size" /><span v-if="label">{{ label }}</span>
  </a>
</template>

<style scoped>
.help-link { color: var(--color-text-muted); }
.help-link:hover { color: var(--color-primary-dark); }
</style>
