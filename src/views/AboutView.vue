<script setup>
import { computed, ref } from 'vue'
import packageJson from '../../package.json'
import AppIcon from '../components/common/AppIcon.vue'
import ReleaseNotesModal from '../components/common/ReleaseNotesModal.vue'
import { useAuthStore } from '../stores/auth'
import { availableGuides, guideDownloads } from '../docs/guides'

const version = packageJson.version
const auth = useAuthStore()
const releaseNotesOpen = ref(false)
// Руководство администратора — только администраторам
const guides = computed(() => availableGuides(auth).map((guide) => ({ ...guide, files: guideDownloads(guide.key) })))
</script>

<template>
  <div class="page about">
    <div class="card hero">
      <span class="mark"><AppIcon name="pulse" :size="30" :stroke-width="2.2" /></span>
      <div>
        <h1>Пульс Проекта</h1>
        <p class="muted">Планирование проектов на диаграммах Ганта · версия {{ version }}</p>
      </div>
    </div>
    <div class="grid">
      <section class="card block">
        <h2 class="section-title">Возможности</h2>
        <ul>
          <li>направления и проекты, несколько планов (версий) у проекта, архив и корзина;</li>
          <li>задачи, подзадачи, группы, вехи, пометки, связи с лагом и автосдвигом;</li>
          <li>рабочие дни по производственному календарю РФ, временные сдвиги сроков;</li>
          <li>совместная работа: правки сохраняются сразу и видны коллегам, конфликты подсвечиваются;</li>
          <li>история изменений «было → стало», сохранённые версии и восстановление;</li>
          <li>роли, доступы к проектам и направлениям, вход через домен (LDAP);</li>
          <li>экспорт в PNG, PDF, SVG, Excel (CSV) и JSON.</li>
        </ul>
      </section>
      <section class="card block">
        <h2 class="section-title">Что нового в 0.2</h2>
        <ul>
          <li>новая платформа: диаграмма — часть приложения, а не встроенная страница;</li>
          <li>одновременная работа без потери чужих правок, индикатор «сейчас на плане»;</li>
          <li>окна приложения вместо системных подтверждений браузера;</li>
          <li>закрытые вкладки-версии не теряются — уходят в архив;</li>
          <li>данные прежней версии перенесены полностью, включая историю и комментарии.</li>
        </ul>
        <p class="guide">
          <button class="btn btn-sm" type="button" @click="releaseNotesOpen = true"><AppIcon name="star" :size="14" /> Что нового по версиям</button>
        </p>
        <p class="muted small">Автор — Alexander Yurasov.</p>
      </section>
    </div>
    <ReleaseNotesModal v-if="releaseNotesOpen" @close="releaseNotesOpen = false" />

    <section class="docs" aria-labelledby="about-docs-title">
      <h2 id="about-docs-title" class="section-title">Документация</h2>
      <div class="docs-grid">
        <article v-for="guide in guides" :key="guide.key" class="card doc" :data-testid="`about-guide-${guide.key}`">
          <span class="doc-icon" aria-hidden="true"><AppIcon name="book" :size="22" /></span>
          <div class="doc-body">
            <h3>{{ guide.title }}</h3>
            <p>{{ guide.description }} Соответствует установленной версии {{ version }}.</p>
            <div class="doc-actions">
              <router-link :to="guide.path" class="btn btn-sm btn-primary"><AppIcon name="book" :size="13" />Читать в приложении</router-link>
              <a class="btn btn-sm" :href="guide.files.pdfUrl" :download="guide.files.pdfName"><AppIcon name="download" :size="13" />Скачать PDF</a>
              <a class="btn btn-sm" :href="guide.files.docxUrl" :download="guide.files.docxName"><AppIcon name="file" :size="13" />Скачать DOCX</a>
            </div>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.guide { margin: 12px 0 8px; display: flex; flex-wrap: wrap; gap: 8px; }
.about { max-width: 980px; }
.hero { display: flex; align-items: center; gap: 18px; padding: 22px; margin-bottom: 14px; }
.hero h1 { font-size: 22px; }
.hero p { margin: 4px 0 0; }
.mark { width: 56px; height: 56px; border-radius: 16px; background: var(--color-primary); color: #fff; display: flex; align-items: center; justify-content: center; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.block { padding: 16px 18px; }
.block ul { margin: 10px 0 0; padding-left: 18px; line-height: 1.6; font-size: 13.5px; }
.docs { margin-top: 22px; }
.docs .section-title { margin-bottom: 10px; }
.docs-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; }
.doc { display: flex; gap: 14px; padding: 18px; }
.doc-icon { display: inline-flex; align-items: center; justify-content: center; width: 44px; height: 44px; border-radius: 12px; background: var(--color-primary-soft); color: var(--color-primary-dark); flex-shrink: 0; }
.doc-body { min-width: 0; }
.doc-body h3 { font-size: 14.5px; margin-bottom: 4px; }
.doc-body p { margin: 0; font-size: 13px; line-height: 1.5; color: var(--color-text-muted); }
.doc-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
@media (max-width: 768px) {
  .grid { grid-template-columns: 1fr; }
  .doc-actions .btn { flex: 1; min-height: 36px; }
}
</style>
