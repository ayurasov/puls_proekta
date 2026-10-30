import { describe, expect, it } from 'vitest'
import {
  EmployeeFileError, avatarBatches, customValueFor, decisionFor, decodeCsv, needsAttention, readEmployeeFile,
  recordKey, visibleChoices, withBulkChoice, withFieldChoice, withIdentity,
} from '../employeeImport'
import { buildZip } from './zipFixture'

const RECORD = { key: 'anna@x.ru', email: 'anna@x.ru', errors: [], warnings: [], unresolved: false }
const POSITION = { field: 'position', kind: 'text', current: 'Инженер', incoming: 'Ведущий инженер', choices: ['current', 'incoming', 'custom'] }
const HEADS = { field: 'heads', kind: 'list', current: ['Компания / Продажи'], incoming: ['Компания / ИТ'], merged: ['Компания / Продажи', 'Компания / ИТ'], choices: ['current', 'incoming', 'merge', 'custom'] }
const ACTIVE = { field: 'isActive', kind: 'bool', current: 'да', incoming: 'нет', choices: ['current', 'incoming', 'custom'] }
const ROLE = { field: 'role', kind: 'role', current: 'Редактор', incoming: 'Менеджер', choices: ['current', 'incoming', 'custom'] }

describe('чтение файла', () => {
  it('CSV из Excel в windows-1251 и UTF-8 с BOM', () => {
    const cp1251 = new Uint8Array([0xc8, 0xec, 0xff]) // «Имя»
    expect(decodeCsv(cp1251)).toBe('Имя')
    expect(decodeCsv(new TextEncoder().encode('\uFEFFemail;ФИО'))).toBe('email;ФИО')
    // «email;ФИО;Подразделение» из Excel (windows-1251) — без потерь
    const excel = new Uint8Array([...new TextEncoder().encode('email;'), 0xd4, 0xc8, 0xce, 0x3b, 0xcf, 0xee, 0xe4, 0xf0, 0xe0, 0xe7, 0xe4, 0xe5, 0xeb, 0xe5, 0xed, 0xe8, 0xe5])
    expect(decodeCsv(excel)).toBe('email;ФИО;Подразделение')
  })

  it('CSV и JSON уходят на сервер текстом', async () => {
    const csv = await readEmployeeFile(new File([new Uint8Array([0xef, 0xbb, 0xbf, ...new TextEncoder().encode('email;ФИО\n')])], 'люди.CSV'))
    expect(csv).toMatchObject({ format: 'csv', content: 'email;ФИО\n', avatars: {}, count: null })
    const json = await readEmployeeFile(new File(['{"format":"podelu.employees"}'], 'employees.json'))
    expect(json.format).toBe('json')
    expect(json.content).toBe('{"format":"podelu.employees"}')
  })

  it('ZIP: на сервер — employees.json и хеши фото, сами фото — отдельно; без email — по логину', async () => {
    const photo = new Uint8Array([1, 2, 3, 4])
    const document = {
      format: 'podelu.employees',
      employees: [{ email: 'Anna@X.ru', avatar: 'avatars/1.png' }, { email: 'b@x.ru' }, { email: '', login: 'Admin', avatar: 'avatars/3.jpg' }],
    }
    const file = new File([buildZip({ 'employees.json': JSON.stringify(document), 'avatars/1.png': photo, 'avatars/3.jpg': photo })], 'выгрузка.zip')
    const result = await readEmployeeFile(file)
    expect(result.format).toBe('json')
    expect(result.count).toBe(3)
    expect(Object.keys(result.avatars)).toEqual(['anna@x.ru', 'login:admin'])
    expect(result.avatars['anna@x.ru']).toBe('9f64a747e1b97f131fabb6b447296c9b6f0201e79fb3c5356e6c77e89b6a806a')
    expect(result.photos.get('anna@x.ru').size).toBe(4)
  })

  it('чужой архив и неизвестный формат — понятные ошибки', async () => {
    await expect(readEmployeeFile(new File([buildZip({ 'a.txt': 'x' })], 'a.zip'))).rejects.toThrow('нет employees.json')
    await expect(readEmployeeFile(new File([buildZip({ 'employees.json': '{' })], 'a.zip'))).rejects.toThrow('повреждён')
    await expect(readEmployeeFile(new File(['x'], 'a.zip'))).rejects.toThrow('не является ZIP-архивом')
    await expect(readEmployeeFile(new File(['x'], 'a.xlsx'))).rejects.toBeInstanceOf(EmployeeFileError)
    await expect(readEmployeeFile(null)).rejects.toThrow('Файл не выбран')
  })

  it('ключ записи — email или логин', () => {
    expect(recordKey({ email: ' Anna@X.ru ', login: 'anna' })).toBe('anna@x.ru')
    expect(recordKey({ email: '', login: 'Admin' })).toBe('login:admin')
    expect(recordKey({})).toBe('')
  })
})

