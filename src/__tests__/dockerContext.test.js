import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'

// Образ клиента собирается из контекста без каталога docs (.dockerignore): всё, что клиент берёт из docs при
// сборке (?raw, ?url, import.meta.glob), должно быть разрешено строкой «!путь». Без этой проверки сборка
// падала только в Docker (так случилось с окном «Что нового»).
const root = resolve(__dirname, '../..')

function sources(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return name === '__tests__' ? [] : sources(path)
    return /\.(vue|js)$/.test(name) ? [path] : []
  })
}

function allowed(path, rules) {
  return rules.some((rule) => {
    const pattern = rule.replace(/\/$/, '')
    if (pattern.includes('*')) {
      const re = new RegExp(`^${pattern.split('*').map((p) => p.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('[^/]*')}`)
      return re.test(path)
    }
    return path === pattern || path.startsWith(`${pattern}/`)
  })
}

describe('контекст сборки Docker', () => {
  it('файлы docs, которые импортирует клиент, попадают в образ', () => {
    const rules = readFileSync(join(root, '.dockerignore'), 'utf8').split('\n')
      .map((line) => line.trim()).filter((line) => line.startsWith('!')).map((line) => line.slice(1))
    const used = new Set()
    for (const file of sources(join(root, 'src'))) {
      const text = readFileSync(file, 'utf8')
      for (const match of text.matchAll(/['"]((?:\.\.\/)+docs\/[^'"?*{]+)/g)) {
        used.add(relative(root, resolve(file, '..', match[1])).split('\\').join('/'))
      }
    }
    expect(used.size).toBeGreaterThan(0)
    for (const path of used) expect(allowed(path, rules), `${path} не попадает в образ — добавьте «!${path}» в .dockerignore`).toBe(true)
  })
})
