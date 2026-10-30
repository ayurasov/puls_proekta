/**
 * Контекстная справка: знак «?» (components/common/HelpLink.vue) открывает раздел руководства
 * о том, что на экране. id — якорь заголовка (slugify из utils/markdown), title — сам заголовок.
 *
 * Тест docs/__tests__/guides.test.js проверяет, что каждый раздел есть в руководстве под этим
 * заголовком и что каждая HelpLink в интерфейсе ссылается на тему отсюда: переименовали раздел
 * в руководстве — тест подскажет, какую ссылку поправить.
 */
export const HELP_TOPICS = {
  projects: { guide: 'user', id: 'проекты', title: 'Проекты' },
  plan: { guide: 'user', id: 'экран-плана', title: 'Экран плана' },
  'item-card': { guide: 'user', id: 'карточка-строки', title: 'Карточка строки' },
  milestones: { guide: 'user', id: 'вехи', title: 'Вехи' },
  groups: { guide: 'user', id: 'группы', title: 'Группы' },
  'plan-settings': { guide: 'user', id: 'настройки-графика', title: 'Настройки графика' },
  'plan-assignees': { guide: 'user', id: 'исполнители-плана', title: 'Исполнители плана' },
  'plan-stats': { guide: 'user', id: 'статистика', title: 'Статистика' },
  'plan-history': { guide: 'user', id: 'история-плана-и-версии', title: 'История плана и версии' },
  'admin-users': { guide: 'admin', id: 'сотрудники', title: 'Сотрудники' },
  'admin-directions': { guide: 'admin', id: 'направления', title: 'Направления' },
  'admin-access-grant': { guide: 'admin', id: 'выдать-доступ', title: 'Выдать доступ' },
  'admin-access-rules': { guide: 'admin', id: 'правила', title: 'Правила' },
  'admin-access-matrix': { guide: 'admin', id: 'кто-что-видит', title: 'Кто что видит' },
  'admin-audit': { guide: 'admin', id: 'реестр-изменений', title: 'Реестр изменений' },
  'admin-trash': { guide: 'admin', id: 'корзина', title: 'Корзина' },
  'admin-ldap': { guide: 'admin', id: 'вход-через-домен', title: 'Вход через домен' },
  'admin-calendar': { guide: 'admin', id: 'производственный-календарь', title: 'Производственный календарь' },
}

export function helpTopic(key) {
  return Object.hasOwn(HELP_TOPICS, key) ? HELP_TOPICS[key] : null
}
