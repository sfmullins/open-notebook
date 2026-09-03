import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { GET } from './route'

describe('GET /config', () => {
  const originalEnv = process.env

  beforeEach(() => {
    process.env = { ...originalEnv }
    delete process.env.API_URL
    delete process.env.NEXT_PUBLIC_API_URL
  })

  afterEach(() => {
    process.env = originalEnv
  })

  it('uses API_URL when explicitly configured', async () => {
    process.env.API_URL = 'https://configured.example.com'

    const response = await GET()
    const body = await response.json()

    expect(body.apiUrl).toBe('https://configured.example.com')
  })

  it('uses NEXT_PUBLIC_API_URL when API_URL is absent', async () => {
    process.env.NEXT_PUBLIC_API_URL = 'https://public.example.com'

    const response = await GET()
    const body = await response.json()

    expect(body.apiUrl).toBe('https://public.example.com')
  })

  it('prefers API_URL over NEXT_PUBLIC_API_URL', async () => {
    process.env.API_URL = 'https://private.example.com'
    process.env.NEXT_PUBLIC_API_URL = 'https://public.example.com'

    const response = await GET()
    const body = await response.json()

    expect(body.apiUrl).toBe('https://private.example.com')
  })

  it('defaults to the same origin when no explicit backend URL is configured', async () => {
    const response = await GET()
    const body = await response.json()

    expect(body.apiUrl).toBe('')
  })
})
