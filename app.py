#!/usr/bin/env python3
# Minimal Flask UI for playable, no-reencode video slicing using ffmpeg
import os
import uuid
import math
import shutil
import threading
import subprocess
from time import time
import zipfile
from flask import Flask, request, redirect, url_for, Response, render_template, send_from_directory, abort

app = Flask(__name__)
app.config["UPLOAD_DIR"] = os.path.abspath("./uploads")
app.config["OUTPUT_DIR"] = os.path.abspath("./outputs")
os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
os.makedirs(app.config["OUTPUT_DIR"], exist_ok=True)

JOBS = {}  # job_id -> dict(status, pct, msg, started, finished, input_path, out_pattern, parts)

def require_tools():
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("ffmpeg and ffprobe must be on PATH")

def ffprobe_duration(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        text=True
    ).strip()
    return float(out)

def ffprobe_bitrate_or_calc(path):
    try:
        br = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=bit_rate",
             "-of", "default=nw=1:nk=1", path],
            text=True
        ).strip()
        br = int(br)
        if br > 0:
            return br
    except subprocess.CalledProcessError:
        pass
    dur = ffprobe_duration(path)
    size = os.path.getsize(path)
    return (size * 8) / max(dur, 1e-6)

def estimate_seg_time_secs(path, chunk_mb):
    bitrate = ffprobe_bitrate_or_calc(path)  # bits/s
    target_bits = chunk_mb * 1024 * 1024 * 8
    return max(1.0, target_bits / bitrate)

def run_job(job_id, input_path, chunk_mb, pattern):
    require_tools()
    try:
        dur = ffprobe_duration(input_path)
        seg_time = estimate_seg_time_secs(input_path, chunk_mb)
    except Exception as e:
        j = JOBS[job_id]
        j["status"] = "error"
        j["msg"] = f"Metadata error: {e}"
        j["pct"] = 0.0
        j["finished"] = time()
        return

    base = os.path.splitext(os.path.basename(input_path))[0]
    ext = os.path.splitext(input_path)[1] or ".mp4"
    out_pattern = pattern or os.path.join(app.config["OUTPUT_DIR"], f"{base}_part%03d{ext}")

    j = JOBS[job_id]
    j["status"] = "running"
    j["msg"] = f"~{math.ceil(dur/seg_time)} parts @ {seg_time:.2f}s/part"
    j["out_pattern"] = out_pattern

    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error",
        "-i", input_path,
        "-c", "copy", "-map", "0",
        "-f", "segment",
        "-segment_time", f"{seg_time:.3f}",
        "-reset_timestamps", "1",
        "-progress", "pipe:1",
        out_pattern
    ]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        for line in proc.stdout:
            line = line.strip()
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k == "out_time_ms":
                out_s = int(v) / 1e6
                j["pct"] = min(1.0, out_s / max(dur, 1e-6))
            elif k == "progress" and v == "end":
                j["pct"] = 1.0
        ret = proc.wait()
        if ret != 0:
            j["status"] = "error"
            j["msg"] = proc.stderr.read() or f"ffmpeg exited {ret}"
        else:
            j["status"] = "done"
            j["msg"] = "Completed"
            # List produced files
            dirpath = os.path.dirname(out_pattern) or "."
            prefix = os.path.basename(out_pattern).split("%")[0]
            j["parts"] = sorted(
                [f for f in os.listdir(dirpath) if f.startswith(prefix) and f.endswith(ext)]
            )
        j["finished"] = time()
    except Exception as e:
        j["status"] = "error"
        j["msg"] = str(e)
        j["finished"] = time()

@app.route("/", methods=["GET"])
def index():
    return render_template(
        "index.html",
        job=None,
        job_ids=list(JOBS.keys())[-5:],  # last 5 job IDs
        jobs=JOBS
    )

@app.route("/start", methods=["POST"])
def start():
    file = request.files.get("file")
    try:
        chunk_mb = int(request.form.get("chunk_mb", "0"))
        assert chunk_mb > 0
    except Exception:
        abort(400, "chunk_mb must be a positive integer")

    pattern = request.form.get("pattern", "").strip() or None

    if file and file.filename:
        up_name = f"{uuid.uuid4().hex}_{os.path.basename(file.filename)}"
        dest = os.path.join(app.config["UPLOAD_DIR"], up_name)
        file.save(dest)
        input_path = dest
    else:
        abort(400, "Please upload a file")

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        "status": "queued",
        "pct": 0.0,
        "msg": "",
        "started": time(),
        "finished": None,
        "input_path": input_path,
        "out_pattern": pattern or "",
        "parts": []
    }
    t = threading.Thread(target=run_job, args=(job_id, input_path, chunk_mb, pattern), daemon=True)
    t.start()
    return redirect(url_for("job", job_id=job_id))

@app.route("/job/<job_id>")
def job(job_id):
    job = JOBS.get(job_id)
    if not job:
        abort(404)
    return render_template("index.html", job=job, job_id=job_id)

@app.route("/progress/<job_id>")
def progress_stream(job_id):
    if job_id not in JOBS:
        abort(404)
    def gen():
        while True:
            j = JOBS.get(job_id)
            if not j:
                break
            payload = {
                "status": j["status"],
                "pct": float(j["pct"]),
                "msg": j.get("msg", ""),
                "files": j.get("parts", []) if j["status"] in ("done", "error") else []
            }
            yield f"data: {__import__('json').dumps(payload)}\n\n"
            if j["status"] in ("done", "error"):
                break
            import time as _t; _t.sleep(0.15)
    return Response(gen(), mimetype="text/event-stream")

@app.route("/download/<job_id>")
def download(job_id):
    j = JOBS.get(job_id)
    if not j or j["status"] != "done":
        abort(404)
    f = request.args.get("f", "")
    dirpath = os.path.dirname(j["out_pattern"]) or app.config["OUTPUT_DIR"]
    fpath = os.path.join(dirpath, f)
    if not os.path.isfile(fpath):
        abort(404)
    return send_from_directory(dirpath, f, as_attachment=True)

@app.route("/download_all/<job_id>")
def download_all(job_id):
    j = JOBS.get(job_id)
    if not j or j["status"] != "done":
        abort(404)
    dirpath = os.path.dirname(j["out_pattern"]) or app.config["OUTPUT_DIR"]
    parts = j.get("parts", [])
    if not parts:
        abort(404)
    # Create a zip archive named with the job_id to avoid collisions.
    zip_name = f"{job_id}_parts.zip"
    zip_path = os.path.join(dirpath, zip_name)
    try:
        # Recreate zip each time to ensure freshness.
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except OSError:
                pass
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for fname in parts:
                fpath = os.path.join(dirpath, fname)
                if os.path.isfile(fpath):
                    zf.write(fpath, arcname=fname)
    except Exception:
        abort(500)
    return send_from_directory(dirpath, zip_name, as_attachment=True)

if __name__ == "__main__":
    # Run:  pip install flask  &&  python app.py
    # Open: http://127.0.0.1:5000
    app.run(host="127.0.0.1", port=5000, debug=False)