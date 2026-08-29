import os
import re
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_file
import yt_dlp

app = Flask(__name__)

API_KEY = os.environ.get("API_KEY", "").strip()
MAX_SECONDS = int(os.environ.get("MAX_SECONDS", "3600"))

ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}


def valid_youtube_url(value: str) -> bool:
    try:
        p = urlparse(value.strip())
        host = (p.hostname or "").lower()
        if host not in ALLOWED_HOSTS:
            return False
        if p.scheme not in ("http", "https"):
            return False
        return True
    except Exception:
        return False


def check_api_key():
    if not API_KEY:
        return True
    supplied = request.headers.get("X-Converter-Key", "")
    return secrets.compare_digest(supplied, API_KEY)


def safe_title(value: str) -> str:
    value = re.sub(r"[^\w\-. ]+", "", value, flags=re.UNICODE).strip()
    return (value[:120] or "mp3zivo-conversie")


@app.get("/")
def index():
    return jsonify({
        "service": "MP3ZIVO conversion server",
        "status": "ok",
        "youtube": True,
    })


@app.post("/convert")
def convert():
    if not check_api_key():
        return jsonify({"error": "Ongeldige API key."}), 401

    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    fmt = str(data.get("format", "mp3")).lower().strip()

    if not valid_youtube_url(url):
        return jsonify({
            "error": "Gebruik een geldige YouTube-link."
        }), 400

    if fmt not in {"mp3", "mp4"}:
        return jsonify({"error": "Formaat moet mp3 of mp4 zijn."}), 400

    workdir = Path(tempfile.mkdtemp(prefix="mp3zivo-"))

    try:
        common = {
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
            "outtmpl": str(workdir / "%(title)s.%(ext)s"),
            "socket_timeout": 30,
            "retries": 2,
            "max_filesize": 500 * 1024 * 1024,
        }

        if fmt == "mp3":
            opts = {
                **common,
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
            }
        else:
            opts = {
                **common,
                "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "merge_output_format": "mp4",
            }

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = safe_title(info.get("title", "mp3zivo-conversie"))

        wanted_ext = fmt
        files = [
            p for p in workdir.iterdir()
            if p.is_file() and p.suffix.lower() == "." + wanted_ext
        ]

        if not files:
            return jsonify({
                "error": "Conversie voltooid, maar het uitvoerbestand werd niet gevonden."
            }), 500

        output = files[0]

        return send_file(
            output,
            mimetype="audio/mpeg" if fmt == "mp3" else "video/mp4",
            as_attachment=True,
            download_name=f"{title}.{fmt}",
            max_age=0,
        )

    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc)
        if "Sign in" in msg or "bot" in msg.lower() or "captcha" in msg.lower():
            msg = "YouTube weigert deze video momenteel aan de server. Probeer een andere video."
        return jsonify({"error": msg[:500]}), 502

    except Exception as exc:
        return jsonify({"error": f"Conversie mislukt: {str(exc)[:400]}"}), 500

    finally:
        # Het bestand wordt na verzending door de tijdelijke omgeving opgeruimd.
        # Geen blijvende mediabibliotheek op de server.
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
