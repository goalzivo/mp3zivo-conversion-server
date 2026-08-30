import os
import json
import uuid
import shutil
import tempfile
import subprocess
from pathlib import Path

from flask import Flask, request, jsonify, send_file, render_template_string, after_this_request

app = Flask(__name__)

# Allow the WordPress front-end to call this public conversion endpoint.
# Set ALLOWED_ORIGIN in Render to your WordPress domain for tighter security.
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Expose-Headers"] = "Content-Disposition"
    return response

@app.route("/convert", methods=["OPTIONS"])
def convert_options():
    return ("", 204)

app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "500")) * 1024 * 1024

ALLOWED_INPUT_EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".oga", ".opus",
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".mpeg",
    ".mpg", ".ts", ".mts", ".m2ts", ".3gp", ".3g2", ".asf", ".flv",
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"
}

OUTPUTS = {
    "mp3": {"label": "MP3 — audio", "ext": ".mp3"},
    "m4a": {"label": "M4A — audio", "ext": ".m4a"},
    "wav": {"label": "WAV — audio", "ext": ".wav"},
    "flac": {"label": "FLAC — lossless audio", "ext": ".flac"},
    "ogg": {"label": "OGG — audio", "ext": ".ogg"},
    "opus": {"label": "OPUS — audio", "ext": ".opus"},
    "aac": {"label": "AAC — audio", "ext": ".aac"},
    "mp4": {"label": "MP4 — video", "ext": ".mp4"},
    "webm": {"label": "WebM — video", "ext": ".webm"},
    "mkv": {"label": "MKV — video", "ext": ".mkv"},
    "mov": {"label": "MOV — video", "ext": ".mov"},
    "avi": {"label": "AVI — video", "ext": ".avi"},
}

