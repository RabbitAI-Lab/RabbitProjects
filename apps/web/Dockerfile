FROM node:22-alpine AS builder
RUN corepack enable && corepack prepare pnpm@11.0.0 --activate
WORKDIR /repo
COPY pnpm-workspace.yaml pnpm-lock.yaml package.json .npmrc turbo.json tsconfig.base.json ./
COPY packages/ packages/
COPY apps/web/package.json apps/web/
RUN pnpm install --frozen-lockfile --filter=@rp/web...
COPY apps/web/ apps/web/
ARG VITE_API_BASE_URL=/api/v1
ARG VITE_LIVE_BASE_URL=/live
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL VITE_LIVE_BASE_URL=$VITE_LIVE_BASE_URL
RUN pnpm turbo run build --filter=@rp/web

FROM nginx:1.27-alpine AS runtime
RUN apk add --no-cache curl
COPY apps/web/docker/nginx-spa.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /repo/apps/web/build/client /usr/share/nginx/html
EXPOSE 3001
