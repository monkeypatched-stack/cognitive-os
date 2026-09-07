# Living World Explorer — production build served by nginx, with nginx
# itself doing what Vite's dev proxy (vite.config.ts) does in development:
# proxy /api (and the root-level /live, /health, /ready probes) to the
# real backend, so the app's own code can keep using the same relative
# paths (src/api/client.ts's API_BASE/ROOT_BASE, authStore.ts's AUTH_BASE)
# in both dev and this production container — no VITE_*_URL env override
# needed at build time, only nginx's own proxy_pass target at runtime.
FROM node:22-slim AS build
WORKDIR /app
COPY living-world-explorer/package.json living-world-explorer/package-lock.json ./
RUN npm ci
COPY living-world-explorer/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY docker/services/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q -O- http://localhost:8080/ || exit 1
