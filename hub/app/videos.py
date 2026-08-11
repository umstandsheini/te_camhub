"""
Video library under the [Videos] SMB share (/backingfiles/Videos, see
setup/pi/configure-samba.sh) -- lists files dropped there over the network
and, on demand, remuxes/transcodes them into something the Tesla browser's
HTML5 <video> can actually play (H.264/AAC in an MP4 container with the
moov atom moved to the front for streaming before the file is fully sent).

Two-step because most drop-in files (MKV rips in particular) already carry
H.264/AAC streams -- just repackaging them into .mp4 (ffmpeg -c copy) is
near-instant. Only the rarer incompatible-codec case (HEVC, DTS/AC3, ...)
needs a real transcode, which is slow on a Pi 4 -- runs as a background
job so the UI can show progress instead of the request just hanging.
"""
import os, subprocess, threading, hashlib

ROOT = "/backingfiles/Videos"
CACHE_DIR = os.path.join(ROOT, ".hub_cache")
VIDEO_EXT = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".wmv")
SAFE_EXT = (".mp4", ".webm")  # containers the browser can already play as-is

_jobs = {}  # name -> {"state": "working"|"done"|"error", "error": str|None}
_guard = threading.Lock()


def _safe(rel):
    rel = (rel or "").replace("\\", "/").lstrip("/")
    full = os.path.normpath(os.path.join(ROOT, rel))
    base = os.path.normpath(ROOT)
    if full != base and not full.startswith(base + os.sep):
        raise ValueError("path outside root")
    return full


def _cache_path(name):
    h = hashlib.sha1(name.encode()).hexdigest()[:16]
    return os.path.join(CACHE_DIR, h + ".mp4")


def _is_ready(name):
    return name.lower().endswith(SAFE_EXT) or os.path.isfile(_cache_path(name))


def list_videos():
    os.makedirs(ROOT, exist_ok=True)
    out = []
    for name in sorted(os.listdir(ROOT), key=str.lower):
        if name.startswith("."):
            continue
        full = os.path.join(ROOT, name)
        if not os.path.isfile(full) or not name.lower().endswith(VIDEO_EXT):
            continue
        st = os.stat(full)
        out.append({"name": name, "size": st.st_size, "mtime": st.st_mtime,
                     "ready": _is_ready(name)})
    return out


def status(name):
    with _guard:
        job = _jobs.get(name)
        if job:
            return dict(job)
    try:
        full = _safe(name)
    except ValueError:
        return {"state": "error", "error": "ungültiger Pfad"}
    if not os.path.isfile(full):
        return {"state": "error", "error": "Datei nicht gefunden"}
    return {"state": "done" if _is_ready(name) else "idle"}


def prepare(name):
    """Kick off (if needed) a background remux/transcode to a browser-safe
    MP4 cached under .hub_cache/. Returns immediately -- poll status()."""
    full = _safe(name)
    if not os.path.isfile(full):
        return {"ok": False, "error": "Datei nicht gefunden"}
    if _is_ready(name):
        return {"ok": True, "state": "done"}
    with _guard:
        job = _jobs.get(name)
        if job and job["state"] == "working":
            return {"ok": True, "state": "working"}
        _jobs[name] = {"state": "working", "error": None}
    threading.Thread(target=_prepare_worker, args=(name, full), daemon=True).start()
    return {"ok": True, "state": "working"}


def _prepare_worker(name, full):
    os.makedirs(CACHE_DIR, exist_ok=True)
    dest = _cache_path(name)
    tmp = dest + ".tmp"
    try:
        # Fast path: just repackage the existing streams into MP4, no
        # re-encode -- works whenever the source is already H.264/AAC
        # (the common case for MKV rips), takes seconds not minutes.
        # -f mp4 is required, not cosmetic: the output path ends in
        # ".mp4.tmp" (so a failed attempt never leaves a half-written file
        # at the real cache path) and ffmpeg can't infer a container from
        # that extension on its own -- without -f it aborts immediately
        # with "Unable to find a suitable output format", every time.
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", full, "-c", "copy", "-movflags", "+faststart", "-f", "mp4", tmp],
            capture_output=True, text=True, timeout=1800)
        if r.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            # Source codec isn't MP4-safe (HEVC, DTS/AC3, ...) -- real
            # transcode. Slow (real-time-ish on a Pi 4), so this is the
            # rare fallback, not the default path.
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", full, "-c:v", "libx264", "-preset", "veryfast",
                 "-crf", "23", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                 "-f", "mp4", tmp],
                capture_output=True, text=True, timeout=10800)
            if r.returncode != 0:
                raise RuntimeError((r.stderr or "ffmpeg fehlgeschlagen").strip()[-300:])
        os.replace(tmp, dest)
        with _guard:
            _jobs[name] = {"state": "done", "error": None}
    except Exception as e:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        with _guard:
            _jobs[name] = {"state": "error", "error": str(e)[:300]}


def resolve(name):
    """Absolute path to a browser-playable file for this video, or None if
    it still needs prepare() first."""
    full = _safe(name)
    if not os.path.isfile(full):
        return None
    if name.lower().endswith(SAFE_EXT):
        return full
    cp = _cache_path(name)
    return cp if os.path.isfile(cp) else None


def delete_cache(name):
    """Drop a cached remux/transcode so the next prepare() redoes it --
    e.g. after replacing the source file with a different cut."""
    cp = _cache_path(name)
    if os.path.isfile(cp):
        os.remove(cp)
    with _guard:
        _jobs.pop(name, None)
