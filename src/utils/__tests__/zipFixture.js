import { deflateRawSync } from 'node:zlib'

/** Собирает ZIP-архив в памяти: { имя: строка | Uint8Array }, deflate или без сжатия. */
export function buildZip(files, { store = false } = {}) {
  const encoder = new TextEncoder()
  const locals = []
  const centrals = []
  let offset = 0
  for (const [name, content] of Object.entries(files)) {
    const nameBytes = encoder.encode(name)
    const raw = typeof content === 'string' ? encoder.encode(content) : content
    const data = store ? raw : new Uint8Array(deflateRawSync(raw))
    const method = store ? 0 : 8
    const local = new DataView(new ArrayBuffer(30))
    local.setUint32(0, 0x04034b50, true)
    local.setUint16(8, method, true)
    // Как у потоковых архиваторов: размеры только в центральном каталоге
    locals.push(new Uint8Array(local.buffer), nameBytes, data)
    const central = new DataView(new ArrayBuffer(46))
    central.setUint32(0, 0x02014b50, true)
    central.setUint16(10, method, true)
    central.setUint32(20, data.length, true)
    central.setUint32(24, raw.length, true)
    central.setUint16(28, nameBytes.length, true)
    central.setUint32(42, offset, true)
    centrals.push(new Uint8Array(central.buffer), nameBytes)
    local.setUint16(26, nameBytes.length, true)
    offset += 30 + nameBytes.length + data.length
  }
  const centralSize = centrals.reduce((sum, part) => sum + part.length, 0)
  const end = new DataView(new ArrayBuffer(22))
  end.setUint32(0, 0x06054b50, true)
  end.setUint16(8, Object.keys(files).length, true)
  end.setUint16(10, Object.keys(files).length, true)
  end.setUint32(12, centralSize, true)
  end.setUint32(16, offset, true)
  const parts = [...locals, ...centrals, new Uint8Array(end.buffer)]
  const out = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0))
  let position = 0
  for (const part of parts) {
    out.set(part, position)
    position += part.length
  }
  return out.buffer
}
