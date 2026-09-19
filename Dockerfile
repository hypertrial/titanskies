# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
FROM ghcr.io/astral-sh/uv:0.8.22@sha256:9874eb7afe5ca16c363fe80b294fe700e460df29a55532bbfea234a0f12eddb1 AS uv

FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python UV_PYTHON=3.12
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
COPY package.json package-lock.json ./
RUN npm ci
COPY pyproject.toml uv.lock .python-version ./
RUN uv python install 3.12 && uv sync --frozen --no-dev --no-install-project
COPY . .
RUN npm run build && cp -r public .next/standalone/ && cp -r .next/static .next/standalone/.next/

FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 TITANSKIES_BIND_ADDR=127.0.0.1 TITANSKIES_PORT=8080 TITANSKIES_DATA_DIR=/var/lib/titanskies TITANSKIES_CACHE_DIR=/var/cache/titanskies CONTEXT_SOURCE=live
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python UV_PYTHON=3.12
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/lib/titanskies /var/cache/titanskies \
    && chown node:node /var/lib/titanskies /var/cache/titanskies
COPY --from=build /opt/uv/python /opt/uv/python
COPY --from=build --chown=node:node /app/.venv /app/.venv
COPY --from=build --chown=node:node /app/.next/standalone /app/.next/standalone
COPY --from=build --chown=node:node /app/ingest /app/ingest
COPY --from=build --chown=node:node /app/scripts/container-entrypoint.sh /app/scripts/container-entrypoint.sh
COPY --from=build --chown=node:node /app/scripts/run-web.mjs /app/scripts/run-web.mjs
COPY --from=build --chown=node:node /app/scripts/run_python.sh /app/scripts/run_python.sh
COPY --from=build --chown=node:node /app/scripts/watch_context.py /app/scripts/watch_context.py
COPY --from=build --chown=node:node /app/scripts/check-publication.py /app/scripts/check-publication.py
COPY --from=build --chown=node:node /app/shared /app/shared
USER node
EXPOSE 8080
ENTRYPOINT ["/app/scripts/container-entrypoint.sh"]
CMD ["web"]
