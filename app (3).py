import os
import re
import secrets
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_file
import yt_dlp

app = Flask(__name__)

API_KEY = os.environ.get("API_KEY", "").strip()
MAX_SECONDS = int(os.environ.get("MAX_SECONDS", "3600"))
MAX_FILE_MB = int(os.environ.get("MAX_FILE_MB", "500"))
CHROME_BIN = os.environ.get("CHROME_BIN", "/usr/bin/chromium")

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}


def is_youtube_url(value: str) -> bool:
    try:
        p = urlparse(value.strip())
        return (
            p.scheme in ("http", "https")
            and (p.hostname or "").lower() in YOUTUBE_HOSTS
        )
    except Exception:
        return False


def check_api_key() -> bool:
    if not API_KEY:
        return True
    supplied = request.headers.get("X-Converter-Key", "")
    return secrets.compare_digest(supplied, API_KEY)


def safe_filename(value: str) -> str:
    value = re.sub(r"[^\w\-. ]+", "", value, flags=re.UNICODE).strip()
    value = re.sub(r"\s+", " ", value)
    return (value[:120] or "mp3zivo-conversie")


def make_ydl_options(workdir: Path, fmt: str) -> dict:
    common = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "restrictfilenames": True,
        "outtmpl": str(workdir / "%(title)s.%(ext)s"),
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "continuedl": True,
        "overwrites": True,
        "max_filesize": MAX_FILE_MB * 1024 * 1024,
        # Current YouTube deployments may require both JS challenges and
        # PO tokens. The WPC provider can mint PO tokens in Chromium.
        "extractor_args": {
            "youtube": {
                "player_client": [
                    "web_embedded",
                    "web_safari",
                    "default",
                ],
            },
            "youtubepot-wpc": {
                "browser_path": [CHROME_BIN],
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
        "format": (
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]"
            "/best[ext=mp4]/best"
        ),
        "merge_output_format": "mp4",
    }


@app.get("/")
def health():
    return jsonify({
        "service": "MP3ZIVO conversion server",
        "status": "ok",
        "youtube": True,
        "yt_dlp": yt_dlp.version.__version__,
        "chromium": shutil.which("chromium") or CHROME_BIN,
    })


@app.post("/convert")
def convert():
    if not check_api_key():
        return jsonify({"error": "Ongeldige API key."}), 401

    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    fmt = str(data.get("format", "mp3")).lower().strip()

    if not is_youtube_url(url):
        return jsonify({
            "error": "Gebruik een geldige YouTube-link."
        }), 400

    if fmt not in {"mp3", "mp4"}:
        return jsonify({
            "error": "Formaat moet mp3 of mp4 zijn."
        }), 400

    workdir = Path(tempfile.mkdtemp(prefix="mp3zivo-"))

    try:
        opts = make_ydl_options(workdir, fmt)

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

            duration = info.get("duration")
            if duration and duration > MAX_SECONDS:
                raise ValueError(
                    f"Video is langer dan de toegestane {MAX_SECONDS} seconden."
                )

            title = safe_filename(info.get("title", "mp3zivo-conversie"))

        output_files = [
            p for p in workdir.iterdir()
            if p.is_file() and p.suffix.lower() == "." + fmt
        ]

        if not output_files:
            raise RuntimeError(
                "De download is uitgevoerd maar het uitvoerbestand is niet gevonden."
            )

        output = max(output_files, key=lambda p: p.stat().st_mtime)

        mimetype = "audio/mpeg" if fmt == "mp3" else "video/mp4"

        response = send_file(
            output,
            mimetype=mimetype,
            as_attachment=True,
            download_name=f"{title}.{fmt}",
            max_age=0,
        )

        # Render/Flask sends the file before the request ends; cleanup is
        # intentionally not performed before send_file has consumed it.
        return response

    except yt_dlp.utils.DownloadError as exc:
        raw = str(exc)

        if "Sign in to confirm you're not a bot" in raw:
            msg = (
                "YouTube heeft deze aanvraag als geautomatiseerd verkeer "
                "herkend. Probeer een andere video of later opnieuw."
            )
        elif "Video unavailable" in raw or "This video is unavailable" in raw:
            msg = "Deze YouTube-video is niet beschikbaar voor de server."
        elif "age-restricted" in raw.lower() or "sign in to confirm your age" in raw.lower():
            msg = "Deze video vereist leeftijdsverificatie en kan niet worden verwerkt."
        elif "Private video" in raw:
            msg = "Privévideo's kunnen niet worden verwerkt."
        elif "HTTP Error 403" in raw:
            msg = (
                "YouTube weigert de mediastream (HTTP 403). "
                "Probeer een andere video."
            )
        else:
            msg = f"YouTube kon de video niet verwerken: {raw[:450]}"

        return jsonify({"error": msg}), 502

    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    except Exception as exc:
        return jsonify({
            "error": f"Conversie mislukt: {str(exc)[:450]}"
        }), 500

    finally:
        # Keep the temp directory until Flask has finished serving the file.
        # A small delayed cleanup is safer than deleting it immediately.
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