describe('решения по коллизиям', () => {
  it('по умолчанию — значение системы; выбор и своё значение сохраняются по полю', () => {
    expect(decisionFor({}, RECORD, POSITION)).toBe('current')
    let decisions = withFieldChoice({}, RECORD.key, 'position', 'incoming')
    expect(decisionFor(decisions, RECORD, POSITION)).toBe('incoming')
    expect(customValueFor(decisions, RECORD, POSITION)).toBe('Ведущий инженер')
    decisions = withFieldChoice(decisions, RECORD.key, 'position', 'custom', 'Главный инженер')
    expect(customValueFor(decisions, RECORD, POSITION)).toBe('Главный инженер')
    decisions = withIdentity(decisions, RECORD.key, 'same')
    expect(decisions[RECORD.key]).toEqual({ fields: { position: { choice: 'custom', value: 'Главный инженер' } }, identity: 'same' })
  })

  it('руководство редактируется строкой через «|», роль — кодом из списка', () => {
    expect(customValueFor({}, RECORD, HEADS)).toBe('Компания / ИТ')
    expect(customValueFor({}, RECORD, { ...HEADS, incoming: '' })).toBe('Компания / Продажи')
    expect(customValueFor({}, RECORD, ROLE)).toBe('manager')
  })

  it('массовый выбор — только там, где вариант доступен', () => {
    const plan = { records: [{ ...RECORD, conflicts: [POSITION, { ...HEADS, choices: ['current', 'merge'] }] }] }
    const decisions = withBulkChoice({}, plan, 'incoming')
    expect(decisions[RECORD.key].fields).toEqual({ position: { choice: 'incoming' } })
    expect(withBulkChoice(decisions, plan, 'current')[RECORD.key].fields).toEqual({
      position: { choice: 'current' }, heads: { choice: 'current' },
    })
  })

  it('для да/нет и фото «своё» не предлагается', () => {
    expect(visibleChoices(ACTIVE)).toEqual(['current', 'incoming'])
    expect(visibleChoices({ ...ACTIVE, kind: 'avatar', choices: ['current', 'incoming'] })).toEqual(['current', 'incoming'])
    expect(visibleChoices(HEADS)).toEqual(HEADS.choices)
  })

  it('требуют внимания — ошибки, коллизии, предупреждения и нерешённые', () => {
    expect(needsAttention({ ...RECORD, conflicts: [] })).toBe(false)
    expect(needsAttention({ ...RECORD, conflicts: [POSITION] })).toBe(true)
    expect(needsAttention({ ...RECORD, conflicts: [], unresolved: true })).toBe(true)
    expect(needsAttention({ ...RECORD, conflicts: [], warnings: ['роль не распознана'] })).toBe(true)
  })
})

describe('фото пачками', () => {
  it('режет по размеру и числу, пропуская тех, кого нет', () => {
    const photos = new Map([['a', new Blob([new Uint8Array(6)])], ['b', new Blob([new Uint8Array(6)])], ['c', new Blob([new Uint8Array(1)])]])
    const ids = { a: 1, b: 2, c: 3 }
    const batches = avatarBatches(['a', 'b', 'c', 'missing'], ids, photos, { maxBytes: 10, maxFiles: 5 })
    expect(batches.map((batch) => batch.map((item) => item.userId))).toEqual([[1], [2, 3]])
    expect(avatarBatches(['a', 'b', 'c'], ids, photos, { maxBytes: 100, maxFiles: 2 }).map((b) => b.length)).toEqual([2, 1])
    expect(avatarBatches([], ids, photos)).toEqual([])
  })
})
