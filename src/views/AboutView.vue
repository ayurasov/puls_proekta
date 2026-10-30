<script setup>
import packageJson from '../../package.json'
import AppIcon from '../components/common/AppIcon.vue'
const version = packageJson.version
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
          <li>данные 1.0.1 перенесены полностью, включая историю и комментарии.</li>
        </ul>
        <p class="muted small">Автор — Alexander Yurasov.</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.about { max-width: 980px; }
.hero { display: flex; align-items: center; gap: 18px; padding: 22px; margin-bottom: 14px; }
.hero h1 { font-size: 22px; }
.hero p { margin: 4px 0 0; }
.mark { width: 56px; height: 56px; border-radius: 16px; background: var(--color-primary); color: #fff; display: flex; align-items: center; justify-content: center; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.block { padding: 16px 18px; }
.block ul { margin: 10px 0 0; padding-left: 18px; line-height: 1.6; font-size: 13.5px; }
@media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
</style>
