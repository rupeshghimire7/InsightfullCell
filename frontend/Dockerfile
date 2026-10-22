# syntax=docker/dockerfile:1.7
# Build context: frontend/.   docker build -t insightfulcell-web frontend
FROM node:22-alpine AS deps
RUN corepack enable
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
COPY patches ./patches
RUN --mount=type=cache,target=/root/.local/share/pnpm/store pnpm install --frozen-lockfile

FROM node:22-alpine AS build
RUN corepack enable
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
# No secrets are needed to build; configuration is read when the server starts.
# Node's default heap is sized from the machine's memory and is too small for `next build` on a
# 4 GB host; with swap on, 3 GB fits (lower it on smaller hosts, e.g. --build-arg ...=2048).
ARG NODE_BUILD_HEAP_MB=3072
RUN NODE_OPTIONS="--max-old-space-size=${NODE_BUILD_HEAP_MB}" pnpm build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -S app && adduser -S app -G app
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static
COPY --from=build --chown=app:app /app/public ./public
USER app
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s \
  CMD wget -q -O /dev/null http://127.0.0.1:3000/sign-in || exit 1
CMD ["node", "server.js"]
