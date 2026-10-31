import { describe, expect, it } from 'vitest'
import { apcaContrast, BAR_OPACITY, blendWithWhite, contrastRatio, PALETTE, readableText, STATUSES, textColorOnWhite } from '../colors'

describe('контраст подписей', () => {
  it('текст на полосе любого цвета палитры — и на бледных полосах выполненных и заблокированных задач (APCA)', () => {
    for (const { color } of PALETTE) {
      for (const opacity of [1, BAR_OPACITY.done, BAR_OPACITY.blocked]) {
        const fill = opacity < 1 ? blendWithWhite(color, opacity) : color
        expect(apcaContrast(readableText(color, opacity), fill), `${color} × ${opacity}`).toBeGreaterThanOrEqual(50)
      }
    }
  })
  it('на светлых полосах — тёмный текст, на тёмных — белый', () => {
    expect(readableText('#84cc16')).not.toBe('#ffffff')
    expect(readableText('#f59e0b')).not.toBe('#ffffff')
    expect(readableText('#1e293b')).toBe('#ffffff')
  })
  it('0.5.0: на средних тонах при почти равном контрасте — белый (бирюзовый, голубой, оранжевый), и не хуже Lc 50', () => {
    for (const key of ['teal', 'cyan', 'orange']) {
      const { color } = PALETTE.find((c) => c.key === key)
      expect(readableText(color), key).toBe('#ffffff')
      expect(apcaContrast('#ffffff', color), key).toBeGreaterThanOrEqual(50)
    }
    // Зелёный и жёлтый: чёрный заметно контрастнее — остаётся чёрный
    expect(readableText('#22c55e')).toBe('#000000')
    expect(readableText('#fde047')).toBe('#000000')
  })
  it('0.3.1: на синем, красном, фиолетовом, индиго, сером и тёмных цветах — белый текст, и на бледных полосах', () => {
    for (const key of ['blue', 'red', 'purple', 'indigo', 'slate', 'pink']) {
      const { color } = PALETTE.find((c) => c.key === key)
      expect(readableText(color), key).toBe('#ffffff')
      expect(readableText(color, BAR_OPACITY.done), `${key} завершена`).toBe('#ffffff')
    }
    // Тёмно-зелёный и коричневый на завершённой задаче: по WCAG 2 получался чёрный текст
    expect(readableText('#166534', BAR_OPACITY.done)).toBe('#ffffff')
    expect(readableText('#92400e', BAR_OPACITY.done)).toBe('#ffffff')
    expect(readableText('#fde047')).toBe('#000000')
  })
  it('статусы в строке — тёмными оттенками', () => {
    for (const s of STATUSES) expect(contrastRatio(s.text, '#ffffff'), s.key).toBeGreaterThanOrEqual(4.5)
  })
  it('цветные подписи пометок и вех затемняются до читаемого', () => {
    for (const { color } of PALETTE) expect(contrastRatio(textColorOnWhite(color), '#ffffff'), color).toBeGreaterThanOrEqual(4.5)
    expect(textColorOnWhite('#1e293b')).toBe('#1e293b')
  })
})
