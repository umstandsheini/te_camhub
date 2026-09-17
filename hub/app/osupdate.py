"""
OS updates (apt) from the Hub's Diagnose page.

The root filesystem is read-only, and the Pi runs off the car's USB port,
which the car cuts on its own schedule -- unclean power loss is the normal
case here, not an accident. An apt upgrade cut off halfway leaves dpkg
half-configured on a root that the next boot mounts read-only again, which is
the one way an update can really hurt this box. Hence:

  - check() is harmless at any time: `apt-get update` into throwaway lists
    under /tmp (root stays ro), then a simulated upgrade to list what would
    change, plus `dpkg --audit` to spot an earlier interrupted run.
  - upgrade only on an explicit click, and only on the home WiFi (downloads
    never go over the phone's mobile data). Home WiFi does NOT mean steady
    power: installed in the car, the Pi still runs off the car's USB port
    even in the garage, and the car cuts it when it falls asleep -- the UI
    tells the user to keep the car awake (Sentry) or use a power supply.
    It takes archiveloop's archive lock, remounts / (and
    /boot/firmware, which kernel/firmware packages write to) rw, heals an
    interrupted run with `dpkg --configure -a`, runs a non-interactive
    `apt-get upgrade --with-new-pkgs` (installs new dependencies but never
    removes packages; keeps existing config files), cleans the package
    cache, syncs and remounts ro.
  - While it runs, RUNNING_MARKER exists; server.py refuses the other
    endpoints that remount / themselves (their `remount,ro` in a finally
    block would pull the root out from under dpkg).

Output goes to /mutable/os-update.log (persistent), so an interrupted run can
be reconstructed after the next boot.
"""
import os, re, time, fcntl, shutil, threading, subprocess
import hubconf

LOG = "/mutable/os-update.log"
RUNNING_MARKER = "/run/teslacam-os-update"
ARCHIVE_LOCK = "/tmp/teslausb_archive.lock"   # same flock as archiveloop's archive_lock_and_run
CHECK_DIR = "/tmp/apt-check"
MIN_ROOT_FREE_MB = 400
TAIL_LINES = 80
# Packages after which a reboot is the sane way to get everything running on
# the new version (kernel/firmware, libc, init, the Hub's own interpreter).
REBOOT_PKGS = re.compile(r"^(linux-image|raspi-firmware|raspberrypi-kernel|raspberrypi-bootloader|rpi-eeprom"
                         r"|libc6$|systemd$|udev$|dbus|libssl|python3(\.\d+)?(-minimal)?$|libpython3)")
ENV = dict(os.environ, DEBIAN_FRONTEND="noninteractive", APT_LISTCHANGES_FRONTEND="none",
           NEEDRESTART_MODE="a", LC_ALL="C")
UPGRADE_OPTS = ["-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold",
                "-o", "Acquire::Retries=3"]

_guard = threading.Lock()
_state = {"checked": None, "updates": [], "check_error": None, "audit": "",
          "running": False, "phase": "", "started": None, "finished": None, "ok": None,
          "error": None, "reboot_recommended": False, "tail": []}


class _Abort(Exception):
    pass


def running():
    return os.path.exists(RUNNING_MARKER)


def status():
    with _guard:
        s = dict(_state)
        s["updates"] = list(_state["updates"])
        s["tail"] = list(_state["tail"])
    s["security"] = sum(1 for u in s["updates"] if u["security"])
    return s


def _run(cmd, timeout):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=ENV)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "Zeitüberschreitung")
    except OSError as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))


def _parse_sim(out):
    """'Inst pkg [old] (new Origin:rel/suite [arch])' lines of apt-get -s."""
    ups = []
    for line in out.splitlines():
        m = re.match(r"Inst (\S+) (?:\[([^\]]+)\] )?\((\S+) ([^)]*)\)", line)
        if m:
            ups.append({"pkg": m.group(1), "from": m.group(2) or "", "to": m.group(3),
                        "security": "security" in m.group(4).lower()})
    return ups


def _last_line(r):
    lines = [l for l in ((r.stderr or "") + "\n" + (r.stdout or "")).splitlines() if l.strip()]
    return (lines[-1] if lines else "rc=%s" % r.returncode)[:200]


def check():
    with _guard:
        if _state["running"]:
            return {"ok": False, "error": "Update läuft gerade", "status": status_unlocked()}
    try:
        return _check()
    finally:
        # The throwaway lists and caches are ~250 MB, and /tmp is RAM
        # (tmpfs) on this 1 GB Pi. Left behind after the first check they
        # helped the OOM killer take out the Hub (2026-09-15).
        shutil.rmtree(CHECK_DIR, ignore_errors=True)


def _check():
    lists, cache = os.path.join(CHECK_DIR, "lists"), os.path.join(CHECK_DIR, "cache")
    os.makedirs(os.path.join(lists, "partial"), exist_ok=True)
    os.makedirs(os.path.join(cache, "archives", "partial"), exist_ok=True)
    opts = ["-o", "Dir::State::Lists=" + lists, "-o", "Dir::Cache=" + cache]
    r = _run(["apt-get", "-q", "update"] + opts, 300)
    if r.returncode != 0:
        with _guard:
            _state.update(checked=time.time(), check_error="Paketlisten nicht ladbar: " + _last_line(r))
        return {"ok": False, "error": _state["check_error"], "status": status()}
    s = _run(["apt-get", "-s", "-q", "upgrade", "--with-new-pkgs"] + opts, 180)
    if s.returncode != 0:
        with _guard:
            _state.update(checked=time.time(), check_error="Simulation fehlgeschlagen: " + _last_line(s))
        return {"ok": False, "error": _state["check_error"], "status": status()}
    audit = _run(["dpkg", "--audit"], 30)
    with _guard:
        _state.update(checked=time.time(), updates=_parse_sim(s.stdout), check_error=None,
                      audit=(audit.stdout or "").strip()[:2000])
    return {"ok": True, "status": status()}


