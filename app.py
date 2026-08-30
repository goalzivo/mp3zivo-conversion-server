import os, json, uuid, shutil, tempfile, subprocess, threading, time
from pathlib import Path
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_file, make_response

app = Flask(__name__)
MAX_MB = int(os.getenv("MAX_UPLOAD_MB", "500"))
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")
FFMPEG_TIMEOUT = int(os.getenv("FFMPEG_TIMEOUT", "900"))

INPUTS = {".mp3",".wav",".flac",".aac",".m4a",".ogg",".oga",".opus",".mp4",".m4v",".mov",".mkv",".webm",".avi",".wmv",".mpeg",".mpg",".ts",".mts",".m2ts",".3gp",".3g2",".asf",".flv",".jpg",".jpeg",".png",".webp",".bmp",".gif"}

OUTPUTS = {
 "mp3":(".mp3","audio/mpeg"), "m4a":(".m4a","audio/mp4"), "wav":(".wav","audio/wav"),
 "flac":(".flac","audio/flac"), "ogg":(".ogg","audio/ogg"), "opus":(".opus","audio/opus"),
 "aac":(".aac","audio/aac"), "mp4":(".mp4","video/mp4"), "webm":(".webm","video/webm"),
 "mkv":(".mkv","video/x-matroska"), "mov":(".mov","video/quicktime"), "avi":(".avi","video/x-msvideo"),
 "jpg":(".jpg","image/jpeg"), "png":(".png","image/png"), "webp":(".webp","image/webp"), "gif":(".gif","image/gif")
}
jobs = {}
lock = threading.Lock()

@app.after_request
def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Expose-Headers"] = "Content-Disposition, Content-Length"
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"ok"})

@app.route("/convert", methods=["OPTIONS"])
def convert_options():
    return ("",204)

def safe_name(name):
    n = Path(name or "upload").name
    n = "".join(c for c in n if ord(c) >= 32 and c not in "/\\")
    return n[:180] or "upload"

def run_probe(path):
    p = subprocess.run(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(path)],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    if p.returncode != 0: raise ValueError("Het bestand kon niet als media worden gelezen.")
    return json.loads(p.stdout)

def duration(info):
    try: return max(0.001, float(info.get("format",{}).get("duration") or 0))
    except: return 0.0

def has_stream(info, kind):
    return any(s.get("codec_type")==kind for s in info.get("streams",[]))

