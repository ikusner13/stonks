import {
  createOpenRouter,
  type OpenRouterChatSettings,
} from "@openrouter/ai-sdk-provider";

export const WORKHORSE_ID = process.env.WORKHORSE_MODEL ?? "google/gemini-2.5-flash";
export const PREMIUM_ID = process.env.PREMIUM_MODEL ?? "anthropic/claude-sonnet-4.6";

let provider: ReturnType<typeof createOpenRouter> | undefined;

// Lazy so importing this module (e.g. to print CLI usage) doesn't require the key.
function openrouter() {
  if (!provider) {
    const apiKey = process.env.OPENROUTER_API_KEY;
    if (!apiKey) {
      throw new Error(
        "OPENROUTER_API_KEY is not set. Get a key at https://openrouter.ai/keys and provide it via the environment (e.g. a .env file, or `OPENROUTER_API_KEY=... nr research <SYMBOL>`).",
      );
    }
    provider = createOpenRouter({ apiKey });
  }
  return provider;
}

// `usage.include` turns on OpenRouter usage accounting so each response carries
//   providerMetadata.openrouter.usage = { cost, promptTokensDetails.cachedTokens, ... }
// which is how W1 reports real $ and cache-hit tokens.
// `data_collection: "deny"` keeps financial prompts off providers that log/train.
const BASE: OpenRouterChatSettings = {
  usage: { include: true },
  provider: { data_collection: "deny" },
};

function merge(extra?: OpenRouterChatSettings): OpenRouterChatSettings {
  return {
    ...BASE,
    ...extra,
    provider: { ...BASE.provider, ...extra?.provider },
  };
}

// Cheap, high-volume calls (research draft, discovery). Fallbacks allowed for
// resilience; callers may pass { provider: { sort: "price" } } to chase the floor.
export const workhorse = (extra?: OpenRouterChatSettings) =>
  openrouter()(WORKHORSE_ID, merge(extra));

// Quality-critical calls (the critic chain). `allow_fallbacks: false` makes
// routing sticky so the cached prefix lands on the same provider endpoint across
// calls 2–4 (W2) — Anthropic prompt caching only pays off on cache hits.
export const premium = (extra?: OpenRouterChatSettings) =>
  openrouter()(
    PREMIUM_ID,
    merge({ ...extra, provider: { allow_fallbacks: false, ...extra?.provider } }),
  );
