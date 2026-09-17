"""
Hub updates from GitHub releases (Diagnose page).

A release of the repository carries te_camhub-<tag>.tar.gz (the source tree,
VERSION = <tag>) and its .sha256, both built by .github/workflows/release.yml.
check() asks GitHub for the latest release and compares its tag with
/opt/teslacam-hub/VERSION, which hub/install.sh writes; server.py checks every
CHECK_EVERY seconds, so the Diagnose page and the navigation can say when
something new is out.

Installing (explicit click, home WiFi only -- same reasons as osupdate.py):
  1. backup: the Hub's code (/opt/teslacam-hub, /root/te_camhub, /root/bin),
     its systemd units, the teslausb config, the WiFi profiles, the TLS key
     and the Hub's state (vault, trips, logs; not the regenerable derived/
     cache) into BACKUP_DIR/hub-<installed>-<time>.tar.gz. The newest
     KEEP_BACKUPS stay. It holds the config's passwords in clear, like the
     config itself on the same SSD.
  2. download the release tarball and its checksum into WORK_DIR (on the
     SSD: /tmp is RAM on this Pi) and verify the SHA-256
  3. unpack (plain files and directories under te_camhub/ only) and check
     it: install.sh and the update script are there, VERSION matches the
     tag, every hub/app/*.py compiles with this Pi's Python
  4. hand over to hub-update.sh in its own transient systemd unit, because
     install.sh restarts this very process. That script replaces
     /root/te_camhub, runs install.sh, waits until the Hub answers again and
     puts the previous code back from the backup if either fails. It
     reports into RUN_FILE and LOG, which status() reads after the restart.

RUNNING_MARKER exists from step 1 until the script ends: server.py refuses
the endpoints that remount / meanwhile, and no OS update can start.
"""
import os, re, json, time, glob, shutil, hashlib, tarfile, threading, subprocess
import urllib.request, urllib.error
import hubconf
import osupdate

DEFAULT_REPO = "umstandsheini/te_camhub"
API = "https://api.github.com/repos/%s/releases/latest"
ROOT = "/"
VERSION_FILE = "/opt/teslacam-hub/VERSION"
RUNNER = "/opt/teslacam-hub/hub-update.sh"
BACKUP_DIR = "/backingfiles/hub-backups"
WORK_DIR = "/backingfiles/hub-update"
LOG = "/mutable/hub-update.log"
RUNNING_MARKER = "/run/teslacam-hub-update"
UNIT = "teslacam-hub-update"
CHECK_FILE = "hub-update-check.json"
RUN_FILE = "hub-update-run.json"
KEEP_BACKUPS = 3
CHECK_EVERY = 6 * 3600
MIN_FREE_MB = 600
MAX_DOWNLOAD = 300 * 1048576
TAIL_LINES = 60
BACKUP_PATHS = ["opt/teslacam-hub", "root/te_camhub", "root/bin", "root/teslausb_setup_variables.conf",
                "etc/systemd/system/teslacam-*", "etc/NetworkManager/system-connections", "mutable/tls",
                "backingfiles/decrypt-viewer-state"]
BACKUP_EXCLUDES = ["backingfiles/decrypt-viewer-state/derived", "*/__pycache__"]

_guard = threading.Lock()
_state_dir = None


def init(state_dir):
    global _state_dir
    _state_dir = state_dir


def _path(name):
    return os.path.join(_state_dir, name)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _log(line):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")
    except OSError:
        pass


def repo():
    return hubconf.getval("HUB_UPDATE_REPO") or DEFAULT_REPO


def installed_version():
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return f.read().strip() or "dev"
    except OSError:
        return "dev"


def _vtuple(v):
    if not re.fullmatch(r"v?\d+(\.\d+)*", v or ""):
        return None
    return tuple(int(n) for n in re.findall(r"\d+", v))


def is_newer(latest, installed):
    """A release is offered when its tag is a higher version than the
    installed one -- or when the installed one isn't a release at all
    ("dev", a hand-deployed tree)."""
    a = _vtuple(latest)
    if a is None or latest == installed:
        return False
    b = _vtuple(installed)
    return b is None or a > b


def parse_release(rel):
    tag = str(rel.get("tag_name") or "")
    assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets") or []}
    pkg = "te_camhub-%s.tar.gz" % tag
    return {"tag": tag, "name": rel.get("name") or tag, "notes": (rel.get("body") or "")[:6000],
            "published": rel.get("published_at"), "url": rel.get("html_url"),
            "tarball": assets.get(pkg), "sha256": assets.get(pkg + ".sha256"),
            "image": next((u for n, u in assets.items() if n and n.endswith(".img.xz")), None)}


