import { describe, expect, it } from 'vitest'
import { effectiveServerUrl, normalizeUrl, sameAddresses, suggestsServerUrl } from '../serverAddress'

/** 0.5.1: адрес для запросов сервера — когда «По Делу» видна серверу «Пульса» по другому адресу, чем сотрудникам. */
describe('адрес для запросов сервера', () => {
  it('нормализуется, а совпадающий с адресом «По Делу» не хранится отдельно', () => {
    expect(normalizeUrl('  http://host.docker.internal:39665/ ')).toBe('http://host.docker.internal:39665')
    expect(effectiveServerUrl('https://podelu.company.ru', 'http://host.docker.internal:39665/')).toBe('http://host.docker.internal:39665')
    expect(effectiveServerUrl('https://podelu.company.ru', 'https://podelu.company.ru/')).toBe('')
    expect(effectiveServerUrl('https://podelu.company.ru', '   ')).toBe('')
  })

  it('сохранённый ключ или пароль уходит без повторного ввода, только пока не менялись оба адреса', () => {
    const saved = { baseUrl: 'https://podelu.company.ru', serverUrl: 'http://host.docker.internal:39665' }
    expect(sameAddresses({ baseUrl: 'https://podelu.company.ru/', serverUrl: 'http://host.docker.internal:39665' }, saved)).toBe(true)
    expect(sameAddresses({ baseUrl: 'https://podelu.company.ru', serverUrl: '' }, saved)).toBe(false)
    expect(sameAddresses({ baseUrl: 'https://podelu.company.ru', serverUrl: 'http://192.0.2.2:39665' }, saved)).toBe(false)
    expect(sameAddresses({ baseUrl: 'https://podelu.company.ru', serverUrl: 'https://podelu.company.ru' },
      { baseUrl: 'https://podelu.company.ru', serverUrl: '' })).toBe(true)
    expect(sameAddresses({ baseUrl: 'https://podelu.company.ru', serverUrl: '' }, null)).toBe(false)
  })

  it('ошибка про localhost или имя машины раскрывает поле адреса для запросов сервера', () => {
    expect(suggestsServerUrl('«Адрес «По Делу»» — localhost: для сервера «Пульса» в контейнере это он сам. Укажите … http://host.docker.internal:<порт «По Делу»>')).toBe(true)
    expect(suggestsServerUrl('Неверный ключ «По Делу»')).toBe(false)
    expect(suggestsServerUrl(undefined)).toBe(false)
  })
})
