<script setup>
import { computed, ref } from 'vue'
import BaseModal from './BaseModal.vue'
import { parseReleaseNotes } from '../../utils/releaseNotes'
import versionsMarkdown from '../../../docs/versions.md?raw'

/** «Что нового» — история версий из docs/versions.md (как в «По Делу»): последняя версия раскрыта. */
const emit = defineEmits(['close'])
const releases = computed(() => parseReleaseNotes(versionsMarkdown))
const expanded = ref(new Set(releases.value.length ? [releases.value[0].version] : []))

function toggle(version) {
  const next = new Set(expanded.value)
  if (next.has(version)) next.delete(version)
  else next.add(version)
  expanded.value = next
}
</script>

<template>
  <BaseModal title="Что нового в «Пульсе Проекта»" size="wide" initial-focus=".release-toggle" @close="emit('close')">
    <p class="release-lead muted">Изменения по версиям — простым языком. Номер версии — внизу бокового меню.</p>
    <article
      v-for="(release, index) in releases" :key="release.version" class="release-item"
      :class="{ expanded: expanded.has(release.version), latest: index === 0 }"
    >
      <button class="release-toggle" type="button" :aria-expanded="expanded.has(release.version)" @click="toggle(release.version)">
        <span class="release-mark">{{ index === 0 ? 'NEW' : 'v' }}</span>
        <span class="release-copy">
          <strong>Версия {{ release.version }}</strong>
          <small v-if="release.lead.length">
            <template v-for="(part, i) in release.lead[0]" :key="i"><b v-if="part.bold">{{ part.text }}</b><template v-else>{{ part.text }}</template></template>
          </small>
        </span>
        <span class="release-chevron" aria-hidden="true">{{ expanded.has(release.version) ? '⌃' : '⌄' }}</span>
      </button>
      <div v-if="expanded.has(release.version)" class="release-content">
        <p v-for="(paragraph, i) in release.lead.slice(1)" :key="'lead' + i">
          <template v-for="(part, j) in paragraph" :key="j"><b v-if="part.bold">{{ part.text }}</b><template v-else>{{ part.text }}</template></template>
        </p>
        <section v-for="section in release.sections" :key="section.title" class="release-section">
          <h3>{{ section.title }}</h3>
          <p v-for="(paragraph, i) in section.paragraphs" :key="'p' + i">
            <template v-for="(part, j) in paragraph" :key="j"><b v-if="part.bold">{{ part.text }}</b><template v-else>{{ part.text }}</template></template>
          </p>
          <ul v-if="section.items.length">
            <li v-for="(item, i) in section.items" :key="'i' + i">
              <template v-for="(part, j) in item" :key="j"><b v-if="part.bold">{{ part.text }}</b><template v-else>{{ part.text }}</template></template>
            </li>
          </ul>
        </section>
      </div>
    </article>
  </BaseModal>
</template>

<style scoped>
.release-lead { margin: 0 0 12px; font-size: 12.5px; }
.release-item { border: 1px solid var(--color-border); border-radius: 12px; margin-bottom: 9px; overflow: hidden; background: var(--color-surface); }
.release-item.latest { border-color: rgba(79, 124, 255, 0.32); }
.release-item.expanded { box-shadow: 0 8px 22px rgba(42, 57, 96, 0.07); }
.release-toggle { width: 100%; display: flex; align-items: flex-start; gap: 12px; padding: 14px 15px; border: 0; background: transparent; color: var(--color-text); text-align: left; }
.release-toggle:hover { background: #f7f8fc; }
.release-mark { width: 34px; height: 24px; display: inline-flex; align-items: center; justify-content: center; flex: 0 0 auto; border-radius: 7px; background: #eef2ff; color: var(--color-primary-dark); font-size: 9px; font-weight: 800; letter-spacing: 0.04em; }
.release-item.latest .release-mark { background: var(--color-primary); color: #fff; }
.release-copy { min-width: 0; flex: 1 1 auto; display: flex; flex-direction: column; gap: 3px; }
.release-copy strong { font-size: 14px; line-height: 1.35; }
.release-copy small { color: var(--color-text-muted); font-size: 12px; line-height: 1.45; overflow-wrap: anywhere; }
.release-chevron { margin-left: auto; padding-top: 2px; color: var(--color-primary); font-size: 18px; line-height: 1; flex: 0 0 auto; }
.release-content { padding: 4px 18px 16px 61px; border-top: 1px solid #eef0f5; }
.release-content > p { margin: 12px 0 0; color: var(--color-text-secondary); font-size: 12.5px; line-height: 1.55; }
.release-section { padding-top: 14px; }
.release-section h3 { margin: 0 0 6px; font-size: 13px; color: var(--color-text); }
.release-section p, .release-section li { margin: 0 0 6px; color: var(--color-text-secondary); font-size: 12.5px; line-height: 1.55; }
.release-section ul { margin: 0; padding-left: 18px; }
@media (max-width: 560px) { .release-content { padding-left: 16px; } }
</style>
