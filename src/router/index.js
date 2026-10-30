import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes = [
  { path: '/login', name: 'login', component: () => import('../views/LoginView.vue'), meta: { public: true, title: 'Вход' } },
  { path: '/', name: 'projects', component: () => import('../views/ProjectsView.vue'), meta: { title: 'Проекты' } },
  {
    path: '/projects/:projectId(\\d+)/:planId(\\d+)?', name: 'project',
    component: () => import('../views/ProjectView.vue'), meta: { layout: 'fill' },
  },
  { path: '/admin', redirect: '/admin/users' },
  { path: '/admin/users', name: 'admin-users', component: () => import('../views/admin/UsersView.vue'), meta: { admin: true, title: 'Сотрудники' } },
  { path: '/admin/directions', name: 'admin-directions', component: () => import('../views/admin/DirectionsView.vue'), meta: { admin: true, title: 'Направления' } },
  { path: '/admin/access', name: 'admin-access', component: () => import('../views/admin/AccessView.vue'), meta: { admin: true, title: 'Доступы' } },
  { path: '/admin/audit', name: 'admin-audit', component: () => import('../views/admin/AuditView.vue'), meta: { admin: true, title: 'Реестр изменений' } },
  { path: '/admin/settings', name: 'admin-settings', component: () => import('../views/admin/SettingsView.vue'), meta: { admin: true, title: 'Настройки' } },
  { path: '/admin/integrations', name: 'admin-integrations', component: () => import('../views/admin/IntegrationsView.vue'), meta: { admin: true, title: 'Интеграции' } },
  { path: '/admin/trash', name: 'admin-trash', component: () => import('../views/admin/TrashView.vue'), meta: { admin: true, title: 'Корзина' } },
  { path: '/about', name: 'about', component: () => import('../views/AboutView.vue'), meta: { title: 'О программе' } },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('../views/NotFoundView.vue'), meta: { title: 'Не найдено' } },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

/** Ссылки 1.0.x вида /#/project/5 продолжают работать после перехода. */
export function legacyHashTarget(hash) {
  const match = /^#\/project\/(\d+)/.exec(hash || '')
  if (match) return `/projects/${match[1]}`
  if ((hash || '').startsWith('#/admin')) return '/admin/users'
  if (hash === '#/' || hash === '#') return '/'
  return null
}

router.beforeEach((to) => {
  // Старые ссылки переводим здесь, а не до создания приложения: история роутера запоминает адрес
  // при создании, и ссылка, открытая без входа, иначе терялась бы после страницы входа
  const legacy = to.path === '/' ? legacyHashTarget(to.hash) : null
  if (legacy) return { path: legacy, replace: true }
  const auth = useAuthStore()
  if (to.meta.public) {
    if (to.name === 'login' && auth.user) return { path: typeof to.query.next === 'string' ? to.query.next : '/' }
    return true
  }
  if (!auth.user) return { name: 'login', query: to.fullPath !== '/' ? { next: to.fullPath } : {} }
  if (to.meta.admin && !auth.isAdmin) return { name: 'projects' }
  return true
})

router.afterEach((to) => {
  if (to.meta.title) document.title = `${to.meta.title} · Пульс Проекта`
})