HTML = r"""
<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MP3ZIVO — Media converter</title>
<meta name="description" content="Converteer je eigen audio- en videobestanden rechtstreeks in je browser naar populaire mediaformaten.">
<style>
:root{--bg:#04131d;--panel:#0c2431;--panel2:#0a1d28;--line:#21485d;--text:#f3f7fa;--muted:#a7bdc9;--green:#45f20e;--green2:#2fd10a}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 25%,#0c2937 0,#04131d 42%,#03111a 100%);color:var(--text);font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;min-height:100vh}
header{height:70px;border-bottom:1px solid #163241;background:#041019cc;backdrop-filter:blur(10px);display:flex;align-items:center;justify-content:space-between;padding:0 max(24px,calc((100% - 1140px)/2))}
.logo{font-size:23px;font-weight:900;letter-spacing:-1px}.logo span{color:var(--green)}nav a{color:#b7c8d0;text-decoration:none;margin-left:26px;font-size:14px}
main{max-width:1140px;margin:0 auto;padding:68px 20px 55px;display:grid;grid-template-columns:1.08fr .92fr;gap:54px;align-items:center}
.kicker{color:var(--green);font-size:13px;font-weight:800;letter-spacing:1.2px;margin-bottom:16px}.hero h1{font-size:68px;line-height:.95;letter-spacing:-3.8px;margin:0 0 25px}.hero h1 span{color:var(--green)}.hero p{font-size:18px;line-height:1.6;color:#d2e0e7;max-width:650px}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:28px}.card{background:#0a1d28cc;border:1px solid #183748;border-radius:15px;padding:17px 16px;min-height:112px}.card b{display:block;font-size:16px;margin-bottom:8px}.card small{color:#9db4c0;line-height:1.45}
.panel{background:linear-gradient(145deg,#102c3b,#081c27);border:1px solid #25536a;border-radius:21px;padding:29px;box-shadow:0 20px 70px #0004}.panel h2{margin:0 0 7px;font-size:25px}.panel>p{color:#b3c7d1;margin:0 0 20px;line-height:1.45}
.drop{border:2px dashed #34667d;background:#071b26;border-radius:16px;padding:27px 20px;text-align:center;cursor:pointer;transition:.2s}.drop:hover,.drop.drag{border-color:var(--green);background:#0a2730}.drop strong{display:block;font-size:18px;margin-bottom:6px}.drop span{color:#9bb3bf;font-size:14px}.drop input{display:none}
.file-info{display:none;margin-top:12px;padding:12px 14px;border:1px solid #245165;border-radius:12px;background:#071a24;color:#cde0e8;font-size:14px;justify-content:space-between;gap:10px}.file-info.show{display:flex}.file-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.file-size{color:#8faab7;white-space:nowrap}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:15px}.field label{display:block;color:#9fb7c2;font-size:13px;margin-bottom:7px}.field select{width:100%;padding:13px;border-radius:11px;border:1px solid #28546a;background:#061a25;color:#f1f7fa;font-size:15px}
.check{margin-top:15px;border:1px solid #205e3c;background:#09251f;border-radius:13px;padding:14px;display:flex;gap:11px;align-items:flex-start}.check input{margin-top:3px;accent-color:var(--green)}.check label{font-size:13px;line-height:1.45;color:#cfe0e5}.check a{color:#bceeb0}
button{width:100%;margin-top:15px;border:0;border-radius:12px;background:var(--green);color:#041108;font-weight:900;font-size:15px;padding:15px;cursor:pointer}button:hover{background:#5bff2a}button:disabled{opacity:.45;cursor:not-allowed}
.progress{display:none;margin-top:16px}.progress.show{display:block}.progress-top{display:flex;justify-content:space-between;color:#a9c0ca;font-size:13px;margin-bottom:7px}.bar{height:8px;border-radius:20px;background:#09202c;overflow:hidden}.bar i{display:block;height:100%;width:0;background:var(--green);transition:width .2s}.status{margin-top:12px;color:#a9c0ca;font-size:13px;text-align:center}
.notice{margin-top:16px;color:#829ba7;font-size:12px;line-height:1.5}.result{display:none;margin-top:16px;padding:15px;border-radius:13px;border:1px solid #2a6a45;background:#09231c}.result.show{display:block}.result a{display:block;text-align:center;background:var(--green);color:#041108;font-weight:900;text-decoration:none;border-radius:10px;padding:13px}.error{color:#ffb4b4}
footer{max-width:1140px;margin:0 auto;padding:0 20px 35px;color:#7893a0;font-size:12px}footer a{color:#9db7c3;margin-right:16px}
@media(max-width:900px){main{grid-template-columns:1fr;padding-top:45px;gap:35px}.hero h1{font-size:55px}.cards{grid-template-columns:1fr 1fr}.panel{padding:22px}}
@media(max-width:560px){header{height:60px;padding:0 18px}nav{display:none}.hero h1{font-size:46px;letter-spacing:-2.5px}.hero p{font-size:16px}.cards,.row{grid-template-columns:1fr}.panel{border-radius:16px;padding:18px}}
</style>
</head>
<body>
<header><div class="logo">MP3<span>ZIVO</span></div><nav><a href="#converter">Converter</a><a href="/privacy">Privacy</a><a href="/terms">Voorwaarden</a><a href="/dmca">DMCA</a></nav></header>
<main id="converter">
<section class="hero">
<div class="kicker">SNELLE MEDIA-TOOLS</div>
<h1>Converteer je<br><span>eigen media.</span></h1>
<p>Upload een audio-, video- of afbeeldingsbestand dat je rechtmatig mag gebruiken en zet het om naar een populair mediaformaat. Geen URL-downloaders en geen DRM-omzeiling.</p>
<div class="cards">
<div class="card"><b>⚡ Snel</b><small>Eén duidelijke upload- en conversieworkflow.</small></div>
<div class="card"><b>🔒 Privé</b><small>Bestanden worden tijdelijk verwerkt en daarna verwijderd.</small></div>
<div class="card"><b>📱 Mobiel</b><small>Werkt op desktop, tablet en telefoon.</small></div>
</div>
</section>
<section class="panel">
<h2>Media converter</h2>
<p>Upload je eigen bestand en kies het gewenste uitvoerformaat.</p>
<form id="form">
<label class="drop" id="drop">
<strong>📁 Kies een bestand</strong>
<span>of sleep het hierheen · maximaal {{ max_mb }} MB</span>
<input id="file" name="file" type="file" accept="audio/*,video/*,image/*,.mp3,.mp4,.m4a,.wav,.flac,.ogg,.opus,.webm,.mkv,.mov,.avi">
</label>
<div class="file-info" id="fileInfo"><span class="file-name" id="fileName"></span><span class="file-size" id="fileSize"></span></div>
<div class="row">
<div class="field"><label for="format">Omzetten naar</label><select id="format" name="format">{% for key, item in outputs.items() %}<option value="{{ key }}">{{ item.label }}</option>{% endfor %}</select></div>
<div class="field"><label for="quality">Kwaliteit</label><select id="quality" name="quality"><option value="high">Hoog</option><option value="standard" selected>Standaard</option><option value="small">Klein bestand</option></select></div>
</div>
<div class="check"><input id="rights" type="checkbox" required><label for="rights">Ik heb toestemming/rechten om dit bestand te verwerken en te converteren. Ik gebruik deze dienst niet om auteursrecht, DRM of andere technische beveiligingen te omzeilen.</label></div>
<button id="submit" type="submit" disabled>Converteren →</button>
<div class="progress" id="progress"><div class="progress-top"><span id="status">Uploaden…</span><span id="percent">0%</span></div><div class="bar"><i id="bar"></i></div></div>
<div class="result" id="result"><a id="download">Download je bestand</a></div>
<div class="notice">Bestanden zijn tijdelijk nodig voor de conversie. Deel geen gevoelige bestanden. Door de upload te starten accepteer je de <a href="/terms" style="color:#9db7c3">voorwaarden</a>.</div>
</form>
</section>
</main>
<footer>© {{ year }} MP3ZIVO · <a href="/privacy">Privacy</a><a href="/terms">Voorwaarden</a><a href="/dmca">Copyright / DMCA</a></footer>
<script>
const file=document.getElementById('file'), drop=document.getElementById('drop'), rights=document.getElementById('rights'), submit=document.getElementById('submit');
const info=document.getElementById('fileInfo'), nameEl=document.getElementById('fileName'), sizeEl=document.getElementById('fileSize');
const form=document.getElementById('form'), progress=document.getElementById('progress'), bar=document.getElementById('bar'), percent=document.getElementById('percent'), status=document.getElementById('status'), result=document.getElementById('result'), download=document.getElementById('download');
function fmt(n){if(n<1024*1024)return (n/1024).toFixed(1)+' KB';return (n/1024/1024).toFixed(1)+' MB'}
function refresh(){const f=file.files[0];submit.disabled=!(f&&rights.checked);if(f){info.classList.add('show');nameEl.textContent=f.name;sizeEl.textContent=fmt(f.size)}else info.classList.remove('show')}
file.addEventListener('change',refresh);rights.addEventListener('change',refresh);
['dragenter','dragover'].forEach(e=>drop.addEventListener(e,x=>{x.preventDefault();drop.classList.add('drag')}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,x=>{x.preventDefault();drop.classList.remove('drag')}));
drop.addEventListener('drop',e=>{if(e.dataTransfer.files.length){file.files=e.dataTransfer.files;refresh()}});
form.addEventListener('submit',e=>{
e.preventDefault(); const f=file.files[0]; if(!f)return;
submit.disabled=true; result.classList.remove('show'); progress.classList.add('show'); bar.style.width='0%'; percent.textContent='0%'; status.textContent='Uploaden…';
const xhr=new XMLHttpRequest(); xhr.open('POST','/convert'); xhr.responseType='blob';
xhr.upload.onprogress=e=>{if(e.lengthComputable){const p=Math.round(e.loaded/e.total*35);bar.style.width=p+'%';percent.textContent=p+'%'}};
xhr.onloadstart=()=>{status.textContent='Bestand verwerken…';};
xhr.onload=()=>{
if(xhr.status>=200&&xhr.status<300){
bar.style.width='100%';percent.textContent='100%';status.textContent='Klaar!';
const cd=xhr.getResponseHeader('Content-Disposition')||'';let filename='converted-file';const m=cd.match(/filename="?([^"]+)"?/i);if(m)filename=m[1];
const url=URL.createObjectURL(xhr.response);download.href=url;download.download=filename;result.classList.add('show');
}else{
const reader=new FileReader();reader.onload=()=>{let msg='Conversie mislukt.';try{msg=JSON.parse(reader.result).error||msg}catch(e){}status.innerHTML='<span class="error">'+msg+'</span>';bar.style.width='0%';percent.textContent='';};reader.readAsText(xhr.response);
}
submit.disabled=false;
};
xhr.onerror=()=>{status.innerHTML='<span class="error">Netwerkfout. Probeer opnieuw.</span>';submit.disabled=false};
const data=new FormData(form);data.append('file',f);data.delete('rights');
xhr.send(data);
});
</script>
</body>
</html>
"""