def build(src, dst, fmt, q, hv, ha):
    crf = {"high":"18","standard":"23","small":"28"}[q]
    if fmt=="mp3":
        return ["ffmpeg","-y","-i",str(src),"-vn","-map","0:a:0","-c:a","libmp3lame","-b:a",{"high":"320k","standard":"192k","small":"128k"}[q],"-progress","pipe:1","-nostats",str(dst)]
    if fmt=="m4a":
        return ["ffmpeg","-y","-i",str(src),"-vn","-map","0:a:0","-c:a","aac","-b:a",{"high":"256k","standard":"192k","small":"128k"}[q],"-movflags","+faststart","-progress","pipe:1","-nostats",str(dst)]
    if fmt=="wav":
        return ["ffmpeg","-y","-i",str(src),"-vn","-map","0:a:0","-c:a","pcm_s16le","-progress","pipe:1","-nostats",str(dst)]
    if fmt=="flac":
        return ["ffmpeg","-y","-i",str(src),"-vn","-map","0:a:0","-c:a","flac","-progress","pipe:1","-nostats",str(dst)]
    if fmt=="ogg":
        return ["ffmpeg","-y","-i",str(src),"-vn","-map","0:a:0","-c:a","libvorbis","-b:a",{"high":"256k","standard":"160k","small":"96k"}[q],"-progress","pipe:1","-nostats",str(dst)]
    if fmt=="opus":
        return ["ffmpeg","-y","-i",str(src),"-vn","-map","0:a:0","-c:a","libopus","-b:a",{"high":"160k","standard":"128k","small":"80k"}[q],"-progress","pipe:1","-nostats",str(dst)]
    if fmt=="aac":
        return ["ffmpeg","-y","-i",str(src),"-vn","-map","0:a:0","-c:a","aac","-b:a",{"high":"256k","standard":"192k","small":"128k"}[q],"-progress","pipe:1","-nostats",str(dst)]
    if fmt=="mp4":
        if hv:
            cmd=["ffmpeg","-y","-i",str(src),"-map","0:v:0","-map","0:a:0?","-c:v","libx264","-preset","ultrafast","-crf",crf,"-c:a","aac","-b:a","128k","-movflags","+faststart"]
        else:
            # Audio -> video: use a lightweight static 640x360 canvas at 15 fps.
            # This is much faster on a small Render instance than 1280x720/25 fps.
            cmd=["ffmpeg","-y","-f","lavfi","-i","color=c=black:s=640x360:r=15","-i",str(src),"-shortest","-map","0:v:0","-map","1:a:0","-c:v","libx264","-preset","ultrafast","-tune","stillimage","-crf","30","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k","-movflags","+faststart"]
        return cmd+["-progress","pipe:1","-nostats",str(dst)]
    if fmt=="webm":
        if not hv: raise ValueError("WebM is een videoformaat; kies een audioformaat.")
        return ["ffmpeg","-y","-i",str(src),"-map","0:v:0","-map","0:a:0?","-c:v","libvpx-vp9","-deadline","realtime","-cpu-used","8","-crf",{"high":"30","standard":"34","small":"40"}[q],"-b:v","0","-c:a","libopus","-b:a","96k","-progress","pipe:1","-nostats",str(dst)]
    if fmt=="mkv":
        if not hv: return ["ffmpeg","-y","-i",str(src),"-vn","-c:a","copy","-progress","pipe:1","-nostats",str(dst)]
        return ["ffmpeg","-y","-i",str(src),"-c:v","libx264","-preset","ultrafast","-crf","23","-c:a","aac","-b:a","128k","-progress","pipe:1","-nostats",str(dst)]
    if fmt=="mov":
        if not hv:
            # Audio -> MOV still needs a video stream. Use a lightweight static canvas.
            return ["ffmpeg","-y","-f","lavfi","-i","color=c=black:s=640x360:r=15","-i",str(src),"-shortest","-map","0:v:0","-map","1:a:0","-c:v","libx264","-preset","ultrafast","-tune","stillimage","-crf","30","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k","-movflags","+faststart","-progress","pipe:1","-nostats",str(dst)]
        return ["ffmpeg","-y","-i",str(src),"-map","0:v:0","-map","0:a:0?","-c:v","libx264","-preset","ultrafast","-crf","23","-c:a","aac","-b:a","128k","-movflags","+faststart","-progress","pipe:1","-nostats",str(dst)]
    if fmt=="avi":
        if not hv: raise ValueError("AVI is een videoformaat; kies een audioformaat.")
        return ["ffmpeg","-y","-i",str(src),"-map","0:v:0","-map","0:a:0?","-c:v","mpeg4","-q:v","5","-c:a","aac","-b:a","128k","-progress","pipe:1","-nostats",str(dst)]
    if fmt in {"jpg","png","webp","gif"}:
        if not hv: raise ValueError("Dit bestand bevat geen beeld.")
        if fmt=="jpg": codec=["-frames:v","1","-q:v","3"]
        elif fmt=="png": codec=["-frames:v","1","-c:v","png"]
        elif fmt=="webp": codec=["-frames:v","1","-c:v","libwebp","-q:v","80"]
        else: codec=["-vf","fps=10,scale=640:-1:flags=lanczos","-c:v","gif"]
        return ["ffmpeg","-y","-i",str(src),*codec,"-progress","pipe:1","-nostats",str(dst)]
    raise ValueError("Dit uitvoerformaat wordt niet ondersteund.")

def cleanup_later(job_id, seconds=600):
    def worker():
        time.sleep(seconds)
        with lock:
            j=jobs.get(job_id)
            if not j: return
            shutil.rmtree(j["work"], ignore_errors=True)
            jobs.pop(job_id,None)
    threading.Thread(target=worker,daemon=True).start()

