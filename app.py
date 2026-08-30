import os
import re
import shutil
import tempfile
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, request, send_file
import yt_dlp

app = Flask(__name__)

# Optional protection for your WordPress plugin.
# If API_KEY is set on Render, requests must send:
# Authorization: Bearer YOUR_KEY
API_KEY = os.getenv("API_KEY", "").strip()

MAX_DOWNLOAD_MB = int(os.getenv("MAX_DOWNLOAD_MB", "500"))
MAX_DOWNLOAD_BYTES = MAX_DOWNLOAD_MB * 1024 * 1024

DIRECT_EXTENSIONS = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".flac": "audio/flac",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36"
)


def authorized():
    if not API_KEY:
        return True

    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {API_KEY}"


def is_http_url(value):
    try:
        p = urlparse(value)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def is_youtube_url(url):
    try:
        host = (urlparse(url).hostname or "").lower()
        return (
            host == "youtube.com"
            or host.endswith(".youtube.com")
            or host == "youtu.be"
            or host.endswith(".youtu.be")
        )
    except Exception:
        return False


def extension_from_url(url):
    path = urlparse(url).path.lower()
    for ext in DIRECT_EXTENSIONS:
        if path.endswith(ext):
            return ext
    return ""


def safe_filename(name, fallback="media"):
    name = re.sub(r"[^\w\-. ]+", "_", name, flags=re.UNICODE).strip()
    return (name[:180] or fallback)


def download_direct(url, workdir):
    """
    Download a real media file directly from an HTTP(S) URL.
    This deliberately does not attempt to defeat authentication,
    DRM, signed-link expiry, robots controls, or other access controls.
    """
    response = requests.get(
        url,
        stream=True,
        timeout=(15, 120),
        headers={"User-Agent": USER_AGENT},
        allow_redirects=True,
    )
    response.raise_for_status()

    content_type = (response.headers.get("Content-Type") or "").split(";")[0].lower()
    content_length = response.headers.get("Content-Length")

    if content_length:
        try:
            if int(content_length) > MAX_DOWNLOAD_BYTES:
                raise ValueError(f"Bestand is groter dan {MAX_DOWNLOAD_MB} MB.")
        except ValueError as exc:
            if "groter" in str(exc):
                raise

    ext = extension_from_url(response.url) or extension_from_url(url)

    if not ext:
        ext_by_type = {
            "video/mp4": ".mp4",
            "video/webm": ".webm",
            "video/quicktime": ".mov",
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
            "audio/wav": ".wav",
            "audio/ogg": ".ogg",
            "audio/flac": ".flac",
        }
        ext = ext_by_type.get(content_type, "")

    # Reject HTML pages masquerading as media.
    if content_type in ("text/html", "application/xhtml+xml") and not ext:
        raise ValueError(
            "De URL verwijst naar een webpagina en niet rechtstreeks naar een mediabestand."
        )

    if not ext:
        raise ValueError(
            "Kon het bestandstype niet bepalen. Gebruik een directe .mp4, .webm, "
            ".mp3, .m4a, .wav, .ogg of .flac URL."
        )

    output = os.path.join(workdir, "download" + ext)
    total = 0

    with open(output, "wb") as f:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                raise ValueError(f"Bestand is groter dan {MAX_DOWNLOAD_MB} MB.")
            f.write(chunk)

    if total == 0:
        raise ValueError("De server gaf een leeg bestand terug.")

    return output, content_type or DIRECT_EXTENSIONS.get(ext, "application/octet-stream")