def _http(url, timeout=30):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "TeslaCam-Hub"})
    return urllib.request.urlopen(req, timeout=timeout)


def check():
    """Ask GitHub for the latest release. A failed check (no internet in
    the car) keeps the release found last time."""
    prev = _read_json(_path(CHECK_FILE)) or {}
    res = {"checked": time.time(), "repo": repo(), "latest": prev.get("latest"), "error": None}
    try:
        with _http(API % res["repo"]) as r:
            res["latest"] = parse_release(json.load(r))
        if not res["latest"]["tarball"] or not res["latest"]["sha256"]:
            res["error"] = "Release %s enthält kein Installationspaket" % res["latest"]["tag"]
    except urllib.error.HTTPError as e:
        res["error"] = ("Noch keine Veröffentlichung auf GitHub" if e.code == 404
                        else "GitHub antwortet mit Fehler %d" % e.code)
    except Exception as e:
        res["error"] = "GitHub nicht erreichbar: %s" % str(e)[:150]
    _write_json(_path(CHECK_FILE), res)
    return status()


def check_loop():
    time.sleep(120)
    while True:
        try:
            check()
        except Exception as e:
            print("[hub] update check:", e, flush=True)
        time.sleep(CHECK_EVERY)


def running():
    try:
        return time.time() - os.path.getmtime(RUNNING_MARKER) < 3 * 3600
    except OSError:
        return False


def _backup_files():
    return sorted(glob.glob(os.path.join(BACKUP_DIR, "hub-*.tar.gz")), key=os.path.getmtime, reverse=True)


def list_backups():
    out = []
    for p in _backup_files():
        try:
            st = os.stat(p)
            out.append({"name": os.path.basename(p), "size": st.st_size, "t": st.st_mtime})
        except OSError:
            pass
    return out


def _tail():
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            return [l.rstrip("\n") for l in f.readlines()[-TAIL_LINES:]]
    except OSError:
        return []


def status():
    c = _read_json(_path(CHECK_FILE)) or {}
    latest = c.get("latest")
    inst = installed_version()
    return {"installed": inst, "repo": c.get("repo") or repo(), "checked": c.get("checked"),
            "check_error": c.get("error"), "latest": latest,
            "available": bool(latest and latest.get("tarball") and latest.get("sha256")
                              and is_newer(latest.get("tag"), inst)),
            "running": running(), "run": _read_json(_path(RUN_FILE)), "tail": _tail(),
            "backups": list_backups()}


def _home_wifi():
    """(on the home WiFi, its name)"""
    home = hubconf.getval("SSID")
    return bool(home) and osupdate._ssid() == home, home


def start_update():
    st = status()
    if st["running"]:
        return {"ok": False, "error": "Update läuft bereits"}
    if osupdate.running():
        return {"ok": False, "error": "OS-Update läuft gerade – bitte warten, bis es fertig ist"}
    if not st["available"]:
        return {"ok": False, "error": "Kein neueres Update bekannt – zuerst „Nach Updates suchen“"}
    home_ok, home = _home_wifi()
    if not home_ok:
        return {"ok": False, "error": "Nur im Heim-WLAN (%s) – unterwegs fehlt stabiler Strom, und der Download "
                                      "liefe über mobile Daten." % (home or "nicht konfiguriert")}
    try:
        free = shutil.disk_usage(os.path.dirname(BACKUP_DIR.rstrip("/"))).free
    except OSError:
        free = 0
    if free < MIN_FREE_MB * 1048576:
        return {"ok": False, "error": "Zu wenig Platz für Sicherung und Download (mindestens %d MB)" % MIN_FREE_MB}
    with _guard:
        if running():
            return {"ok": False, "error": "Update läuft bereits"}
        open(RUNNING_MARKER, "w").close()
    threading.Thread(target=_work, args=(st["latest"], st["installed"]), daemon=True).start()
    return {"ok": True}


