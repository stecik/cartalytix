import { QueryClient } from '@tanstack/vue-query'
import { describe, expect, it, vi } from 'vitest'
import { createAppRouter } from './router'

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
