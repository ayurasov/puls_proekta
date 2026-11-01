/**
 * «Адрес для запросов сервера» в настройках «По Делу» (0.5.1).
 *
 * Сервер «Пульса» обращается к «По Делу» сам — не из браузера сотрудника. Когда оба сервиса в Docker на одной машине,
 * адрес, который открывают сотрудники (https://podelu.company.ru или http://localhost:39665), изнутри контейнера
 * «Пульса» не виден: localhost там — сам контейнер, а имя из /etc/hosts машины не разрешается. Тогда запросы сервера
 * идут на отдельный адрес (IP машины или http://host.docker.internal:<порт «По Делу»>), а ссылки для сотрудников — по
 * «Адресу «По Делу»». Пустой адрес для запросов — запросы идут туда же, куда ведут ссылки.
 */

export const SERVER_URL_PLACEHOLDER = 'http://host.docker.internal:39665'

export function normalizeUrl(url) {
  return (url || '').trim().replace(/\/+$/, '')
}

/** Адрес для запросов сервера, как его сохранит сервер: совпадающий с «Адресом «По Делу»» не хранится отдельно. */
export function effectiveServerUrl(baseUrl, serverUrl) {
  const server = normalizeUrl(serverUrl)
  return server && server !== normalizeUrl(baseUrl) ? server : ''
}

/** Совпадают ли адреса в форме с сохранёнными: только тогда сохранённые пароль или ключ уходят без повторного ввода. */
export function sameAddresses(form, saved) {
  if (!saved) return false
  return normalizeUrl(form.baseUrl) === (saved.baseUrl || '')
    && effectiveServerUrl(form.baseUrl, form.serverUrl) === (saved.serverUrl || '')
}

/** Подсказывает ли ошибка проверки связи указать адрес для запросов сервера (тогда поле раскрывается само). */
export function suggestsServerUrl(message) {
  return /host\.docker\.internal|адрес для запросов сервера/i.test(message || '')
}
