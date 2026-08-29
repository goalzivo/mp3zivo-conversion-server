FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DENO_INSTALL=/root/.deno \
    PATH="/root/.deno/bin:${PATH}" \
    DENO_NO_PROMPT=1 \
    DENO_NO_UPDATE_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl ca-certificates unzip git \
    && rm -rf /var/lib/apt/lists/*

# Deno is used both by yt-dlp's EJS support and the BgUtils PO-token provider.
RUN curl -fsSL https://deno.land/install.sh | sh

WORKDIR /app

# Install the current PO-token provider source. Pinning the provider major/minor
# keeps its plugin and server compatible.
RUN git clone --depth 1 --branch 1.3.1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil

WORKDIR /opt/bgutil/server
RUN deno install --allow-scripts=npm:canvas --frozen

WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY app.py .

CMD ["sh", "-c", "deno run --allow-env --allow-net --allow-ffi=/opt/bgutil/server/node_modules --allow-read=/opt/bgutil/server/node_modules /opt/bgutil/server/src/main.ts --port 4416 & exec gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 4 --timeout 300 app:app"]
