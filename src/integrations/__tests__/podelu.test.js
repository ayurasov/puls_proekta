import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
vi.mock('../../api/client', () => ({ api: { get: (...args) => get(...args) } }))

const {
  loadPodelu, podeluBase, podeluOrgUrl, podeluPersonLink, podeluPersonUrl, podeluUnitLink, podeluUnitUrl, resetPodelu,
} = await import('../podelu')

const BASE = 'https://podelu.company.ru'
const UNIT = '26b70020-76d0-45a2-9150-f070f6cf1a62'

describe('ссылки на «По Делу»', () => {
  it('строит ссылки на оргсхему, подразделение и сотрудника', () => {
    expect(podeluOrgUrl(BASE)).toBe(`${BASE}/org`)
    expect(podeluUnitUrl(`${BASE}/`, UNIT)).toBe(`${BASE}/org?unit=${UNIT}`)
    expect(podeluPersonUrl(BASE, 'a b&c')).toBe(`${BASE}/org?user=a%20b%26c`)
    expect(podeluUnitUrl('https://host.ru/podelu', UNIT)).toBe(`https://host.ru/podelu/org?unit=${UNIT}`)
  })

  it('без адреса или идентификатора ссылки нет', () => {
    expect(podeluUnitUrl('', UNIT)).toBeNull()
    expect(podeluUnitUrl(BASE, null)).toBeNull()
    expect(podeluPersonUrl(BASE, '')).toBeNull()
    expect(podeluOrgUrl(null)).toBeNull()
  })

  it('принимает только http(s)-адрес', () => {
    expect(podeluBase('javascript:alert(1)')).toBe('')
    expect(podeluBase('ftp://podelu')).toBe('')
    expect(podeluBase('https://podelu.ru/?x=1')).toBe('')
    expect(podeluBase(' http://10.0.0.5:8080/ ')).toBe('http://10.0.0.5:8080')
  })

  it('ссылки по объектам «Пульса» — только при подключении и с podeluId', () => {
    const status = { connected: true, baseUrl: BASE }
    expect(podeluUnitLink(status, { id: 'x', podeluId: UNIT })).toBe(`${BASE}/org?unit=${UNIT}`)
    expect(podeluUnitLink(status, { id: 'x', podeluId: null })).toBeNull()
    expect(podeluPersonLink(status, { id: 5, podeluId: 'p-1' })).toBe(`${BASE}/org?user=p-1`)
    expect(podeluPersonLink({ connected: false, baseUrl: null }, { podeluId: 'p-1' })).toBeNull()
    expect(podeluPersonLink(null, { podeluId: 'p-1' })).toBeNull()
  })
})

describe('loadPodelu', () => {
  beforeEach(() => {
    resetPodelu()
    get.mockReset()
  })

  it('один запрос на страницу, force перечитывает', async () => {
    get.mockResolvedValue({ connected: true, baseUrl: `${BASE}/` })
    expect(await loadPodelu()).toEqual({ connected: true, baseUrl: BASE })
    await loadPodelu()
    expect(get).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenCalledWith('/podelu')
    get.mockResolvedValue({ connected: false, baseUrl: null })
    expect(await loadPodelu({ force: true })).toEqual({ connected: false, baseUrl: null })
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('ошибка не запоминается', async () => {
    get.mockRejectedValueOnce(new Error('нет сети'))
    expect(await loadPodelu()).toEqual({ connected: false, baseUrl: null })
    get.mockResolvedValue({ connected: true, baseUrl: BASE })
    expect(await loadPodelu()).toEqual({ connected: true, baseUrl: BASE })
  })
})
