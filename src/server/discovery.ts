import { createServerFn } from '@tanstack/react-start'
import { discoverIdeas } from '../llm/discovery.js'

// Server-only: wraps the core discovery pipeline. Heavy imports (LLM, data
// sources) stay out of the client bundle because they're only used in handler.
export const discover = createServerFn({ method: 'POST' })
  .validator((goal: string) => goal)
  .handler(async ({ data }) => discoverIdeas(data))
