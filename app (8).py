import hmac
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from flask import Flask, jsonify, request, send_file

app = Flask(__name__)

API_KEY = os.environ.get("MP3ZIVO_API_KEY", "").strip()
MAX_FILE_MB = int(os.environ.get("MAX_FILE_MB", "500"))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024
MAX_SECONDS = int(os.environ.get("MAX_SECONDS", "3600"))
POT_PROVIDER_URL = os.environ.get(
    "YTDLP_POT_PROVIDER_URL", "http://127.0.0.1:4416"
).strip()

YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "music.youtube.com", "youtu.be", "www.youtu.be",
}


def error(message, status=400):
    return jsonify({"error": message}), status


def authorized():
    if not API_KEY:
        return True
    supplied = request.headers.get("X-Converter-Key", "")
    return hmac.compare_digest(supplied, API_KEY)


def is_youtube_url(url):
    try:
        p = urlparse(url.strip())
        return (
            p.scheme in ("http", "https")
            and (p.hostname or "").lower() in YOUTUBE_HOSTS
        )
    except Exception:
        return False


def clean_name(value):
    value = re.sub(r'[\\/:*?"<>|]+', "_", value or "")
    value = re.sub(r"\s+", " ", value).strip()
    return value[:120].strip(" .") or "mp3zivo-conversie"


def find_output(directory, fmt):
    wanted = ".mp3" if fmt == "mp3" else ".mp4"
    files = [
        p for p in Path(directory).iterdir()
        if p.is_file() and not p.name.endswith((".part", ".ytdl"))
    ]
    matching = [p for p in files if p.suffix.lower() == wanted]
    if matching:
        return max(matching, key=lambda p: p.stat().st_size)
    return max(files, key=lambda p: p.stat().st_size) if files else None


def ytdlp_options(workdir, fmt):
    common = {
        "outtmpl": str(Path(workdir) / "%(title).100s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "restrictfilenames": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "continuedl": True,
        "overwrites": True,
        "max_filesize": MAX_FILE_BYTES,
        # yt-dlp's EJS support; Deno is intentionally NOT installed here.
        # Node is available in the image and can run the provider/EJS code.
        "js_runtimes": {"node": {}},
        "remote_components": ["ejs:github"],
        "extractor_args": {
            "youtube": {
                "player_client": ["mweb"],
            },
            "youtubepot-bgutilhttp": {
                "base_url": [POT_PROVIDER_URL],
            },
        },
    }

    if fmt == "mp3":
        return {
            **common,
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        }

    return {
        **common,
        "format": "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/best",
        "merge_output_format": "mp4",
    }


@app.get("/")
def index():
    return jsonify({
        "service": "MP3ZIVO conversion server",
        "status": "ok",
        "converter": "/convert",
        "youtube": True,
        "po_token_provider": POT_PROVIDER_URL,
        "yt_dlp": yt_dlp.version.__version__,
    })


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/convert")
def convert():
    if not authorized():
        return error("Ongeldige API key.", 401)

    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    fmt = str(data.get("format", "mp3")).lower().strip()

    if not url:
        return error("Geen URL opgegeven.")
    if fmt not in ("mp3", "mp4"):
        return error("Format moet mp3 of mp4 zijn.")
    if not is_youtube_url(url):
        return error("Gebruik een geldige openbare YouTube-link.")

    workdir = tempfile.mkdtemp(prefix="mp3zivo-")

    try:
        with yt_dlp.YoutubeDL(ytdlp_options(workdir, fmt)) as ydl:
            info = ydl.extract_info(url, download=True)

        duration = info.get("duration")
        if duration and duration > MAX_SECONDS:
            return error(
                f"Deze video is langer dan de limiet van {MAX_SECONDS // 60} minuten."
            )

        output = find_output(workdir, fmt)
        if not output or not output.exists():
            return error("Geen uitvoerbestand gevonden.", 500)

        size = output.stat().st_size
        if size <= 0:
            return error("Het uitvoerbestand is leeg.", 500)
        if size > MAX_FILE_BYTES:
            return error(f"Bestand groter dan {MAX_FILE_MB} MB.", 413)

        title = clean_name(info.get("title"))
        mime = "audio/mpeg" if fmt == "mp3" else "video/mp4"

        response = send_file(
            output,
            mimetype=mime,
            as_attachment=True,
            download_name=f"{title}.{fmt}",
            max_age=0,
        )
        response.call_on_close(
            lambda: shutil.rmtree(workdir, ignore_errors=True)
        )
        return response

    except yt_dlp.utils.DownloadError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        raw = str(exc).strip()
        lower = raw.lower()

        if "sign in to confirm" in lower or "confirm you're not a bot" in lower:
            message = (
                "YouTube blokkeert deze serveraanvraag met een bot/login-controle. "
                "De PO-token provider is actief, maar YouTube kan ook het "
                "server-IP of de specifieke video beperken."
            )
        elif "video unavailable" in lower or "this video is not available" in lower:
            message = "Deze YouTube-video is niet beschikbaar voor deze server."
        elif "private video" in lower or "members-only" in lower:
            message = "Deze video vereist geautoriseerde toegang."
        elif "age-restricted" in lower or "sign in to confirm your age" in lower:
            message = "Deze video vereist leeftijdsverificatie."
        elif "http error 403" in lower:
            message = "YouTube weigert de mediastream (HTTP 403)."
        else:
            message = f"YouTube kon de video niet verwerken: {raw[:500]}"

        app.logger.warning("yt-dlp failed: %s", raw)
        return error(message, 502)

    except Exception as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        app.logger.exception("MP3ZIVO conversion failed")
        return error(f"Interne conversiefout: {str(exc)[:300]}", 500)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "10000"))
    )