def download_youtube(url, workdir):
    """
    Use yt-dlp for publicly accessible YouTube media.
    This does not bypass login requirements, DRM, private videos,
    paid content, or other access restrictions.
    """
    output_template = os.path.join(workdir, "%(title).150B.%(ext)s")

    opts = {
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "retries": 2,
        "fragment_retries": 2,
        "socket_timeout": 30,
        "max_filesize": MAX_DOWNLOAD_BYTES,
        "merge_output_format": "mp4",
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        # Current yt-dlp versions can use an installed JS runtime when needed.
        "js_runtimes": {"deno": {}},
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            requested = ydl.prepare_filename(info)
    except Exception as exc:
        message = str(exc)

        # Turn common yt-dlp failures into useful messages for the website.
        lowered = message.lower()
        if "login" in lowered or "sign in" in lowered:
            raise ValueError(
                "YouTube vereist voor deze video een ingelogde sessie. "
                "Deze server gebruikt geen gebruikerscookies."
            )
        if "private" in lowered:
            raise ValueError("Deze YouTube-video is privé en kan niet publiek worden opgehaald.")
        if "age" in lowered and ("restrict" in lowered or "confirm" in lowered):
            raise ValueError(
                "Deze YouTube-video heeft een leeftijdsbeperking en vereist aanvullende toegang."
            )
        raise ValueError("YouTube kon deze openbare video niet ophalen. Probeer een andere openbare video.")

    candidates = []
    if os.path.exists(requested):
        candidates.append(requested)

    # yt-dlp may merge into .mp4 even when the prepared filename has another extension.
    candidates.extend(
        str(p)
        for p in Path(workdir).iterdir()
        if p.is_file() and p.name != "download"
    )

    if not candidates:
        raise ValueError("YouTube-download is gestart maar er kwam geen bestand beschikbaar.")

    output = max(candidates, key=lambda p: os.path.getsize(p))
    if os.path.getsize(output) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"Bestand is groter dan {MAX_DOWNLOAD_MB} MB.")

    return output, "video/mp4" if output.lower().endswith(".mp4") else "application/octet-stream"


@app.get("/")
def home():
    return jsonify({
        "ok": True,
        "service": "MP3ZIVO conversion server",
        "message": "Converter API is online.",
    })


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.post("/convert")
@app.post("/api/convert")
def convert():
    if not authorized():
        return jsonify({"ok": False, "error": "Ongeldige API key."}), 401

    data = request.get_json(silent=True) or request.form
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"ok": False, "error": "Geen URL opgegeven."}), 400

    if not is_http_url(url):
        return jsonify({"ok": False, "error": "Gebruik een geldige http(s)-URL."}), 400

    # Do not allow localhost/private-network fetching through this endpoint.
    host = (urlparse(url).hostname or "").lower()
    blocked_hosts = {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "169.254.169.254",
    }
    if host in blocked_hosts or host.endswith(".local"):
        return jsonify({"ok": False, "error": "Deze URL is niet toegestaan."}), 400

    workdir = tempfile.mkdtemp(prefix="mp3zivo-")

    try:
        if is_youtube_url(url):
            filepath, content_type = download_youtube(url, workdir)
        else:
            filepath, content_type = download_direct(url, workdir)

        filename = safe_filename(os.path.basename(filepath), "media")

        # send_file will stream the result to the caller.
        response = send_file(
            filepath,
            mimetype=content_type,
            as_attachment=True,
            download_name=filename,
            conditional=True,
        )

        # Clean temporary files after the response has been sent.
        @response.call_on_close
        def cleanup():
            shutil.rmtree(workdir, ignore_errors=True)

        return response

    except requests.HTTPError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        status = getattr(exc.response, "status_code", None)
        if status == 403:
            msg = "De bron weigert de serveraanvraag (HTTP 403)."
        elif status == 404:
            msg = "Het mediabestand bestaat niet meer (HTTP 404)."
        else:
            msg = f"De bron kon niet worden opgehaald (HTTP {status or 'fout'})."
        return jsonify({"ok": False, "error": msg}), 502

    except ValueError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        return jsonify({"ok": False, "error": str(exc)}), 400

    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        app.logger.exception("Conversion failed")
        return jsonify({
            "ok": False,
            "error": "De conversie is mislukt. Controleer de URL en probeer opnieuw.",
        }), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
