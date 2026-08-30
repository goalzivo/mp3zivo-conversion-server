import ipaddress
import mimetypes
import os
import re
import socket
import shutil
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

from flask import Flask, jsonify, request, send_file

app = Flask(__name__)

MAX_DOWNLOAD_MB = int(os.getenv("MAX_DOWNLOAD_MB", "1000"))
MAX_DOWNLOAD_BYTES = MAX_DOWNLOAD_MB * 1024 * 1024
API_KEY = os.getenv("API_KEY", "").strip()

# Common downloadable attachment/media types.
ALLOWED_TYPES = {
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/x-7z-compressed": ".7z",
    "application/x-rar-compressed": ".rar",
    "application/gzip": ".gz",
    "application/x-tar": ".tar",
    "application/json": ".json",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "text/xml": ".xml",
    "application/xml": ".xml",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/flac": ".flac",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


def check_auth():
    if not API_KEY:
        return True
    return request.headers.get("Authorization", "") == f"Bearer {API_KEY}"


def safe_filename(name, fallback="download"):
    name = unquote(name or "").strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip(". ")
    return (name[:180] or fallback)


def is_public_ip(hostname):
    """Resolve the hostname and reject loopback/private/link-local/reserved targets."""
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def validate_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Gebruik een geldige openbare http(s)-URL.")

    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("Deze URL is niet toegestaan.")

    if not is_public_ip(host):
        raise ValueError("Deze URL verwijst niet naar een openbaar internetadres.")


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def parse_content_disposition(value):
    if not value:
        return None

    # RFC 5987 filename*=UTF-8''...
    match = re.search(r"filename\*\s*=\s*(?:UTF-8''|utf-8'')([^;]+)", value)
    if match:
        return safe_filename(match.group(1))

    match = re.search(r'filename\s*=\s*"([^"]+)"', value, re.I)
    if match:
        return safe_filename(match.group(1))

    match = re.search(r"filename\s*=\s*([^;]+)", value, re.I)
    if match:
        return safe_filename(match.group(1))

    return None


def filename_from_url(url):
    path = urlparse(url).path
    name = Path(unquote(path)).name
    return safe_filename(name, "download")


def extension_for_type(content_type):
    return ALLOWED_TYPES.get(content_type) or mimetypes.guess_extension(content_type or "")


def download_public_file(url, workdir):
    validate_url(url)

    opener = build_opener(SafeRedirectHandler)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
    }

    req = Request(url, headers=headers, method="GET")
    response = opener.open(req, timeout=60)

    content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    disposition_name = parse_content_disposition(
        response.headers.get("Content-Disposition", "")
    )

    length = response.headers.get("Content-Length")
    if length:
        try:
            if int(length) > MAX_DOWNLOAD_BYTES:
                raise ValueError(
                    f"Dit bestand is groter dan de limiet van {MAX_DOWNLOAD_MB} MB."
                )
        except ValueError as exc:
            if "groter" in str(exc):
                response.close()
                raise

    # Never save an HTML page as if it were an attachment.
    if content_type in {"text/html", "application/xhtml+xml"}:
        response.close()
        raise ValueError(
            "Deze URL is een webpagina, geen direct downloadbaar bestand. "
            "Gebruik de rechtstreekse downloadlink naar het bestand."
        )

    name = disposition_name or filename_from_url(response.geturl())
    ext = Path(name).suffix.lower()

    if not ext:
        ext = extension_for_type(content_type) or ""

    if not ext:
        ext = ".bin"

    if not Path(name).suffix:
        name += ext

    output = Path(workdir) / ("download" + ext)

    total = 0
    try:
        with output.open("wb") as f:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise ValueError(
                        f"Dit bestand is groter dan de limiet van {MAX_DOWNLOAD_MB} MB."
                    )
                f.write(chunk)
    finally:
        response.close()

    if total == 0:
        raise ValueError("De bron gaf een leeg bestand terug.")

    return str(output), name, content_type or "application/octet-stream"


@app.get("/")
def home():
    return jsonify({
        "ok": True,
        "service": "MP3ZIVO download server",
        "supports": [
            "video",
            "audio",
            "images",
            "pdf",
            "documents",
            "spreadsheets",
            "presentations",
            "archives",
            "text",
            "other public files",
        ],
    })


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.post("/convert")
@app.post("/api/convert")
def convert():
    if not check_auth():
        return jsonify({"ok": False, "error": "Ongeldige API key."}), 401

    data = request.get_json(silent=True) or request.form
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"ok": False, "error": "Geen URL opgegeven."}), 400

    workdir = tempfile.mkdtemp(prefix="mp3zivo-")

    try:
        filepath, filename, content_type = download_public_file(url, workdir)

        response = send_file(
            filepath,
            mimetype=content_type,
            as_attachment=True,
            download_name=filename,
            conditional=True,
        )

        @response.call_on_close
        def cleanup():
            shutil.rmtree(workdir, ignore_errors=True)

        return response

    except ValueError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        return jsonify({"ok": False, "error": str(exc)}), 400

    except Exception as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        app.logger.exception("Download failed")
        return jsonify({
            "ok": False,
            "error": f"Download mislukt: {type(exc).__name__}"
        }), 502


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
