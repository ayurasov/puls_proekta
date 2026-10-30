import { describe, expect, it } from 'vitest'
import { BAR_OPACITY, blendWithWhite, contrastRatio, PALETTE, readableText, STATUSES, textColorOnWhite } from '../colors'

describe('контраст подписей (WCAG AA, 4,5:1)', () => {
  it('текст на полосе любого цвета палитры — и на бледных полосах выполненных и заблокированных задач', () => {
    for (const { color } of PALETTE) {
      for (const opacity of [1, BAR_OPACITY.done, BAR_OPACITY.blocked]) {
        const fill = opacity < 1 ? blendWithWhite(color, opacity) : color
        expect(contrastRatio(fill, readableText(color, opacity)), `${color} × ${opacity}`).toBeGreaterThanOrEqual(4.5)
      }
    }
  })
  it('на светлых полосах — тёмный текст, на тёмных — белый', () => {
    expect(readableText('#84cc16')).not.toBe('#ffffff')
    expect(readableText('#14b8a6')).not.toBe('#ffffff')
    expect(readableText('#1e293b')).toBe('#ffffff')
  })
  it('статусы в строке — тёмными оттенками', () => {
    for (const s of STATUSES) expect(contrastRatio(s.text, '#ffffff'), s.key).toBeGreaterThanOrEqual(4.5)
  })
  it('цветные подписи пометок и вех затемняются до читаемого', () => {
    for (const { color } of PALETTE) expect(contrastRatio(textColorOnWhite(color), '#ffffff'), color).toBeGreaterThanOrEqual(4.5)
    expect(textColorOnWhite('#1e293b')).toBe('#1e293b')
  })
})
