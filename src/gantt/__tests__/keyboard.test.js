import { describe, expect, it } from 'vitest'
import { isUndoRedo, keyContext } from '../keyboard'

/** Элемент-заглушка: closest() ищет по списку предков, заданных селекторами, которым он «соответствует». */
function element(tagName, { matches = [], ancestors = [], editable = false } = {}) {
  return {
    tagName, isContentEditable: editable,
    closest(selector) {
      const wanted = selector.split(',').map((s) => s.trim())
      return [...matches, ...ancestors].some((m) => wanted.includes(m)) ? {} : null
    },
  }
}
const at = (target) => keyContext({ target })

describe('клавиши диаграммы не перехватываются у кнопок, меню и карточки', () => {
  it('поля ввода — свои клавиши', () => {
    expect(at(element('INPUT'))).toBe('typing')
    expect(at(element('TEXTAREA', { ancestors: ['.panel'] }))).toBe('typing')
    expect(at(element('DIV', { editable: true }))).toBe('typing')
  })
  it('карточка строки, меню, окно — своя клавиатура', () => {
    expect(at(element('BUTTON', { matches: ['button'], ancestors: ['.panel'] }))).toBe('owned')
    expect(at(element('BUTTON', { matches: ['button'], ancestors: ['.menu'] }))).toBe('owned')
    expect(at(element('DIV', { ancestors: ['[role="menu"]'] }))).toBe('owned')
    expect(at(element('BUTTON', { matches: ['button'], ancestors: ['[role="dialog"]'] }))).toBe('owned')
  })
  it('кнопка и ссылка: Enter нажимает их, а не открывает карточку', () => {
    expect(at(element('BUTTON', { matches: ['button'] }))).toBe('control')
    expect(at(element('A', { matches: ['a[href]'] }))).toBe('control')
    expect(at(element('DIV', { matches: ['[role="tab"]'] }))).toBe('control')
  })
  it('диаграмма и страница — сочетания работают', () => {
    expect(at(element('BODY'))).toBe('free')
    expect(at(element('DIV'))).toBe('free')
    expect(keyContext({ target: null })).toBe('free')
  })
  it('Ctrl+Z / Ctrl+Y / ⌘+Shift+Z — отмена и повтор', () => {
    expect(isUndoRedo({ ctrlKey: true, key: 'z' })).toBe(true)
    expect(isUndoRedo({ metaKey: true, shiftKey: true, key: 'Z' })).toBe(true)
    expect(isUndoRedo({ ctrlKey: true, key: 'y' })).toBe(true)
    expect(isUndoRedo({ key: 'z' })).toBe(false)
    expect(isUndoRedo({ ctrlKey: true, key: 'd' })).toBe(false)
  })
})
