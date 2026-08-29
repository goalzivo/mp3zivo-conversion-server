FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV DENO_INSTALL=/root/.deno
ENV PATH="/root/.deno/bin:${PATH}"
ENV CHROME_BIN=/usr/bin/chromium
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg \
      ca-certificates \
      curl \
      chromium \
      fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

# yt-dlp now needs an external JS runtime for current YouTube extraction.
RUN curl -fsSL https://deno.land/install.sh | sh

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -U -r requirements.txt

COPY app.py .

ENV PORT=8080
CMD ["python", "app.py"]
