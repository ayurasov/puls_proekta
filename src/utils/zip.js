/**
 * Чтение ZIP-архива в браузере — тот же код, что в todo-planner «По Делу» (src/utils/zip.js):
 * выгрузка сотрудников с фото может быть больше предела запроса к серверу, поэтому архив
 * разбирается здесь, а на сервер уходят только employees.json и фото небольшими пачками.
 *
 * Поддерживаются способы хранения 0 (без сжатия) и 8 (deflate — через DecompressionStream).
 * Читается центральный каталог в конце архива: размеры и смещения берутся оттуда, а не из
 * локальных заголовков, где они могут быть нулями.
 */
const EOCD = 0x06054b50
const CENTRAL = 0x02014b50
const LOCAL = 0x04034b50

export class ZipFormatError extends Error {}

function findEndOfCentralDirectory(view) {
  // Запись конца каталога — последние 22 байта плюс комментарий до 64 КБ
  const stop = Math.max(0, view.byteLength - 22 - 0xffff)
  for (let offset = view.byteLength - 22; offset >= stop; offset--) {
    if (view.getUint32(offset, true) === EOCD) return offset
  }
  throw new ZipFormatError('Файл не является ZIP-архивом')
}

async function inflate(bytes) {
  if (typeof DecompressionStream === 'undefined') throw new ZipFormatError('Браузер не умеет распаковывать ZIP — обновите его')
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate-raw'))
  return new Uint8Array(await new Response(stream).arrayBuffer())
}

/**
 * ArrayBuffer → Map(имя файла → { size, read(): Promise<Uint8Array> }). Папки пропускаются.
 */
export function readZip(buffer) {
  const view = new DataView(buffer)
  const end = findEndOfCentralDirectory(view)
  const count = view.getUint16(end + 10, true)
  let offset = view.getUint32(end + 16, true)
  const decoder = new TextDecoder()
  const entries = new Map()
  for (let index = 0; index < count; index++) {
    if (view.getUint32(offset, true) !== CENTRAL) throw new ZipFormatError('Повреждён каталог ZIP-архива')
    const method = view.getUint16(offset + 10, true)
    const compressedSize = view.getUint32(offset + 20, true)
    const size = view.getUint32(offset + 24, true)
    const nameLength = view.getUint16(offset + 28, true)
    const extraLength = view.getUint16(offset + 30, true)
    const commentLength = view.getUint16(offset + 32, true)
    const localOffset = view.getUint32(offset + 42, true)
    const name = decoder.decode(new Uint8Array(buffer, offset + 46, nameLength))
    offset += 46 + nameLength + extraLength + commentLength
    if (name.endsWith('/')) continue
    entries.set(name, {
      size,
      async read() {
        if (view.getUint32(localOffset, true) !== LOCAL) throw new ZipFormatError(`Повреждён файл ${name} в архиве`)
        const start = localOffset + 30 + view.getUint16(localOffset + 26, true) + view.getUint16(localOffset + 28, true)
        const bytes = new Uint8Array(buffer, start, compressedSize)
        if (method === 0) return bytes.slice()
        if (method === 8) return inflate(bytes)
        throw new ZipFormatError(`Файл ${name} сжат неподдерживаемым способом`)
      },
    })
  }
  return entries
}

export async function readZipText(entries, name) {
  const entry = entries.get(name)
  if (!entry) return null
  return new TextDecoder().decode(await entry.read())
}

/** SHA-256 в шестнадцатеричном виде — сервер сравнивает фото из архива с хранимыми. */
export async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('')
}
