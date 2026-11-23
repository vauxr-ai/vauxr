# A small, one-shot target: no application or speech dependencies required.
FROM python:3.12-slim AS storage-init
COPY docker/init-storage.py /usr/local/bin/init-storage.py
CMD ["python", "/usr/local/bin/init-storage.py"]

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
ARG VAUXR_EXTRAS=acme
# Pin numeric uid/gid to match the Alpine `vauxr` user from the previous
# Node image. Without this, an existing /data volume (owned by uid 100)
# becomes unwritable after the rewrite, preventing persisted routing and
# enrollment/lifecycle updates.
RUN groupadd --system --gid 101 vauxr && useradd --system --uid 100 --gid vauxr vauxr
# Only custom WebRTC builds need OpenCV's X/GL runtime libraries.
RUN case ",${VAUXR_EXTRAS}," in *,realtime,*) \
      apt-get update \
      && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libxcb1 libxext6 libsm6 \
      && rm -rf /var/lib/apt/lists/* ;; \
    esac
COPY pyproject.toml README.md ./
COPY --from=build /wheels /wheels
# Bridge-mode Docker disables WebRTC, so its default image avoids the large
# realtime dependency tree. A custom host-network build may supply
# --build-arg VAUXR_EXTRAS=realtime,acme when that deployment is configured.
RUN pip install --no-cache-dir "$(ls /wheels/*.whl)[${VAUXR_EXTRAS}]"
# Pipecat's sentence tokenizer needs NLTK data only in realtime builds.
RUN case ",${VAUXR_EXTRAS}," in *,realtime,*) \
      python -m nltk.downloader -d /usr/share/nltk_data punkt punkt_tab ;; \
    esac
COPY --from=web-build /app/web-client/dist ./web-client/dist
RUN mkdir -p /data && chown vauxr:vauxr /data
USER vauxr
EXPOSE 8765
EXPOSE 8080
EXPOSE 8443
CMD ["python", "-m", "vauxr"]
