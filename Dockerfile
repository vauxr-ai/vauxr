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
RUN groupadd --system vauxr && useradd --system --gid vauxr vauxr
COPY pyproject.toml README.md ./
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl
COPY --from=web-build /app/web-client/dist ./web-client/dist
RUN mkdir -p /data && chown vauxr:vauxr /data
USER vauxr
EXPOSE 8765
EXPOSE 8080
CMD ["python", "-m", "vauxr.server"]
