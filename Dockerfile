# syntax=docker/dockerfile:1.7
FROM node:22-bookworm-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv python3-pip ca-certificates && rm -rf /var/lib/apt/lists/*
COPY package.json package-lock.json ./
RUN npm ci
COPY pyproject.toml uv.lock ./
RUN python3 -m pip install --break-system-packages --no-cache-dir uv==0.8.22 && uv sync --frozen --no-dev --no-install-project
COPY . .
RUN npm run build && cp -r public .next/standalone/ && cp -r .next/static .next/standalone/.next/

FROM node:22-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 TITANSKIES_BIND_ADDR=127.0.0.1 TITANSKIES_PORT=8080 TITANSKIES_DATA_DIR=/var/lib/titanskies TITANSKIES_CACHE_DIR=/var/cache/titanskies CONTEXT_SOURCE=live
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/lib/titanskies /var/cache/titanskies \
    && chown node:node /var/lib/titanskies /var/cache/titanskies
COPY --from=build --chown=node:node /app/.venv /app/.venv
COPY --from=build --chown=node:node /app/.next/standalone /app/.next/standalone
COPY --from=build --chown=node:node /app/ingest /app/ingest
COPY --from=build --chown=node:node /app/scripts /app/scripts
COPY --from=build --chown=node:node /app/shared /app/shared
USER node
EXPOSE 8080
ENTRYPOINT ["/app/scripts/container-entrypoint.sh"]
CMD ["web"]
