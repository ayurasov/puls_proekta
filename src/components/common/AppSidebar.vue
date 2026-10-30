<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import { useProjectsStore } from '../../stores/projects'
import { localStorageGet, localStorageSet, useUiStore } from '../../stores/ui'
import AppIcon from './AppIcon.vue'
import UserAvatar from './UserAvatar.vue'
import ProfileModal from './ProfileModal.vue'
import packageJson from '../../../package.json'

const auth = useAuthStore()
const projects = useProjectsStore()
const ui = useUiStore()
const route = useRoute()
const router = useRouter()
const appVersion = packageJson.version
const profileOpen = ref(false)
const expanded = ref(readExpanded())

function readExpanded() {
  try { return JSON.parse(localStorageGet('puls.sidebarGroups') || '{}') } catch { return {} }
}
function groupKey(group) {
  return group.direction ? String(group.direction.id) : 'none'
}
function isExpanded(group) {
  const key = groupKey(group)
  if (key in expanded.value) return expanded.value[key]
  return group.projects.some((p) => String(p.id) === route.params.projectId) || projects.grouped.length <= 3
}
function toggle(group) {
  expanded.value = { ...expanded.value, [groupKey(group)]: !isExpanded(group) }
  localStorageSet('puls.sidebarGroups', JSON.stringify(expanded.value))
}

const groups = computed(() => projects.grouped.filter((g) => g.projects.length || g.direction))
const adminLinks = [
  { to: '/admin/users', icon: 'users', label: 'Сотрудники' },
  { to: '/admin/directions', icon: 'folder', label: 'Направления' },
  { to: '/admin/access', icon: 'shield', label: 'Доступы' },
  { to: '/admin/audit', icon: 'history', label: 'Реестр изменений' },
  { to: '/admin/trash', icon: 'trash', label: 'Корзина' },
  { to: '/admin/settings', icon: 'settings', label: 'Настройки' },
]

onMounted(() => { if (!projects.loaded) projects.load().catch(() => {}) })

