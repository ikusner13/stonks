import { createServerFn } from '@tanstack/react-start'
import { researchTickerCached } from '../llm/cached-research.js'
import type { ReviewMode } from '../llm/critic.js'
import { formatEvent, withRun } from '../llm/usage.js'

interface ResearchInput {
  symbol: string
  mode?: ReviewMode
  fresh?: boolean
}

// Server-only: cached research + critic pipeline, one wide usage event per run.
export const research = createServerFn({ method: 'POST' })
  .validator((input: string | ResearchInput) =>
    typeof input === 'string' ? { symbol: input } : input,
  )
  .handler(async ({ data }) => {
    const symbol = data.symbol.toUpperCase()
    const mode = data.mode ?? 'thorough'
    return withRun(
      { kind: 'research', subject: symbol, mode },
      () => researchTickerCached(symbol, { mode, fresh: data.fresh }),
      (event) => console.error('\n' + formatEvent(event)),
    )
  })
