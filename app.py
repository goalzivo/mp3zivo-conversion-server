import os, re, socket, ipaddress, tempfile, subprocess, urllib.parse, urllib.request
from flask import Flask, request, jsonify, Response

app = Flask(__name__)
API_KEY = os.environ.get("CONVERTER_API_KEY", "")
MAX_BYTES = int(os.environ.get("MAX_INPUT_BYTES", str(200 * 1024 * 1024)))
TIMEOUT = int(os.environ.get("DOWNLOAD_TIMEOUT", "45"))

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
        raise ValueError("Gebruik een geldige openbare http(s)-URL.")
    if not is_public_host(p.hostname):
        raise ValueError("Deze URL is niet toegestaan.")
    return url

def download(url, target):
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

@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "mp3zivo-converter"})

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
        validate_url(url)
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "source")
            out = os.path.join(d, "output." + fmt)
            download(url, src)

            if fmt == "mp3":
                cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                       "-i", src, "-vn", "-c:a", "libmp3lame", "-b:a", "192k", out]
            else:
                cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                       "-i", src, "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", out]

            p = subprocess.run(cmd, capture_output=True, timeout=180)
            if p.returncode != 0 or not os.path.exists(out):
                return jsonify({"error": "Conversie mislukt.", "detail": p.stderr.decode("utf-8", "ignore")[-1000:]}), 422

            filename = "mp3zivo-conversie." + fmt
            data_bytes = open(out, "rb").read()
            mime = "audio/mpeg" if fmt == "mp3" else "video/mp4"
            return Response(data_bytes, status=200, mimetype=mime,
                            headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                     "Cache-Control": "no-store"})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": "Kon de bron niet verwerken.", "detail": str(e)[:500]}), 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