def status_unlocked():
    s = dict(_state)
    s["security"] = sum(1 for u in _state["updates"] if u["security"])
    return s


def _ssid():
    r = _run(["iwgetid", "-r"], 5)
    return (r.stdout or "").strip()


def start_upgrade():
    home = hubconf.getval("SSID")
    if not home or _ssid() != home:
        return {"ok": False, "error": "Nur im Heim-WLAN (%s) – unterwegs fehlt stabiler Strom, und der Download "
                                      "liefe über mobile Daten." % (home or "nicht konfiguriert")}
    free = shutil.disk_usage("/").free
    if free < MIN_ROOT_FREE_MB * 1048576:
        return {"ok": False, "error": "Zu wenig Platz auf der Systempartition (%d MB frei, mindestens %d MB nötig)"
                                      % (free // 1048576, MIN_ROOT_FREE_MB)}
    with _guard:
        if _state["running"]:
            return {"ok": False, "error": "Update läuft bereits"}
        _state.update(running=True, phase="startet", started=time.time(), finished=None, ok=None,
                      error=None, reboot_recommended=False, tail=[])
    open(RUNNING_MARKER, "w").close()
    threading.Thread(target=_upgrade_worker, daemon=True).start()
    return {"ok": True}


def _phase(text):
    with _guard:
        _state["phase"] = text
    _out("=== " + text)


def _out(line):
    line = line.rstrip()
    with _guard:
        _state["tail"].append(line)
        del _state["tail"][:-TAIL_LINES]
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _stream(cmd):
    """Run cmd, mirroring its output into the log and the UI tail. No
    timeout on purpose: killing dpkg halfway is exactly the state this
    module exists to avoid."""
    _out("$ " + " ".join(cmd))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=ENV, bufsize=1)
    for line in p.stdout:
        _out(line)
    return p.wait()


def _is_ro(mnt):
    r = _run(["findmnt", "-n", "-o", "OPTIONS", mnt], 5)
    return r.returncode == 0 and (r.stdout or "").strip().split(",")[0] == "ro"


def _is_mountpoint(mnt):
    return _run(["findmnt", "-n", mnt], 5).returncode == 0


def _upgrade_worker():
    fd = os.open(ARCHIVE_LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    remounted, ok, err, reboot = [], False, None, False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise _Abort("Archivierung läuft gerade – später erneut versuchen")
        _out("")
        _out("##### OS-Update %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
        _phase("Systempartition beschreibbar machen")
        for mnt in ("/", "/boot/firmware"):
            if _is_mountpoint(mnt) and _is_ro(mnt):
                if _stream(["mount", mnt, "-o", "remount,rw"]) != 0:
                    raise _Abort("%s ließ sich nicht beschreibbar machen" % mnt)
                remounted.append(mnt)
        _phase("unterbrochene Installation reparieren (falls vorhanden)")
        if _stream(["dpkg", "--configure", "-a"]) != 0:
            raise _Abort("dpkg --configure -a fehlgeschlagen – siehe Protokoll")
        _phase("Paketlisten laden")
        if _stream(["apt-get", "-q", "update", "-o", "Acquire::Retries=3"]) != 0:
            raise _Abort("Paketlisten nicht ladbar")
        sim = _run(["apt-get", "-s", "-q", "upgrade", "--with-new-pkgs"], 180)
        pkgs = _parse_sim(sim.stdout or "")
        reboot = any(REBOOT_PKGS.match(u["pkg"]) for u in pkgs)
        _phase("%d Pakete installieren" % len(pkgs))
        if _stream(["apt-get", "-y", "-q"] + UPGRADE_OPTS + ["upgrade", "--with-new-pkgs"]) != 0:
            raise _Abort("apt-get upgrade fehlgeschlagen – siehe Protokoll")
        _phase("aufräumen")
        _stream(["apt-get", "clean"])
        ok = True
    except _Abort as e:
        err = str(e)
    except Exception as e:
        err = "Interner Fehler: %s" % str(e)[:200]
    finally:
        os.sync()
        for mnt in reversed(remounted):
            if _stream(["mount", mnt, "-o", "remount,ro"]) != 0:
                # Something upgraded still holds a deleted file open for
                # writing; a reboot remounts cleanly ro anyway.
                reboot = True
                _out("%s bleibt bis zum Neustart beschreibbar" % mnt)
        os.close(fd)
        try:
            os.remove(RUNNING_MARKER)
        except OSError:
            pass
        _out("##### Ergebnis: %s" % ("OK" if ok else err))
        with _guard:
            _state.update(running=False, phase="", finished=time.time(), ok=ok, error=err,
                          reboot_recommended=reboot)
            if ok:
                _state.update(updates=[], audit="", checked=time.time(), check_error=None)
