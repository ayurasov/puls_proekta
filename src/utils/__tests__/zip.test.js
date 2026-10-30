import { describe, expect, it } from 'vitest'
import { readZip, readZipText, sha256Hex, ZipFormatError } from '../zip'
import { buildZip } from './zipFixture'

describe('readZip', () => {
  it('читает сжатые и несжатые файлы по центральному каталогу', async () => {
    const photo = new Uint8Array([137, 80, 78, 71, 1, 2, 3])
    for (const store of [false, true]) {
      const entries = readZip(buildZip({ 'employees.json': '{"ok":"да"}', 'avatars/a.png': photo }, { store }))
      expect([...entries.keys()]).toEqual(['employees.json', 'avatars/a.png'])
      expect(await readZipText(entries, 'employees.json')).toBe('{"ok":"да"}')
      expect([...(await entries.get('avatars/a.png').read())]).toEqual([...photo])
    }
  })

  it('архив с комментарием и папками: каталог ищется с конца, папки пропускаются', async () => {
    const buffer = buildZip({ 'avatars/': '', 'employees.json': '[]' }, { store: true })
    const withComment = new Uint8Array(buffer.byteLength + 5)
    withComment.set(new Uint8Array(buffer))
    withComment.set(new TextEncoder().encode('note!'), buffer.byteLength)
    new DataView(withComment.buffer).setUint16(buffer.byteLength - 2, 5, true)
    const entries = readZip(withComment.buffer)
    expect([...entries.keys()]).toEqual(['employees.json'])
    expect(await readZipText(entries, 'employees.json')).toBe('[]')
  })

  it('неподдерживаемое сжатие и повреждённый архив — понятные ошибки', async () => {
    const buffer = buildZip({ 'a.txt': 'x' }, { store: true })
    const view = new DataView(buffer)
    const central = view.getUint32(buffer.byteLength - 6, true)
    view.setUint16(central + 10, 12, true) // bzip2
    await expect(readZip(buffer).get('a.txt').read()).rejects.toThrow('неподдерживаемым способом')
    view.setUint32(central, 0, true)
    expect(() => readZip(buffer)).toThrow('Повреждён каталог')
  })

  it('нет файла — null; не архив — понятная ошибка', async () => {
    expect(await readZipText(readZip(buildZip({ 'a.txt': 'x' })), 'employees.json')).toBeNull()
    expect(() => readZip(new TextEncoder().encode('email;ФИО\n'.repeat(5)).buffer)).toThrow(ZipFormatError)
  })
})

describe('sha256Hex', () => {
  it('совпадает с эталоном', async () => {
    expect(await sha256Hex(new TextEncoder().encode('abc')))
      .toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
  })
})
