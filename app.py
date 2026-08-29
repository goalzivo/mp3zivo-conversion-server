import os
import re
import socket
import ipaddress
import tempfile
import subprocess
import urllib.parse
import urllib.request
from flask import Flask, request, jsonify, Response

app = Flask(__name__)

API_KEY = os.environ.get("CONVERTER_API_KEY", "")
MAX_BYTES = int(os.environ.get("MAX_INPUT_BYTES", str(200 * 1024 * 1024)))
TIMEOUT = int(os.environ.get("DOWNLOAD_TIMEOUT", "45"))
CONVERT_TIMEOUT = int(os.environ.get("CONVERT_TIMEOUT", "300"))

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}

def is_public_host(host):
    try:
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global:
                return False
        return True
    except Exception:
        return False

def validate_url(url):
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("Gebruik een geldige http(s)-URL.")
    host = p.hostname.lower().rstrip(".")
    if host not in YOUTUBE_HOSTS and not is_public_host(host):
        raise ValueError("Deze URL is niet toegestaan.")
    return url, host

def download_direct(url, target):
    req = urllib.request.Request(url, headers={"User-Agent": "MP3ZIVO/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        total = int(r.headers.get("Content-Length") or 0)
        if total and total > MAX_BYTES:
            raise ValueError("Bestand is te groot.")
        written = 0
        with open(target, "wb") as f:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_BYTES:
                    raise ValueError("Bestand is te groot.")
                f.write(chunk)

def run_ytdlp(url, fmt, out_dir):
    # Only public URLs are accepted. No DRM/circumvention is performed.
    if fmt == "mp3":
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--no-warnings",
            "--restrict-filenames",
            "-x",
            "--audio-format", "mp3",
            "--audio-quality", "192K",
            "-o", os.path.join(out_dir, "output.%(ext)s"),
            url,
        ]
        expected = os.path.join(out_dir, "output.mp3")
    else:
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--no-warnings",
            "--restrict-filenames",
            "-f", "bv*+ba/b",
            "--merge-output-format", "mp4",
            "-o", os.path.join(out_dir, "output.%(ext)s"),
            url,
        ]
        expected = os.path.join(out_dir, "output.mp4")

    p = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=CONVERT_TIMEOUT
    )
    if p.returncode != 0 or not os.path.exists(expected):
        detail = (p.stderr or p.stdout or "yt-dlp kon de bron niet verwerken.")[-1500:]
        raise RuntimeError(detail)
    return expected

@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "mp3zivo-converter", "youtube": True})

@app.post("/convert")
def convert():
    if API_KEY and request.headers.get("X-Converter-Key") != API_KEY:
        return jsonify({"error": "Niet geautoriseerd."}), 401

    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    fmt = str(data.get("format", "mp3")).lower()

    if fmt not in ("mp3", "mp4"):
        return jsonify({"error": "Formaat moet mp3 of mp4 zijn."}), 400

    try:
        url, host = validate_url(url)

        with tempfile.TemporaryDirectory() as d:
            parsed = urllib.parse.urlparse(url)
            is_youtube = host in YOUTUBE_HOSTS

            if is_youtube:
                out = run_ytdlp(url, fmt, d)
            else:
                src = os.path.join(d, "source")
                out = os.path.join(d, "output." + fmt)
                download_direct(url, src)

                if fmt == "mp3":
                    cmd = [
                        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-i", src, "-vn", "-c:a", "libmp3lame",
                        "-b:a", "192k", out
                    ]
                else:
                    cmd = [
                        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-i", src, "-c:v", "libx264", "-c:a", "aac",
                        "-movflags", "+faststart", out
                    ]

                p = subprocess.run(
                    cmd, capture_output=True, timeout=CONVERT_TIMEOUT
                )
                if p.returncode != 0 or not os.path.exists(out):
                    detail = p.stderr.decode("utf-8", "ignore")[-1000:]
                    return jsonify({
                        "error": "Conversie mislukt.",
                        "detail": detail
                    }), 422

            data_bytes = open(out, "rb").read()
            mime = "audio/mpeg" if fmt == "mp3" else "video/mp4"
            filename = "mp3zivo-conversie." + fmt

            return Response(
                data_bytes,
                status=200,
                mimetype=mime,
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Cache-Control": "no-store",
                },
            )

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Conversie duurde te lang."}), 504
    except Exception as e:
        return jsonify({
            "error": "Kon de bron niet verwerken.",
            "detail": str(e)[:1500]
        }), 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
