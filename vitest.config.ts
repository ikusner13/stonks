import { defineConfig } from 'vitest/config'

// Standalone config — does NOT load the app's vite plugins (TanStack Start,
// Nitro, etc.). These are fast, isolated unit tests over pure logic.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
