# MP3ZIVO conversion server
# Uses the official bgutil PO-token provider image instead of building it manually.
# This avoids the previous Deno/unzip and provider build failures.

FROM brainicism/bgutil-ytdlp-pot-provider:1.3.1-node AS pot_provider

FROM node:26-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:${PATH}" \
    YTDLP_POT_PROVIDER_URL=http://127.0.0.1:4416 \
    DENO_NO_PROMPT=1 \
    DENO_NO_UPDATE_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy the prebuilt official PO-token provider.
COPY --from=pot_provider /app/build /opt/bgutil/build

WORKDIR /app
COPY requirements.txt .

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 10000 4416

CMD ["sh", "-c", "node /opt/bgutil/build/main.js --port 4416 & exec /opt/venv/bin/gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 4 --timeout 300 app:app"]