async function logout() {
  await auth.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <aside class="sidebar scroll-thin" :class="{ collapsed: ui.sidebarCollapsed }">
    <div class="sidebar-top">
      <router-link to="/" class="brand" title="Пульс Проекта">
        <span class="brand-mark"><AppIcon name="pulse" :size="18" :stroke-width="2.2" /></span>
        <span v-if="!ui.sidebarCollapsed" class="brand-text">
          <span class="brand-name">Пульс Проекта</span>
          <span class="brand-tagline">Планы и сроки проектов</span>
        </span>
      </router-link>
      <button class="collapse-btn" type="button" :title="ui.sidebarCollapsed ? 'Развернуть меню' : 'Свернуть меню'" @click="ui.toggleSidebar()">
        <AppIcon :name="ui.sidebarCollapsed ? 'chevronRight' : 'chevronLeft'" :size="14" />
      </button>
    </div>

    <nav class="sidebar-nav" aria-label="Основная навигация">
      <router-link to="/" class="nav-item" :class="{ 'router-link-active': route.name === 'projects' }" exact-active-class="" title="Проекты">
        <AppIcon name="home" /><span v-if="!ui.sidebarCollapsed" class="nav-label">Проекты</span>
      </router-link>
    </nav>

    <div v-if="!ui.sidebarCollapsed" class="sidebar-section">
      <div class="section-caption">Направления</div>
      <div v-for="group in groups" :key="groupKey(group)" class="nav-group">
        <button class="nav-item nav-group-toggle" type="button" :aria-expanded="isExpanded(group)" @click="toggle(group)">
          <AppIcon :name="isExpanded(group) ? 'chevronDown' : 'chevronRight'" :size="14" class="caret" />
          <span class="nav-label">{{ group.direction ? group.direction.name : 'Без направления' }}</span>
          <span class="nav-count">{{ group.projects.length }}</span>
        </button>
        <div v-if="isExpanded(group)" class="nav-sub">
          <router-link
            v-for="project in group.projects" :key="project.id" :to="`/projects/${project.id}`"
            class="nav-item nav-subitem" :class="{ 'router-link-active': String(project.id) === route.params.projectId }"
            :title="project.title"
          >
            <span class="nav-label">{{ project.title }}</span>
          </router-link>
          <div v-if="!group.projects.length" class="nav-empty">Нет проектов</div>
        </div>
      </div>
    </div>

    <div v-if="auth.isAdmin" class="sidebar-section">
      <div v-if="!ui.sidebarCollapsed" class="section-caption">Администрирование</div>
      <router-link v-for="link in adminLinks" :key="link.to" :to="link.to" class="nav-item" :title="link.label">
        <AppIcon :name="link.icon" /><span v-if="!ui.sidebarCollapsed" class="nav-label">{{ link.label }}</span>
      </router-link>
    </div>

    <div class="sidebar-bottom">
      <router-link
        to="/guide" class="nav-item" :class="{ 'router-link-active': route.path.startsWith('/guide') }"
        title="Руководство" data-testid="guide-link"
      >
        <AppIcon name="book" /><span v-if="!ui.sidebarCollapsed" class="nav-label">Руководство</span>
      </router-link>
      <button class="user-card" type="button" title="Профиль" @click="profileOpen = true">
        <UserAvatar :user="auth.user" :size="30" />
        <span v-if="!ui.sidebarCollapsed" class="user-text">
          <span class="user-name ellipsis">{{ auth.user?.name }}</span>
          <span class="user-role ellipsis">{{ auth.roleLabel }}</span>
        </span>
      </button>
      <button class="nav-item" type="button" title="Выйти" @click="logout">
        <AppIcon name="logout" /><span v-if="!ui.sidebarCollapsed" class="nav-label">Выйти</span>
      </button>
      <div v-if="!ui.sidebarCollapsed" class="sidebar-meta">
        <span class="author">© Alexander Yurasov <router-link to="/about" class="about-mark" title="О программе">?</router-link></span>
        <router-link to="/about" class="version">Версия {{ appVersion }}</router-link>
      </div>
    </div>
    <ProfileModal v-if="profileOpen" @close="profileOpen = false" />
  </aside>
</template>

<style scoped>
.sidebar { width: 248px; background: var(--color-surface); border-right: 1px solid var(--color-border); display: flex; flex-direction: column; padding: 14px 10px; overflow-y: auto; overflow-x: hidden; transition: width 0.18s ease; flex-shrink: 0; }
.sidebar.collapsed { width: 60px; padding: 14px 8px; }
.sidebar-top { display: flex; align-items: center; justify-content: space-between; gap: 6px; padding: 2px 4px 14px; }
.sidebar.collapsed .sidebar-top { flex-direction: column; gap: 10px; }
.brand { display: flex; align-items: center; gap: 9px; text-decoration: none; color: inherit; min-width: 0; }
.brand-mark { width: 30px; height: 30px; border-radius: 9px; background: var(--color-primary); color: #fff; display: flex; align-items: center; justify-content: center; flex-shrink: 0; box-shadow: 0 4px 10px rgba(79, 124, 255, 0.3); }
.brand-text { display: flex; flex-direction: column; min-width: 0; }
.brand-name { font-weight: 700; font-size: 15px; line-height: 1.2; white-space: nowrap; }
.brand-tagline { font-size: 10.5px; color: var(--color-text-muted); white-space: nowrap; }
.collapse-btn { border: 1px solid var(--color-border); background: var(--color-surface); border-radius: 7px; width: 24px; height: 24px; display: flex; align-items: center; justify-content: center; color: var(--color-text-muted); flex-shrink: 0; }
.collapse-btn:hover { background: #eef1f7; color: var(--color-text); }
.sidebar-nav { display: flex; flex-direction: column; gap: 2px; }
.sidebar-section { margin-top: 14px; display: flex; flex-direction: column; gap: 2px; }
.section-caption { font-size: 11px; color: var(--color-text-muted); padding: 4px 10px; text-transform: uppercase; letter-spacing: 0.04em; white-space: nowrap; }
.nav-item { display: flex; align-items: center; gap: 10px; padding: 7px 10px; border-radius: var(--radius-sm); font-size: 13px; color: var(--color-text); text-decoration: none; border: 0; background: none; text-align: left; width: 100%; white-space: nowrap; min-height: 32px; }
.sidebar.collapsed .nav-item { justify-content: center; padding: 8px; }
.nav-item:hover { background: #eef1f7; }
.nav-item.router-link-active { background: var(--color-primary-soft); color: var(--color-primary-dark); font-weight: 600; }
.nav-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.nav-group-toggle { font-weight: 600; color: var(--color-text-secondary); gap: 6px; }
.caret { color: var(--color-text-muted); }
.nav-count { min-width: 20px; padding: 1px 6px; border-radius: 999px; background: #eef1f7; color: var(--color-text-muted); font-size: 10.5px; text-align: center; font-weight: 600; }
.nav-sub { display: flex; flex-direction: column; gap: 1px; padding-left: 18px; margin-bottom: 4px; }
.nav-subitem { font-size: 12.5px; padding: 5px 10px; min-height: 28px; }
.nav-empty { font-size: 11.5px; color: var(--color-text-muted); padding: 4px 10px; }
.sidebar-bottom { margin-top: auto; padding-top: 10px; border-top: 1px solid var(--color-border); display: flex; flex-direction: column; gap: 2px; }
.user-card { display: flex; align-items: center; gap: 10px; padding: 6px 8px; border: 0; background: transparent; border-radius: var(--radius-sm); text-align: left; min-width: 0; }
.sidebar.collapsed .user-card { justify-content: center; padding: 6px 0; }
.user-card:hover { background: #eef1f7; }
.user-text { display: flex; flex-direction: column; min-width: 0; }
.user-name { font-size: 13px; font-weight: 600; }
.user-role { font-size: 11px; color: var(--color-text-muted); }
.sidebar-meta { display: flex; flex-direction: column; gap: 4px; padding: 8px 10px 0; font-size: 10.5px; color: var(--color-text-muted); }
.author { display: flex; align-items: center; gap: 6px; }
.about-mark { display: inline-flex; align-items: center; justify-content: center; width: 16px; height: 16px; border-radius: 50%; border: 1px solid currentColor; color: inherit; font-size: 10px; font-weight: 700; text-decoration: none; }
.about-mark:hover { background: var(--color-primary); border-color: var(--color-primary); color: #fff; }
.version { color: inherit; text-decoration: none; }
.version:hover { color: var(--color-primary); text-decoration: underline; }
</style>
