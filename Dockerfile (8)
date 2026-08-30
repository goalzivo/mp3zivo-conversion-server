# Build the PO-token provider with the same Node version used by its
# current upstream Docker image.
FROM node:26-bookworm-slim AS bgutil
WORKDIR /opt/bgutil/server
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && git clone --depth 1 --branch 1.3.1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil
WORKDIR /opt/bgutil/server
RUN npm ci --omit=dev --no-audit --no-fund \
    && npm ci --no-audit --no-fund \
    && npx tsc

FROM node:26-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DENO_INSTALL=/root/.deno \
    PATH="/root/.deno/bin:/opt/venv/bin:${PATH}" \
    DENO_NO_PROMPT=1 \
    DENO_NO_UPDATE_CHECK=1 \
    YTDLP_POT_PROVIDER_URL=http://127.0.0.1:4416

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    ffmpeg \
    curl \
    ca-certificates \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Deno is the JS runtime yt-dlp uses for its EJS challenge solver.
RUN curl -fsSL https://deno.land/install.sh | sh

# Copy the already-built bgutil provider from the Node 26 build stage.
COPY --from=bgutil /opt/bgutil/server /opt/bgutil/server

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 10000 4416

# Start the PO-token HTTP server first, then the Flask/Gunicorn API.
CMD ["sh", "-c", "node /opt/bgutil/server/build/main.js --port 4416 & exec /opt/venv/bin/gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 4 --timeout 300 app:app"]