POLICY_PAGES = {
"privacy": """
<!doctype html><html lang="nl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Privacy — MP3ZIVO</title><style>body{font-family:system-ui;max-width:800px;margin:50px auto;padding:0 20px;line-height:1.7;background:#06151f;color:#eaf2f5}a{color:#53f51b}</style>
<h1>Privacybeleid</h1><p>MP3ZIVO verwerkt geüploade bestanden uitsluitend om de door de gebruiker aangevraagde mediaconversie uit te voeren.</p>
<h2>Tijdelijke bestanden</h2><p>Een upload wordt tijdelijk op de server opgeslagen tijdens de conversie. Na afloop worden het bronbestand en het gegenereerde bestand verwijderd. Er wordt geen openbare downloadpagina voor je bestand gemaakt.</p>
<h2>Technische gegevens</h2><p>Zoals bij de meeste websites kunnen noodzakelijke technische gegevens zoals IP-adres, browserinformatie en foutlogs tijdelijk door de hostingprovider worden verwerkt voor beveiliging en werking.</p>
<h2>Advertenties</h2><p>Als op deze website advertenties worden geplaatst, kunnen advertentiepartners gegevens verwerken volgens hun eigen beleid en de toepasselijke toestemming-/privacyregels.</p>
<p><a href="/">← Terug naar converter</a></p></html>
""",
"terms": """
<!doctype html><html lang="nl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Voorwaarden — MP3ZIVO</title><style>body{font-family:system-ui;max-width:800px;margin:50px auto;padding:0 20px;line-height:1.7;background:#06151f;color:#eaf2f5}a{color:#53f51b}</style>
<h1>Gebruiksvoorwaarden</h1><p>Je mag MP3ZIVO alleen gebruiken voor bestanden waarvoor je de benodigde rechten, toestemming of een andere wettelijke grondslag hebt.</p>
<h2>Niet toegestaan</h2><ul><li>Inbreuk op auteursrechten of andere rechten van derden.</li><li>Het omzeilen of verwijderen van DRM of andere technische beveiligingen.</li><li>Illegale, schadelijke of kwaadaardige bestanden.</li><li>Misbruik van de dienst, waaronder pogingen om de server of andere gebruikers te schaden.</li></ul>
<h2>Beschikbaarheid</h2><p>Conversies zijn afhankelijk van bestandstype, codecs, bestandsgrootte en beschikbare servercapaciteit. Niet ieder formaat kan met ieder ander formaat worden geconverteerd.</p>
<p><a href="/">← Terug naar converter</a></p></html>
""",
"dmca": """
<!doctype html><html lang="nl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Copyright / DMCA — MP3ZIVO</title><style>body{font-family:system-ui;max-width:800px;margin:50px auto;padding:0 20px;line-height:1.7;background:#06151f;color:#eaf2f5}a{color:#53f51b}</style>
<h1>Copyright / DMCA</h1><p>MP3ZIVO is bedoeld voor legitieme mediaconversie van bestanden die de gebruiker rechtmatig mag verwerken. De dienst is niet bedoeld voor het downloaden van streamingcontent, het omzeilen van DRM of het faciliteren van auteursrechtsinbreuk.</p>
<p>Als je van mening bent dat materiaal of functionaliteit op deze website jouw rechten schendt, neem dan contact op met de websitebeheerder met voldoende informatie om de claim te beoordelen.</p>
<p><a href="/">← Terug naar converter</a></p></html>
"""
}

