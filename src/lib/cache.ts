import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

// Dependency-light persistent KV: one JSON file per entry under .cache/.
// No Redis/SQLite for a personal tool. Shared by CLI and the web server fns.
const CACHE_DIR = resolve(process.cwd(), ".cache");

interface Entry<T> {
  expiresAt: number; // epoch ms; 0 = never expires
  value: T;
}

function pathFor(namespace: string, key: string): string {
  const safe = key.replace(/[^a-zA-Z0-9._-]/g, "_");
  return resolve(CACHE_DIR, namespace, `${safe}.json`);
}

export function readCache<T>(namespace: string, key: string): T | null {
  try {
    const entry = JSON.parse(readFileSync(pathFor(namespace, key), "utf8")) as Entry<T>;
    if (entry.expiresAt && Date.now() > entry.expiresAt) return null;
    return entry.value;
  } catch {
    return null;
  }
}

export function writeCache<T>(namespace: string, key: string, value: T, ttlMs: number): void {
  try {
    const file = pathFor(namespace, key);
    mkdirSync(dirname(file), { recursive: true });
    const entry: Entry<T> = { expiresAt: ttlMs > 0 ? Date.now() + ttlMs : 0, value };
    writeFileSync(file, JSON.stringify(entry));
  } catch {
    // A cache write failure must never break the call it wraps.
  }
}

// Read-through cache. Returns the cached value (hit) or runs `produce`, stores
// it, and returns it (miss). `fresh` forces a miss to deliberately bypass.
export async function withCache<T>(
  namespace: string,
  key: string,
  ttlMs: number,
  produce: () => Promise<T>,
  opts?: { fresh?: boolean },
): Promise<{ value: T; hit: boolean }> {
  if (!opts?.fresh) {
    const cached = readCache<T>(namespace, key);
    if (cached !== null) return { value: cached, hit: true };
  }
  const value = await produce();
  writeCache(namespace, key, value, ttlMs);
  return { value, hit: false };
}
