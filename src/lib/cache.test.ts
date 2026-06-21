import { rmSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readCache, withCache, writeCache } from './cache.js'

const NS = '__vitest__'
const dir = resolve(process.cwd(), '.cache', NS)

afterEach(() => {
  rmSync(dir, { recursive: true, force: true })
  vi.useRealTimers()
})

describe('withCache', () => {
  it('misses then hits, running produce only once', async () => {
    let calls = 0
    const produce = async () => {
      calls++
      return { v: calls }
    }
    expect(await withCache(NS, 'k', 60_000, produce)).toEqual({ value: { v: 1 }, hit: false })
    expect(await withCache(NS, 'k', 60_000, produce)).toEqual({ value: { v: 1 }, hit: true })
    expect(calls).toBe(1)
  })

  it('fresh:true bypasses a warm cache', async () => {
    let calls = 0
    const produce = async () => ++calls
    await withCache(NS, 'k', 60_000, produce)
    const r = await withCache(NS, 'k', 60_000, produce, { fresh: true })
    expect(r).toEqual({ value: 2, hit: false })
    expect(calls).toBe(2)
  })
})

describe('readCache / writeCache TTL', () => {
  it('returns the value before expiry and null after', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 0, 1))
    writeCache(NS, 'k', 'val', 1000)
    expect(readCache(NS, 'k')).toBe('val')
    vi.advanceTimersByTime(1001)
    expect(readCache(NS, 'k')).toBeNull()
  })

  it('ttl of 0 never expires', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 0, 1))
    writeCache(NS, 'k', 'forever', 0)
    vi.advanceTimersByTime(1e12)
    expect(readCache(NS, 'k')).toBe('forever')
  })

  it('isolates entries by key and returns null for a miss', () => {
    writeCache(NS, 'a', 1, 60_000)
    writeCache(NS, 'b', 2, 60_000)
    expect(readCache(NS, 'a')).toBe(1)
    expect(readCache(NS, 'b')).toBe(2)
    expect(readCache(NS, 'absent')).toBeNull()
  })
})