def safe_original_name(name):
    name = Path(name or "upload").name
    # Keep Unicode filenames but remove control characters and path separators.
    name = "".join(ch for ch in name if ord(ch) >= 32 and ch not in "/\\")
    return name[:180] or "upload"

def run(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=int(os.getenv("FFMPEG_TIMEOUT", "600")))

def probe(path):
    p = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    if p.returncode != 0:
        raise ValueError("Het bestand kon niet als media worden gelezen.")
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        raise ValueError("Ongeldige media-informatie ontvangen.")

def build_command(src, dst, fmt, quality, has_video, has_audio):
    # All arguments are passed as an argv list; no shell is used.
    if fmt == "mp3":
        bitrate = {"high":"320k","standard":"192k","small":"128k"}[quality]
        return ["ffmpeg","-y","-i",str(src),"-vn","-map","0:a:0","-c:a","libmp3lame","-b:a",bitrate,str(dst)]
    if fmt == "m4a":
        bitrate = {"high":"256k","standard":"192k","small":"128k"}[quality]
        return ["ffmpeg","-y","-i",str(src),"-vn","-map","0:a:0","-c:a","aac","-b:a",bitrate,"-movflags","+faststart",str(dst)]
    if fmt == "wav":
        return ["ffmpeg","-y","-i",str(src),"-vn","-map","0:a:0","-c:a","pcm_s16le",str(dst)]
    if fmt == "flac":
        return ["ffmpeg","-y","-i",str(src),"-vn","-map","0:a:0","-c:a","flac",str(dst)]
    if fmt == "ogg":
        bitrate = {"high":"256k","standard":"160k","small":"96k"}[quality]
        return ["ffmpeg","-y","-i",str(src),"-vn","-map","0:a:0","-c:a","libvorbis","-b:a",bitrate,str(dst)]
    if fmt == "opus":
        bitrate = {"high":"160k","standard":"128k","small":"80k"}[quality]
        return ["ffmpeg","-y","-i",str(src),"-vn","-map","0:a:0","-c:a","libopus","-b:a",bitrate,str(dst)]
    if fmt == "aac":
        bitrate = {"high":"256k","standard":"192k","small":"128k"}[quality]
        return ["ffmpeg","-y","-i",str(src),"-vn","-map","0:a:0","-c:a","aac","-b:a",bitrate,str(dst)]
    if fmt == "mp4":
        crf = {"high":"18","standard":"23","small":"28"}[quality]
        if has_video:
            return ["ffmpeg","-y","-i",str(src),"-map","0:v:0","-map","0:a:0?","-c:v","libx264","-preset","veryfast","-crf",crf,"-c:a","aac","-b:a","160k","-movflags","+faststart",str(dst)]
        # Audio-only input: create a simple black video track so MP4 remains a valid video file.
        return ["ffmpeg","-y","-f","lavfi","-i","color=c=black:s=1280x720:r=25","-i",str(src),"-shortest","-map","0:v:0","-map","1:a:0","-c:v","libx264","-preset","veryfast","-tune","stillimage","-pix_fmt","yuv420p","-c:a","aac","-b:a","160k","-movflags","+faststart",str(dst)]
    if fmt == "webm":
        if not has_video:
            raise ValueError("WebM is een videoformaat; kies MP3, OGG, OPUS, M4A, WAV of FLAC voor audio.")
        crf = {"high":"30","standard":"34","small":"40"}[quality]
        return ["ffmpeg","-y","-i",str(src),"-map","0:v:0","-map","0:a:0?","-c:v","libvpx-vp9","-crf",crf,"-b:v","0","-c:a","libopus","-b:a","128k",str(dst)]
    if fmt == "mkv":
        if not has_video:
            return ["ffmpeg","-y","-i",str(src),"-vn","-c:a","copy",str(dst)]
        return ["ffmpeg","-y","-i",str(src),"-c:v","libx264","-preset","veryfast","-crf","23","-c:a","aac","-b:a","160k",str(dst)]
    if fmt == "mov":
        if not has_video:
            raise ValueError("MOV is een videoformaat; kies een audioformaat voor een audiobestand.")
        return ["ffmpeg","-y","-i",str(src),"-map","0:v:0","-map","0:a:0?","-c:v","libx264","-preset","veryfast","-crf","23","-c:a","aac","-b:a","160k","-movflags","+faststart",str(dst)]
    if fmt == "avi":
        if not has_video:
            raise ValueError("AVI is een videoformaat; kies een audioformaat voor een audiobestand.")
        return ["ffmpeg","-y","-i",str(src),"-map","0:v:0","-map","0:a:0?","-c:v","mpeg4","-q:v","4","-c:a","aac","-b:a","160k",str(dst)]
    raise ValueError("Dit uitvoerformaat wordt niet ondersteund.")

