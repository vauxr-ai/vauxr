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
# Pipecat's smallwebrtc path imports opencv (cv2) unconditionally, even for
# audio-only pipelines; cv2 needs these X/GL system libs or its import fails
# with `libxcb.so.1: cannot open shared object file`, which kills the WebRTC
# connection callback and leaves the device hung in listening mode.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libxcb1 libxext6 libsm6 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY --from=build /wheels /wheels
# Install the wheel with the `realtime` extra so the in-process Pipecat WebRTC
# pipeline (aiortc + silero VAD) is available when REALTIME_ENABLED=1. The
# extra is applied to the built wheel path; deps resolve from the index.
RUN pip install --no-cache-dir "$(ls /wheels/*.whl)[realtime]"
# Pre-seed NLTK punkt data into a default search path so Pipecat's sentence
# tokenizer (used for TTS chunking) doesn't try to download at runtime and fail
# on the read-only filesystem with a permission error.
RUN python -m nltk.downloader -d /usr/share/nltk_data punkt punkt_tab
COPY --from=web-build /app/web-client/dist ./web-client/dist
RUN mkdir -p /data && chown vauxr:vauxr /data
USER vauxr
EXPOSE 8765
EXPOSE 8080
# Flat layout — server modules sit at top level after hatchling's sources=["src"]
CMD ["python", "-m", "server"]
