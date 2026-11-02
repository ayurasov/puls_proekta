import { describe, expect, it } from 'vitest'
import { barProgress, progressPath, progressStyle, PROGRESS_SHADE, PROGRESS_TINT, rowModel } from '../chartModel'
import { blendWithWhite, readableText, shadeColor } from '../domain/colors'

/** 0.6.1: вид прогресса на полосах — заливка (как в 1.0, по умолчанию), полоска, светлый остаток, с процентом. */
const BLUE = '#3b82f6'
const YELLOW = '#eab308'
const bar = (display, progress = 60, fill = BLUE, status = 'inwork') => barProgress({ x: 100, y: 10, w: 200, h: 24, fill, status, progress, display })

describe('прогресс на полосах', () => {
  it('по умолчанию — заливка; выключенный прогресс — ничего', () => {
    expect(progressStyle({ showProgress: true })).toBe('fill')
    expect(progressStyle({ showProgress: true, progressStyle: 'нечто' })).toBe('fill')
    expect(progressStyle({ showProgress: false, progressStyle: 'tint' })).toBeNull()
    const off = bar({ showProgress: false, progressStyle: 'fill' })
    expect(off).toMatchObject({ style: null, pct: 0, base: BLUE, done: null, strip: null, prefix: '' })
  })

  it('заливка: выполненная часть во всю высоту, левые углы скруглены, правый край прямой', () => {
    const p = bar({ showProgress: true, progressStyle: 'fill' })
    expect(p.base).toBe(BLUE)
    expect(p.done.kind).toBe('shade')
    expect(p.done.fill).toBe(`rgba(15, 23, 42, ${PROGRESS_SHADE})`)
    expect(p.done.d).toBe(progressPath(100, 10, 200, 24, 60))
    expect(p.done.d.startsWith('M106 10H220V34H106')).toBe(true) // 60 % от 200 px — до x = 220, на всю высоту 24
    expect(p.strip).toBeNull()
    expect(p.prefix).toBe('')
  })

  it('полоска: тонкая полоса по нижнему краю, как в 0.4–0.6', () => {
    const p = bar({ showProgress: true, progressStyle: 'strip' })
    expect(p.done).toBeNull()
    expect(p.strip).toEqual({ x: 102, y: 28, w: 196 * 0.6, h: 4 })
  })

  it('светлый остаток: полоса светлая, сделанное — цветом задачи; подпись под цвет того, что под центром', () => {
    const half = bar({ showProgress: true, progressStyle: 'tint' }, 60)
    expect(half.base).toBe(blendWithWhite(BLUE, PROGRESS_TINT))
    expect(half.done).toMatchObject({ fill: BLUE, kind: 'tint' })
    expect(half.text).toBe(readableText(BLUE))
    const start = bar({ showProgress: true, progressStyle: 'tint' }, 20)
    expect(start.text).toBe(readableText(blendWithWhite(BLUE, PROGRESS_TINT)))
    expect(start.text).toBe('#000000') // на светлом тоне — тёмная подпись
    const none = bar({ showProgress: true, progressStyle: 'tint' }, 0)
    expect(none.done).toBeNull()
    expect(none.base).toBe(blendWithWhite(BLUE, PROGRESS_TINT)) // не начатая задача — вся светлая
  })

  it('с процентом: заливка и «60% · » в начале подписи; без прогресса — без процента', () => {
    const p = bar({ showProgress: true, progressStyle: 'percent' })
    expect(p.done.kind).toBe('shade')
    expect(p.prefix).toBe('60% · ')
    expect(bar({ showProgress: true, progressStyle: 'percent' }, 0).prefix).toBe('')
  })

  it('подпись на затемнённой части подбирается под затемнённый цвет', () => {
    const p = bar({ showProgress: true, progressStyle: 'fill' }, 80, YELLOW)
    expect(p.text).toBe(readableText(shadeColor(YELLOW, PROGRESS_SHADE)))
  })

  it('100 % — контур всей полосы со скруглением с обеих сторон; прогресс ограничен 0–100', () => {
    expect(progressPath(0, 0, 100, 24, 100)).toContain('A6 6 0 0 1 100 6')
    expect(bar({ showProgress: true, progressStyle: 'fill' }, 140).pct).toBe(100)
    expect(bar({ showProgress: true, progressStyle: 'fill' }, -5).done).toBeNull()
    expect(progressPath(0, 0, 100, 24, 0)).toBe('')
  })

  it('rowModel: полоса получает цвет основы, контур выполненного и подпись с процентом', () => {
    const item = { key: 't1', type: 'task', name: 'Задача', start: '2026-11-02', end: '2026-11-11', color: 'blue', status: 'inwork', progress: 50 }
    const ctx = {
      rowHeight: 44, range: { start: '2026-11-01', end: '2026-12-31' }, px: 20, notes: [], index: null,
      display: { showProgress: true, progressStyle: 'percent', showNotes: false, showTaskNamesOnBars: false },
    }
    const model = rowModel({ item }, 0, ctx)
    expect(model.bar.color).toBe(BLUE)
    expect(model.bar.progressStyle).toBe('percent')
    expect(model.bar.done.d).toBe(progressPath(model.bar.x, model.bar.y, model.bar.w, model.bar.h, 50))
    expect(model.bar.label.startsWith('50% · 02.11–11.11')).toBe(true)
    const tint = rowModel({ item }, 0, { ...ctx, display: { ...ctx.display, progressStyle: 'tint' } })
    expect(tint.bar.fill).toBe(blendWithWhite(BLUE, PROGRESS_TINT))
    expect(tint.bar.label.startsWith('02.11')).toBe(true)
  })
})
