/**
 * Полномочия без роли администратора (ADR-0013) — тот же список, что backend/app/capabilities.py.
 * Администратор выдаёт их сотруднику точечно; у самого администратора действуют все. Назначать роль
 * «Администратор», выдавать полномочия, выпускать ключи доступа и настраивать вход через домен может только
 * администратор.
 */
export const CAPABILITIES = [
  {
    key: 'system.health', group: 'Система', label: 'Смотрит состояние системы',
    hint: 'Раздел «Состояние системы»: сервер, база данных, резервные копии, ошибки, подключения',
  },
  {
    key: 'backups.manage', group: 'Система', label: 'Ведёт резервное копирование',
    hint: 'Расписание и хранение резервных копий, копия по запросу',
  },
  {
    key: 'modules.manage', group: 'Система', label: 'Настраивает модули',
    hint: 'Jira и «По Делу»: подключение, проверка, поля сроков, ссылки из задач',
  },
  { key: 'calendar.manage', group: 'Система', label: 'Ведёт производственный календарь', hint: 'Праздники, переносы и рабочие субботы' },
  { key: 'audit.view', group: 'Система', label: 'Смотрит реестр изменений', hint: 'Кто, что и когда менял; выгрузка в CSV' },
  {
    key: 'users.manage', group: 'Люди и доступы', label: 'Ведёт учётные записи',
    hint: 'Сотрудники: создание, роли (кроме администратора), пароли, фото, отключение; импорт и экспорт сотрудников',
  },
  {
    key: 'org.manage', group: 'Люди и доступы', label: 'Ведёт структуру компании',
    hint: 'Подразделения и руководители, перевод сотрудников, импорт и экспорт структуры',
  },
  {
    key: 'access.manage', group: 'Люди и доступы', label: 'Выдаёт доступы к проектам',
    hint: 'Правила доступа сотрудников и подразделений к проектам и направлениям; направления',
  },
  {
    key: 'trash.manage', group: 'Проекты', label: 'Управляет корзиной',
    hint: 'Удаление проектов; корзина всех проектов: вернуть или удалить навсегда',
  },
]
export const CAPABILITY_KEYS = CAPABILITIES.map((c) => c.key)
export const CAPABILITY_LABELS = Object.fromEntries(CAPABILITIES.map((c) => [c.key, c.label]))

/** Группы для списка у администратора: [{ name, items }] в порядке списка. */
export function capabilityGroups() {
  const groups = []
  for (const cap of CAPABILITIES) {
    let group = groups.find((g) => g.name === cap.group)
    if (!group) groups.push((group = { name: cap.group, items: [] }))
    group.items.push(cap)
  }
  return groups
}

/** Действующие полномочия: у администратора — все, у отключённого — никаких. */
export function effectiveCapabilities(user) {
  if (!user || user.isActive === false) return []
  if (user.role === 'admin') return [...CAPABILITY_KEYS]
  const own = new Set(user.capabilities || [])
  return CAPABILITY_KEYS.filter((key) => own.has(key))
}

/** Есть ли хотя бы одно из полномочий (ключ 'admin' — только у администратора). */
export function hasCapability(user, ...capabilities) {
  if (!user || user.isActive === false) return false
  if (user.role === 'admin') return true
  const own = new Set(user.capabilities || [])
  return capabilities.flat().some((c) => c !== 'admin' && own.has(c))
}

/** Может ли сотрудник с «Ведёт учётные записи» менять учётную запись target (как can_manage_account на сервере). */
export function canManageAccount(actor, target) {
  if (!actor || !target) return false
  if (actor.role === 'admin') return true
  if (!hasCapability(actor, 'users.manage') || target.role === 'admin') return false
  const own = new Set(actor.capabilities || [])
  return (target.capabilities || []).every((c) => own.has(c))
}

/**
 * Разделы администрирования: путь, полномочие (или список — любое из них), подпись и значок. Первый доступный
 * раздел открывается по /admin.
 */
export const ADMIN_SECTIONS = [
  { to: '/admin/health', capability: 'system.health', icon: 'pulse', label: 'Состояние системы' },
  { to: '/admin/backups', capability: ['backups.manage', 'system.health'], icon: 'archive', label: 'Резервные копии' },
  { to: '/admin/users', capability: 'users.manage', icon: 'users', label: 'Сотрудники' },
  { to: '/admin/structure', capability: 'org.manage', icon: 'hierarchy', label: 'Структура' },
  { to: '/admin/org-transfer', capability: ['org.manage', 'users.manage'], icon: 'upload', label: 'Импорт и экспорт' },
  { to: '/admin/directions', capability: 'access.manage', icon: 'folder', label: 'Направления' },
  { to: '/admin/access', capability: 'access.manage', icon: 'shield', label: 'Доступы' },
  { to: '/admin/audit', capability: 'audit.view', icon: 'history', label: 'Реестр изменений' },
  { to: '/admin/trash', capability: 'trash.manage', icon: 'trash', label: 'Корзина' },
  { to: '/admin/settings', capability: 'calendar.manage', icon: 'calendar', label: 'Календарь' },
]
export const INTEGRATION_SECTIONS = [
  { to: '/admin/integrations/jira', capability: 'modules.manage', icon: 'jira', label: 'Jira' },
  { to: '/admin/integrations/podelu', capability: 'modules.manage', icon: 'podelu', label: '«По Делу»' },
  { to: '/admin/integrations/ldap', capability: 'admin', icon: 'shield', label: 'Вход через домен' },
  { to: '/admin/integrations/keys', capability: 'admin', icon: 'key', label: 'Ключи доступа' },
]

export function allowedSections(user, sections) {
  return sections.filter((s) => hasCapability(user, s.capability))
}

/** Есть ли у сотрудника хоть один раздел администрирования. */
export function hasAdminArea(user) {
  return effectiveCapabilities(user).length > 0
}
