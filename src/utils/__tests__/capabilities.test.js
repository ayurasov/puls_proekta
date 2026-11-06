import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  ADMIN_SECTIONS, allowedSections, CAPABILITIES, CAPABILITY_KEYS, canManageAccount, capabilityGroups, effectiveCapabilities,
  hasAdminArea, hasCapability, INTEGRATION_SECTIONS,
} from '../capabilities'

const admin = { role: 'admin', capabilities: [] }
const hr = { role: 'editor', capabilities: ['org.manage'] }
const support = { role: 'editor', capabilities: ['users.manage'] }
const plain = { role: 'viewer', capabilities: [] }

describe('полномочия без роли администратора (0.7.2, ADR-0013)', () => {
  it('тот же список и те же подписи, что на сервере', () => {
    const python = readFileSync(resolve(__dirname, '../../../backend/app/capabilities.py'), 'utf8')
    const server = [...python.matchAll(/^\s+"([a-z.]+)": "([^"]+)",$/gm)].map((m) => [m[1], m[2]])
    expect(server).toEqual(CAPABILITIES.map((c) => [c.key, c.label]))
  })

  it('у администратора — все, у отключённого — никаких, порядок — как в списке', () => {
    expect(effectiveCapabilities(admin)).toEqual(CAPABILITY_KEYS)
    expect(effectiveCapabilities({ role: 'editor', capabilities: ['trash.manage', 'system.health', 'нечто'] }))
      .toEqual(['system.health', 'trash.manage'])
    expect(effectiveCapabilities({ ...hr, isActive: false })).toEqual([])
    expect(hasCapability(hr, 'users.manage', 'org.manage')).toBe(true)
    expect(hasCapability(hr, 'admin')).toBe(false)
    expect(hasCapability(admin, 'admin')).toBe(true)
    expect(hasAdminArea(plain)).toBe(false)
    expect(hasAdminArea(hr)).toBe(true)
  })

  it('разделы меню — по полномочиям; ключи доступа и вход через домен — только администратору', () => {
    expect(allowedSections(hr, ADMIN_SECTIONS).map((s) => s.to)).toEqual(['/admin/structure', '/admin/org-transfer'])
    expect(allowedSections(admin, ADMIN_SECTIONS)).toHaveLength(ADMIN_SECTIONS.length)
    const modules = { role: 'editor', capabilities: ['modules.manage'] }
    expect(allowedSections(modules, INTEGRATION_SECTIONS).map((s) => s.label)).toEqual(['Jira', '«По Делу»'])
    expect(allowedSections(admin, INTEGRATION_SECTIONS)).toHaveLength(4)
    expect(allowedSections(plain, ADMIN_SECTIONS)).toEqual([])
  })

  it('«Ведёт учётные записи» не меняет администраторов и тех, у кого есть чужие полномочия', () => {
    expect(canManageAccount(support, plain)).toBe(true)
    expect(canManageAccount(support, admin)).toBe(false)
    expect(canManageAccount(support, hr)).toBe(false)
    expect(canManageAccount(support, { role: 'editor', capabilities: ['users.manage'] })).toBe(true)
    expect(canManageAccount(hr, plain)).toBe(false)
    expect(canManageAccount(admin, admin)).toBe(true)
  })

  it('группы для списка у администратора', () => {
    expect(capabilityGroups().map((g) => [g.name, g.items.length])).toEqual([['Система', 5], ['Люди и доступы', 3], ['Проекты', 1]])
  })
})
