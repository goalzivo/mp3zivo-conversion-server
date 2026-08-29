import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from flask import Flask, after_this_request, jsonify, request, send_file

app = Flask(__name__)

API_KEY = os.environ.get("MP3ZIVO_API_KEY", "").strip()
MAX_FILE_MB = int(os.environ.get("MAX_FILE_MB", "500"))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}

YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def json_error(message, status=400):
    return jsonify({"error": message}), status


def is_youtube_url(value):
    try:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https"):
            return False
        host = (parsed.hostname or "").lower()
        return host in YOUTUBE_HOSTS
    except Exception:
        return False


def check_api_key():
    if not API_KEY:
        return True
    supplied = request.headers.get("X-Converter-Key", "")
    return supplied == API_KEY


def safe_filename(name, fallback):
    name = re.sub(r'[\\/:*?"<>|]+', "_", name or "")
    name = re.sub(r"\s+", " ", name).strip()
    name = name[:120].strip(" .")
    return name or fallback


def find_output(folder, format_name):
    files = [
        p for p in Path(folder).iterdir()
        if p.is_file() and not p.name.endswith((".part", ".ytdl"))
    ]
    if not files:
        return None

    wanted = ".mp3" if format_name == "mp3" else ".mp4"
    matching = [p for p in files if p.suffix.lower() == wanted]
    if matching:
        return max(matching, key=lambda p: p.stat().st_size)

    # yt-dlp can occasionally leave a container with a different extension.
    return max(files, key=lambda p: p.stat().st_size)


@app.get("/")
def home():
    return jsonify({
        "service": "MP3ZIVO conversion server",
        "status": "ok",
        "endpoint": "/convert",
        "youtube": True,
    })


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/convert")
def convert():
    if not check_api_key():
        return json_error("Ongeldige API key.", 401)

    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    fmt = str(data.get("format", "mp3")).lower().strip()

    if not url:
        return json_error("Geen URL opgegeven.")
    if fmt not in {"mp3", "mp4"}:
        return json_error("Format moet mp3 of mp4 zijn.")
    if not is_youtube_url(url):
        return json_error(
            "Deze server accepteert momenteel alleen openbare YouTube-links."
        )

    temp_dir = tempfile.mkdtemp(prefix="mp3zivo-")

    @after_this_request
    def cleanup(response):
        shutil.rmtree(temp_dir, ignore_errors=True)
        return response

    output_template = str(Path(temp_dir) / "%(title).120s [%(id)s].%(ext)s")

    common = {
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "max_filesize": MAX_FILE_BYTES,
        # YouTube's current JS challenges require a supported runtime.
        "js_runtimes": {"deno": {}},
        # Deno can fetch the EJS challenge solver from npm.
        "remote_components": ["ejs:npm"],
    }

    if fmt == "mp3":
        options = {
            **common,
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }
    else:
        options = {
            **common,
            "format": "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/best",
            "merge_output_format": "mp4",
        }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            title = safe_filename(
                info.get("title"),
                "mp3zivo-conversie",
            )
            ydl.prepare_filename(info)

        output = find_output(temp_dir, fmt)
        if output is None or not output.exists():
            return json_error("Conversie is voltooid maar er is geen uitvoerbestand gevonden.", 500)

        size = output.stat().st_size
        if size <= 0:
            return json_error("Het uitvoerbestand is leeg.", 500)
        if size > MAX_FILE_BYTES:
            return json_error(
                f"Het bestand is groter dan de limiet van {MAX_FILE_MB} MB.",
                413,
            )

        extension = "mp3" if fmt == "mp3" else "mp4"
        download_name = f"{title}.{extension}"

        mimetype = "audio/mpeg" if fmt == "mp3" else "video/mp4"
        return send_file(
            output,
            mimetype=mimetype,
            as_attachment=True,
            download_name=download_name,
            max_age=0,
        )

    except yt_dlp.utils.DownloadError as exc:
        message = str(exc).strip()
        # Keep the response useful without dumping a huge internal traceback.
        if "Sign in to confirm" in message:
            message = "YouTube vereist voor deze video een ingelogde sessie; deze server gebruikt geen accountcookies."
        elif "Video unavailable" in message or "This video is not available" in message:
            message = "Deze YouTube-video is niet beschikbaar voor de server."
        elif "Private video" in message:
            message = "Deze video is privé en kan niet zonder geautoriseerde toegang worden verwerkt."
        elif "age" in message.lower() and "confirm" in message.lower():
            message = "Deze video heeft leeftijdsbeperking en kan niet zonder geautoriseerde toegang worden verwerkt."

        return json_error(message, 502)

    except Exception as exc:
        app.logger.exception("MP3ZIVO conversion failed")
        return json_error(f"Interne conversiefout: {str(exc)[:300]}", 500)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