@app.get("/")
def index():
    return render_template_string(HTML, outputs=OUTPUTS, max_mb=int(os.getenv("MAX_UPLOAD_MB","500")), year=__import__("datetime").datetime.now().year)

@app.get("/health")
def health():
    try:
        p = subprocess.run(["ffmpeg","-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        ok = p.returncode == 0
    except Exception:
        ok = False
    return jsonify({"status":"ok" if ok else "ffmpeg_missing"})

@app.get("/<page>")
def page(page):
    if page in POLICY_PAGES:
        return POLICY_PAGES[page]
    return ("Not found",404)

@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": f"Bestand te groot. Maximum is {app.config['MAX_CONTENT_LENGTH']//1024//1024} MB."}), 413

@app.post("/convert")
def convert():
    uploaded = request.files.get("file")
    fmt = (request.form.get("format") or "").lower().strip()
    quality = (request.form.get("quality") or "standard").lower().strip()

    if not uploaded or not uploaded.filename:
        return jsonify({"error":"Kies eerst een bestand."}),400
    if fmt not in OUTPUTS:
        return jsonify({"error":"Ongeldig uitvoerformaat."}),400
    if quality not in {"high","standard","small"}:
        return jsonify({"error":"Ongeldige kwaliteitsinstelling."}),400

    original = safe_original_name(uploaded.filename)
    suffix = Path(original).suffix.lower()
    if suffix not in ALLOWED_INPUT_EXTENSIONS:
        return jsonify({"error":"Dit bestandstype wordt niet ondersteund. Gebruik een gangbaar audio-, video- of afbeeldingsbestand."}),415

    work = Path(tempfile.mkdtemp(prefix="mp3zivo-"))
    src = work / ("input" + suffix)
    dst = work / ("converted" + OUTPUTS[fmt]["ext"])
    try:
        uploaded.save(src)
        info = probe(src)
        streams = info.get("streams", [])
        has_video = any(s.get("codec_type") == "video" for s in streams)
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        if not has_video and not has_audio:
            raise ValueError("Er is geen audio- of videostream gevonden in dit bestand.")
        if fmt in {"mp3","m4a","wav","flac","ogg","opus","aac"} and not has_audio:
            raise ValueError("Dit bestand bevat geen audiotrack.")
        cmd = build_command(src, dst, fmt, quality, has_video, has_audio)
        result = run(cmd)
        if result.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
            # Do not expose the complete ffmpeg command or internal paths.
            tail = (result.stderr or "").strip().splitlines()
            detail = tail[-1] if tail else "FFmpeg kon het bestand niet converteren."
            raise ValueError(f"Conversie mislukt: {detail[:220]}")

        download_name = f"{Path(original).stem[:120]}-converted{OUTPUTS[fmt]['ext']}"
        @after_this_request
        def cleanup(response):
            shutil.rmtree(work, ignore_errors=True)
            return response
        return send_file(dst, as_attachment=True, download_name=download_name, mimetype=None, max_age=0)
    except subprocess.TimeoutExpired:
        shutil.rmtree(work, ignore_errors=True)
        return jsonify({"error":"De conversie duurde te lang. Probeer een kleiner bestand of een eenvoudiger formaat."}),408
    except ValueError as e:
        shutil.rmtree(work, ignore_errors=True)
        return jsonify({"error":str(e)}),400
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        app.logger.exception("Conversion error")
        return jsonify({"error":"Er ging iets mis tijdens de conversie. Probeer het opnieuw met een ander bestand."}),500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","10000")))
