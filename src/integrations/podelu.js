import { api } from '../api/client'

/**
 * «По Делу» (ADR-0009): адрес подключённой «По Делу» и ссылки на её оргсхему.
 *
 * Адрес — из GET /api/podelu ({ connected, baseUrl }, без учётной записи подключения; доступно всем сотрудникам).
 * Идентификаторы «По Делу» — поле podeluId подразделения и сотрудника в ответах /api/org/tree и
 * /api/org/users/<id>: подразделения, загруженные из «По Делу», хранят её UUID, сотрудники — свой id в «По Делу».
 *
 *   const podelu = await loadPodelu()
 *   podeluUnitLink(podelu, department)   // https://podelu.company.ru/org?unit=<uuid> или null
 *   podeluPersonLink(podelu, person)     // https://podelu.company.ru/org?user=<id> или null
 */

const DISCONNECTED = Object.freeze({ connected: false, baseUrl: null })
let pending = null

/** Подключение «По Делу» (один запрос на страницу; force — перечитать, например после смены подключения). */
export function loadPodelu({ force = false } = {}) {
  if (!pending || force) {
    pending = api.get('/podelu').then(
      (data) => ({ connected: Boolean(data?.connected && podeluBase(data.baseUrl)), baseUrl: podeluBase(data?.baseUrl) || null }),
      () => {
        pending = null // не запоминать ошибку: следующий вызов спросит сервер снова
        return DISCONNECTED
      },
    )
  }
  return pending
}

/** Забыть загруженное подключение (администратор сохранил или отключил «По Делу»). */
export function resetPodelu() {
  pending = null
}

/** Адрес «По Делу» без «/» в конце; только http(s) — иначе пустая строка. */
export function podeluBase(baseUrl) {
  const url = String(baseUrl || '').trim().replace(/\/+$/, '')
  return /^https?:\/\/[^\s/?#]+(\/[^\s?#]*)?$/i.test(url) ? url : ''
}

/** Оргсхема «По Делу». */
export function podeluOrgUrl(baseUrl) {
  const base = podeluBase(baseUrl)
  return base ? `${base}/org` : null
}

/** Подразделение в оргсхеме «По Делу»: ${base}/org?unit=<UUID подразделения в «По Делу»>. */
export function podeluUnitUrl(baseUrl, unitId) {
  const base = podeluBase(baseUrl)
  return base && unitId ? `${base}/org?unit=${encodeURIComponent(unitId)}` : null
}

/** Карточка сотрудника в оргсхеме «По Делу»: ${base}/org?user=<id сотрудника в «По Делу»>. */
export function podeluPersonUrl(baseUrl, podeluUserId) {
  const base = podeluBase(baseUrl)
  return base && podeluUserId ? `${base}/org?user=${encodeURIComponent(podeluUserId)}` : null
}

/** Ссылка на подразделение «Пульса» в «По Делу» — null, если «По Делу» не подключена или подразделение не оттуда. */
export function podeluUnitLink(status, department) {
  return status?.connected ? podeluUnitUrl(status.baseUrl, department?.podeluId) : null
}

/** Ссылка на сотрудника «Пульса» в «По Делу» — null, если «По Делу» не подключена или сотрудник не связан с ней. */
export function podeluPersonLink(status, person) {
  return status?.connected ? podeluPersonUrl(status.baseUrl, person?.podeluId) : null
}
