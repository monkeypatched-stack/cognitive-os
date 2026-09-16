FROM node:22-alpine AS build
WORKDIR /app
COPY apps/workspace/package.json apps/workspace/package-lock.json* ./
RUN npm install
COPY apps/workspace/ ./

# NEXT_PUBLIC_* vars are resolved once, at `next build` time -- Next.js
# bakes next.config.ts's own rewrites() resolution into the build output,
# a K8s/compose *runtime* env var override has no effect on an
# already-built image (confirmed live: setting it only in the Deployment
# spec left the container still proxying to the Dockerfile's build-time
# default). Must be passed as a --build-arg (scripts/workspace_demo_stack.sh
# does this) whenever the target backend differs from the default below.
ARG NEXT_PUBLIC_API_TARGET=http://localhost:8031
ENV NEXT_PUBLIC_API_TARGET=$NEXT_PUBLIC_API_TARGET
ARG NEXT_PUBLIC_LIVEKIT_URL=
ENV NEXT_PUBLIC_LIVEKIT_URL=$NEXT_PUBLIC_LIVEKIT_URL

RUN npm run build

# Production runtime: `next start` serves the built app AND still runs
# next.config.ts's rewrites() server-side (same proxy-to-Kong mechanism
# `next dev` uses) -- unlike docker/services/frontend/'s plain static
# SPA (nginx doing the proxying itself), this app needs an actual Node
# server for that, so this stays a Node runtime image rather than nginx.
FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/package.json /app/package-lock.json* ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
COPY --from=build /app/next.config.ts ./
COPY --from=build /app/public ./public

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O- http://localhost:3000/login || exit 1

CMD ["npx", "next", "start", "-H", "0.0.0.0", "-p", "3000"]