def _work(rel, installed):
    run = {"tag": rel["tag"], "from": installed, "phase": "", "ok": None, "error": None,
           "started": time.time(), "finished": None, "backup": None}

    def phase(p):
        run["phase"] = p
        _write_json(_path(RUN_FILE), run)
        _log("=== %s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), p))

    _log("")
    _log("##### Hub-Update %s -> %s, %s" % (installed, rel["tag"], time.strftime("%Y-%m-%d %H:%M:%S")))
    try:
        phase("Sicherung")
        run["backup"] = make_backup(installed)
        phase("Download")
        src = fetch_release(rel)
        phase("Installation startet")
        _launch(src, run["backup"], rel["tag"])
    except Exception as e:
        # Nothing was installed yet: report and give the marker back.
        run.update(phase="fehlgeschlagen", ok=False, error=str(e)[:300], finished=time.time())
        _write_json(_path(RUN_FILE), run)
        _log("##### Ergebnis: %s" % run["error"])
        try:
            os.remove(RUNNING_MARKER)
        except OSError:
            pass


def make_backup(installed):
    os.makedirs(BACKUP_DIR, mode=0o700, exist_ok=True)
    name = "hub-%s-%s.tar.gz" % (re.sub(r"[^\w.-]", "_", installed), time.strftime("%Y%m%d-%H%M%S"))
    final = os.path.join(BACKUP_DIR, name)
    tmp = final + ".part"
    paths = []
    for pattern in BACKUP_PATHS:
        paths += sorted(os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(ROOT, pattern)))
    if not paths:
        raise RuntimeError("Sicherung fehlgeschlagen: nichts zu sichern gefunden")
    os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
    cmd = ["tar", "-czf", tmp, "-C", ROOT] + ["--exclude=" + e for e in BACKUP_EXCLUDES] + paths
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode not in (0, 1):   # 1: a file (a log) changed while it was read
        try:
            os.remove(tmp)
        except OSError:
            pass
        lines = [l for l in (r.stderr or "").splitlines() if l.strip()]
        raise RuntimeError("Sicherung fehlgeschlagen: %s" % (lines[-1] if lines else "tar rc=%d" % r.returncode)[:200])
    os.chmod(tmp, 0o600)
    os.replace(tmp, final)
    for old in _backup_files()[KEEP_BACKUPS:]:
        try:
            os.remove(old)
        except OSError:
            pass
    _log("Sicherung: %s (%.1f MB)" % (final, os.path.getsize(final) / 1048576))
    return final


def _download(url, dest):
    size = 0
    with _http(url, timeout=60) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_DOWNLOAD:
                raise RuntimeError("Download zu groß")
            f.write(chunk)
    return size


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_extract(tgz, dest):
    with tarfile.open(tgz, "r:gz") as tf:
        members = tf.getmembers()
        for m in members:
            parts = m.name.split("/")
            if (m.name.startswith("/") or ".." in parts or parts[0] != "te_camhub"
                    or not (m.isfile() or m.isdir())):
                raise RuntimeError("Unerwarteter Eintrag im Paket: %s" % m.name[:120])
        os.makedirs(dest)
        kw = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        tf.extractall(dest, members=members, **kw)


def fetch_release(rel):
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    os.makedirs(WORK_DIR, mode=0o700)
    tgz = os.path.join(WORK_DIR, "release.tar.gz")
    size = _download(rel["tarball"], tgz)
    shafile = os.path.join(WORK_DIR, "release.sha256")
    _download(rel["sha256"], shafile)
    with open(shafile, encoding="utf-8", errors="replace") as f:
        want = (f.read().split() or [""])[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", want) or _sha256(tgz) != want:
        raise RuntimeError("Prüfsumme stimmt nicht – Download beschädigt")
    _log("Download: %s (%.1f MB), SHA-256 geprüft" % (rel["tarball"], size / 1048576))
    _safe_extract(tgz, os.path.join(WORK_DIR, "src"))
    src = os.path.join(WORK_DIR, "src", "te_camhub")
    for need in ("VERSION", "hub/install.sh", "hub/hub-update.sh", "hub/app/server.py"):
        if not os.path.isfile(os.path.join(src, need)):
            raise RuntimeError("Paket unvollständig: %s fehlt" % need)
    with open(os.path.join(src, "VERSION"), encoding="utf-8") as f:
        version = f.read().strip()
    if version != rel["tag"]:
        raise RuntimeError("Paket meldet Version %s statt %s" % (version, rel["tag"]))
    for py in sorted(glob.glob(os.path.join(src, "hub", "app", "*.py"))):
        try:
            with open(py, "rb") as f:
                compile(f.read(), py, "exec")
        except (SyntaxError, ValueError) as e:
            raise RuntimeError("Paket passt nicht zu diesem Pi: %s lässt sich nicht übersetzen (%s)"
                               % (os.path.basename(py), str(e)[:100]))
    return src


def _launch(src, backup, tag):
    runner = RUNNER if os.path.isfile(RUNNER) else os.path.join(src, "hub", "hub-update.sh")
    subprocess.run(["systemctl", "reset-failed", UNIT], capture_output=True)
    r = subprocess.run(["systemd-run", "--unit=" + UNIT, "--collect", "/bin/bash", runner, src, backup, tag],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError("Installation ließ sich nicht starten: %s" % (r.stderr or "").strip()[:200])
    _log("Installation läuft in %s.service (%s)" % (UNIT, runner))
