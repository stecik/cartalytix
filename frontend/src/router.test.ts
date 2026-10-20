import { QueryClient } from '@tanstack/vue-query'
import { describe, expect, it, vi } from 'vitest'
import type { User } from './api'
import { accountRedirect, createAppRouter } from './router'

const base: User = { id: 'u', email: 'u@example.com', locale: 'en', receipt_view: 'rendered', theme: 'system', dashboard_period: '12m', dashboard_category_slices: 9, is_admin: false, must_change_password: false }

describe('account routing', () => {
  it('forces issued credentials to be replaced before anything else', () => {
    const pending = { ...base, must_change_password: true }
    expect(accountRedirect(pending, 'receipts')).toEqual({ name: 'change-credentials' })
    expect(accountRedirect({ ...pending, is_admin: true }, 'accounts')).toEqual({ name: 'change-credentials' })
    expect(accountRedirect(pending, 'change-credentials')).toBeNull()
  })

  it('keeps administrators on account management and settings only', () => {
    const admin = { ...base, is_admin: true }
    expect(accountRedirect(admin, 'overview')).toEqual({ name: 'accounts' })
    expect(accountRedirect(admin, 'connections')).toEqual({ name: 'accounts' })
    expect(accountRedirect(admin, 'login')).toEqual({ name: 'accounts' })
    expect(accountRedirect(admin, 'settings')).toBeNull()
    expect(accountRedirect(admin, 'accounts')).toBeNull()
  })

  it('keeps regular users out of account management', () => {
    expect(accountRedirect(base, 'accounts')).toEqual({ name: 'overview' })
    expect(accountRedirect(base, 'change-credentials')).toEqual({ name: 'overview' })
    expect(accountRedirect(base, 'receipts')).toBeNull()
  })
})

describe('protected navigation', () => {
  it('redirects an expired session and clears previous private query data', async () => {
    vi.stubGlobal('scrollTo', vi.fn())
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async () => new Response(null, { status: 401 })))
    const client = new QueryClient()
    client.setQueryData(['receipts'], { items: [{ id: 'private' }] })
    const router = createAppRouter(client)
    await router.push('/receipts')
    expect(router.currentRoute.value.name).toBe('login')
    expect(client.getQueryData(['receipts'])).toBeUndefined()
  })
})
