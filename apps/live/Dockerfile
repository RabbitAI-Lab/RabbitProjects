FROM node:22-alpine AS builder
RUN corepack enable && corepack prepare pnpm@11.0.0 --activate
WORKDIR /repo
COPY pnpm-workspace.yaml pnpm-lock.yaml package.json .npmrc turbo.json tsconfig.base.json ./
COPY packages/ packages/
COPY apps/live/package.json apps/live/
RUN pnpm install --frozen-lockfile --filter=@rp/live...
COPY apps/live/ apps/live/
RUN pnpm turbo run build --filter=@rp/live
RUN pnpm deploy --filter=@rp/live --prod /out

FROM node:22-alpine AS runtime
RUN apk add --no-cache curl && addgroup -S rp && adduser -S -u 10001 -G rp rp
WORKDIR /app
COPY --from=builder /out /app
USER rp
EXPOSE 3000
CMD ["node", "dist/index.js"]