def convert_worker(job_id):
    with lock: j=jobs[job_id]; j["status"]="processing"; j["message"]="FFmpeg verwerkt je bestand."
    try:
        info=run_probe(j["src"])
        hv=has_stream(info,"video"); ha=has_stream(info,"audio")
        if not hv and not ha: raise ValueError("Er is geen audio- of videostream gevonden.")
        fmt=j["format"]
        if fmt in {"mp3","m4a","wav","flac","ogg","opus","aac"} and not ha:
            raise ValueError("Dit bestand bevat geen audiotrack.")
        if fmt in {"jpg","png","webp","gif"} and not hv:
            raise ValueError("Dit bestand bevat geen beeld.")
        dur=duration(info)
        cmd=build(j["src"],j["dst"],fmt,j["quality"],hv,ha)
        proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
        last=0
        while True:
            line=proc.stdout.readline()
            if line:
                if line.startswith("out_time_ms="):
                    try:
                        out_ms=int(line.split("=",1)[1].strip())
                        if dur>0: last=max(0,min(99,(out_ms/1000000)/dur*100))
                    except: pass
                with lock:
                    j["progress"]=round(last,1)
            elif proc.poll() is not None: break
        stderr=proc.stderr.read()
        if proc.returncode!=0 or not j["dst"].exists() or j["dst"].stat().st_size==0:
            tail=(stderr or "").strip().splitlines()
            raise ValueError("Conversie mislukt. Controleer het bestand of kies een ander uitvoerformaat.")
        with lock:
            j["progress"]=100; j["status"]="done"; j["message"]="Klaar"; j["filename"]=j["download_name"]
        cleanup_later(job_id)
    except subprocess.TimeoutExpired:
        with lock: j["status"]="error"; j["error"]="De conversie duurde te lang. Probeer een kleiner bestand."
        cleanup_later(job_id,60)
    except Exception as e:
        with lock: j["status"]="error"; j["error"]=str(e)[:300]
        cleanup_later(job_id,60)

@app.post("/convert")
def convert():
    f=request.files.get("file"); fmt=(request.form.get("format") or "").lower().strip(); q=(request.form.get("quality") or "standard").lower().strip()
    if not f or not f.filename: return jsonify(error="Kies eerst een bestand."),400
    if fmt not in OUTPUTS: return jsonify(error="Ongeldig uitvoerformaat."),400
    if q not in {"high","standard","small"}: return jsonify(error="Ongeldige kwaliteitsinstelling."),400
    original=safe_name(f.filename); suffix=Path(original).suffix.lower()
    if suffix not in INPUTS: return jsonify(error="Dit bestandstype wordt niet ondersteund."),415
    work=Path(tempfile.mkdtemp(prefix="mp3zivo-")); src=work/("input"+suffix); dst=work/("converted"+OUTPUTS[fmt][0])
    try:
        f.save(src)
        job_id=uuid.uuid4().hex
        name=f"{Path(original).stem[:100]}-converted{OUTPUTS[fmt][0]}"
        with lock:
            jobs[job_id]={"status":"queued","progress":0,"message":"Wachten…","work":str(work),"src":src,"dst":dst,"format":fmt,"quality":q,"filename":name,"download_name":name}
        threading.Thread(target=convert_worker,args=(job_id,),daemon=True).start()
        return jsonify(job_id=job_id,status="queued"),202
    except Exception:
        shutil.rmtree(work,ignore_errors=True); return jsonify(error="De upload kon niet worden verwerkt."),500

@app.get("/progress/<job_id>")
def progress(job_id):
    with lock: j=jobs.get(job_id)
    if not j: return jsonify(error="Deze conversie bestaat niet meer of is verlopen."),404
    return jsonify(status=j["status"],progress=j.get("progress",0),message=j.get("message",""),error=j.get("error"),filename=j.get("filename"))

@app.get("/download/<job_id>")
def download(job_id):
    with lock: j=jobs.get(job_id)
    if not j or j["status"]!="done": return jsonify(error="Het bestand is nog niet klaar."),404
    if not j["dst"].exists(): return jsonify(error="Het tijdelijke bestand bestaat niet meer."),404
    return send_file(j["dst"],as_attachment=True,download_name=j["filename"],mimetype=OUTPUTS[j["format"]][1],max_age=0)

@app.errorhandler(413)
def too_large(_): return jsonify(error=f"Bestand te groot. Maximum is {MAX_MB} MB."),413

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))
