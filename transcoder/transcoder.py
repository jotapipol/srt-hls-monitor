#!/usr/bin/env python3
import os, subprocess, time, shutil, requests

API        = os.getenv("MEDIAMTX_API",    "http://localhost:9997")
HLS_SRC    = os.getenv("HLS_SRC_BASE",    "http://localhost:8888")
OUTPUT_DIR = os.getenv("OUTPUT_DIR",      "/data/hls_preview")
POLL       = int(os.getenv("POLL_INTERVAL",   "3"))
BITRATE    = os.getenv("PREVIEW_BITRATE", "1500k")
AUDIO_BR   = os.getenv("PREVIEW_AUDIO",   "128k")
PRESET     = os.getenv("FFMPEG_PRESET",   "veryfast")

procs = {}  # path → Popen

def start(path):
    out = f"{OUTPUT_DIR}/{path}"
    os.makedirs(out, exist_ok=True)
    threads = os.getenv("FFMPEG_THREADS", "2")
    cmd = [
        "ffmpeg", "-y",
        "-threads", threads,
        "-i", f"{HLS_SRC}/{path}/index.m3u8",
        "-c:v", "libx264", "-preset", PRESET,
        "-b:v", BITRATE, "-maxrate", BITRATE, "-bufsize", "3000k",
        "-vf", "scale=-2:480",
        "-c:a", "aac", "-b:a", AUDIO_BR,
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "6",
        "-hls_flags", "delete_segments+append_list",
        "-hls_segment_filename", f"{out}/%04d.ts",
        f"{out}/index.m3u8"
    ]
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs[path] = p
    print(f"[start] {path} pid={p.pid}", flush=True)

def stop(path):
    p = procs.pop(path, None)
    if p:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
    out = f"{OUTPUT_DIR}/{path}"
    if os.path.exists(out):
        shutil.rmtree(out)
    print(f"[stop]  {path}", flush=True)

os.makedirs(OUTPUT_DIR, exist_ok=True)
print(f"Transcoder iniciado — API={API}  src={HLS_SRC}  out={OUTPUT_DIR}  poll={POLL}s", flush=True)

while True:
    try:
        items  = requests.get(f"{API}/v3/paths/list", timeout=10).json().get("items", [])
        active = {p["name"] for p in items if p.get("ready")}

        for path in active - set(procs):
            start(path)
        # Solo matar si la API respondió correctamente y dice que el stream ya no existe
        for path in set(procs) - active:
            stop(path)

        # Reiniciar procesos que murieron solos
        for path, p in list(procs.items()):
            if p.poll() is not None:
                print(f"[restart] {path} salió con código {p.returncode}", flush=True)
                del procs[path]
                if path in active:
                    start(path)

    except Exception as e:
        # API timeout u otro error: NO tocar los procesos ffmpeg que están corriendo
        print(f"[poll error, manteniendo procesos activos] {e}", flush=True)

    time.sleep(POLL)
