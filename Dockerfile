# syntax=docker/dockerfile:1
# Production image for the TanStack Start (Nitro) SSR app.
# The Python optimizer runs separately — see sidecar/ and docker-compose.yml.

FROM node:22-slim AS builder
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
# --ignore-scripts: the only dep with a build script is esbuild, whose native
# binary ships via its @esbuild/<platform> optional dep — the script is a no-op
# here. pnpm 11 otherwise hard-errors (ERR_PNPM_IGNORED_BUILDS) on it in CI.
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile --ignore-scripts
COPY . .
RUN pnpm build

FROM node:22-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000
# Nitro's node-server output is self-contained (deps bundled into .output).
COPY --from=builder /app/.output ./.output
EXPOSE 3000
CMD ["node", ".output/server/index.mjs"]
