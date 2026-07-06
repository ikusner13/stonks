# stonks-web

React SPA (Vite + TanStack Router/Query) served by a Cloudflare Worker that
proxies `/api/*` to the FastAPI backend.

**Dev**: backend on `:8000`, `cp .dev.vars.example .dev.vars`, then
`npm install && npm run dev`.

**Gen API types**: `npm run gen:api` (needs `uv`) regenerates
`src/api/schema.d.ts` from the backend's OpenAPI schema.

**Deploy**: `npm run build && npm run deploy`. Set `CF_ACCESS_CLIENT_ID` /
`CF_ACCESS_CLIENT_SECRET` as wrangler secrets if the backend sits behind
Cloudflare Access.
