FROM node:22-alpine AS web-build
WORKDIR /app/web-client
COPY web-client/package.json web-client/package-lock.json* ./
RUN npm install
COPY web-client/ ./
RUN npm run build

FROM python:3.12-slim AS build
WORKDIR /app
RUN pip install --no-cache-dir --upgrade pip build
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip wheel --no-cache-dir --no-deps -w /wheels .

FROM python:3.12-slim
WORKDIR /app
# Pin numeric uid/gid to match the Alpine `vauxr` user from the previous
# Node image. Without this, an existing /data volume (owned by uid 100)
# becomes unwritable after the rewrite — every channel create/rotate hits
# PermissionError → 500, and channel-token bearer auth then 401s because
# no channels can be persisted.
RUN groupadd --system --gid 101 vauxr && useradd --system --uid 100 --gid vauxr vauxr
COPY pyproject.toml README.md ./
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl
COPY --from=web-build /app/web-client/dist ./web-client/dist
RUN mkdir -p /data && chown vauxr:vauxr /data
USER vauxr
EXPOSE 8765
EXPOSE 8080
# Flat layout — server modules sit at top level after hatchling's sources=["src"]
CMD ["python", "-m", "server"]
