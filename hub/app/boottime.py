"""
Boot timing per Pi boot, measured from the persistent journal
(/mutable/journal) and kept in boottimes.jsonl in the Hub's state dir, so the
numbers outlive journal rotation (64 MB / 30 days). Every boot the journal
still has gets filled in, including boots from before this module existed.

Times are seconds of uptime (CLOCK_MONOTONIC since kernel start) -- what
matters in the car: the Pi powers up with the car, and the question is how
long until the dashcam sees its drive.

Per boot:
  kernel_s, userspace_s, finished_s   systemd's "Startup finished in ..." (PID 1)
  backingfiles_s                      SSD data partition mounted
  gadget_s                            USB gadget bound (first "bound to ..." of
                                      enable_gadget.sh; archiveloop re-binds later)
  drives_s                            the car's USB host configured the drives
                                      (gadget-watch.sh; absent if no host came)
  wifi_s                              wlan0 activated
  hub_s                               Hub serving HTTPS (first start in that boot)
  clean_end                           that boot ended with a proper shutdown;
                                      False = power cut (the car's normal way)
  booted_at                           wall clock (epoch s): the Pi has no RTC, so
                                      this is a late entry's realtime minus its
                                      monotonic time, not the first entry's stamp
"""
import os, re, json, time, subprocess, threading
import eventlog

LOG_NAME = "boottimes.jsonl"
KEEP = 500

# One journalctl --grep (PCRE2) pass per boot; the checks in _parse() pick
# the values out of what it returns.
_GREP = (r"Startup finished in .*\(kernel\)|Mounted backingfiles\.mount|bound to \S+ at [0-9.]+s uptime"
         r"|host configured the drives at|Hub https://|\(wlan0\): state change: .* -> activated"
         r"|Reached target .*(?:[Ss]hutdown|[Rr]eboot|[Pp]ower-?[Oo]ff|[Ff]inal)|Stopped target (?:sysinit|local-fs)\.target"
         r"|Journal stopped")
# A clean shutdown rarely gets as far as "Journal stopped": the journal lives
# on /mutable, which is unmounted mid-shutdown, so the last lines PID 1 gets
# to write are "Stopped target sysinit/local-fs.target" (measured 2026-09-15
# on a `reboot`). A power cut just ends the journal mid-run.
_CLEAN_TARGETS = ("shutdown.target", "reboot.target", "poweroff.target", "final.target")
_CLEAN_STOPPED = ("sysinit.target", "local-fs.target")
_STARTUP = re.compile(r"Startup finished in (.+?) \(kernel\) \+ (.+?) \(userspace\) = (.+?)\.?$")
_BOUND = re.compile(r"bound to \S+ at ([0-9.]+)s uptime")
_DRIVES = re.compile(r"host configured the drives at ([0-9.]+)s uptime")
_BOOT_ROW = re.compile(r"^\s*-?\d+\s+([0-9a-f]{32})\s")

_lock = threading.Lock()
_state_dir = None


def init(state_dir):
    global _state_dir
    _state_dir = state_dir


def _path():
    return os.path.join(_state_dir, LOG_NAME)


def _dur(s):
    """systemd duration text ("3.287s", "1min 2.345s", "358ms") -> seconds."""
    m = re.fullmatch(r"(?:(\d+)min )?([0-9.]+)(ms|s)", (s or "").strip())
    if not m:
        return None
    v = float(m.group(2)) / (1000.0 if m.group(3) == "ms" else 1.0)
    return round(v + 60 * int(m.group(1) or 0), 3)


def _uptime():
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


def _current_boot_id():
    with open("/proc/sys/kernel/random/boot_id") as f:
        return f.read().strip().replace("-", "")


def _journal(args, timeout=60):
    r = subprocess.run(["journalctl", "--no-pager", "-q"] + args, capture_output=True, text=True, timeout=timeout)
    return r.stdout


def _list_boots():
    return [m.group(1) for m in map(_BOOT_ROW.match, _journal(["--list-boots"]).splitlines()) if m]


def _entries(text):
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e.get("MESSAGE"), str):
            yield e


def parse_entries(entries, current):
    """Boot record from journal JSON entries (split out for testing)."""
    rec = {}
    for e in entries:
        msg = e["MESSAGE"]
        mono = int(e.get("__MONOTONIC_TIMESTAMP", 0)) / 1e6
        pid = str(e.get("_PID", ""))
        if pid == "1" and "(kernel)" in msg:
            m = _STARTUP.search(msg)
            if m and "finished_s" not in rec:
                rec.update(kernel_s=_dur(m.group(1)), userspace_s=_dur(m.group(2)), finished_s=_dur(m.group(3)))
        elif "Mounted backingfiles.mount" in msg:
            rec.setdefault("backingfiles_s", round(mono, 2))
        elif _BOUND.search(msg):
            rec.setdefault("gadget_s", float(_BOUND.search(msg).group(1)))
        elif _DRIVES.search(msg):
            rec.setdefault("drives_s", float(_DRIVES.search(msg).group(1)))
        elif "(wlan0): state change:" in msg and "-> activated" in msg:
            rec.setdefault("wifi_s", round(mono, 2))
        elif msg.startswith("Hub https://"):
            rec.setdefault("hub_s", round(mono, 2))
        elif (pid == "1" and ((msg.startswith("Reached target") and any(t in msg for t in _CLEAN_TARGETS))
                              or (msg.startswith("Stopped target") and any(t in msg for t in _CLEAN_STOPPED)))) or \
                (e.get("SYSLOG_IDENTIFIER") == "systemd-journald" and "Journal stopped" in msg):
            rec["clean_end"] = True
    if not current:
        rec.setdefault("clean_end", False)
    return rec


def _measure(boot_id, current):
    rec = parse_entries(_entries(_journal(["-b", boot_id, "-o", "json", "-g", _GREP])), current)
    rec["boot_id"] = boot_id
    if current:
        rec["booted_at"] = round(time.time() - _uptime())
    else:
        last = next(_entries(_journal(["-b", boot_id, "-n", "1", "-o", "json"])), None)
        if last:
            rec["booted_at"] = round((int(last["__REALTIME_TIMESTAMP"]) - int(last["__MONOTONIC_TIMESTAMP"])) / 1e6)
    return rec


def _load():
    recs = {}
    try:
        with open(_path(), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    recs[r["boot_id"]] = r
                except (ValueError, KeyError):
                    continue
    except FileNotFoundError:
        pass
    return recs


def _save(recs):
    rows = sorted(recs.values(), key=lambda r: r.get("booted_at") or 0)[-KEEP:]
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    os.replace(tmp, _path())


def _fmt(v):
    return ("%.1f s" % v).replace(".", ",") if isinstance(v, (int, float)) else "–"


def collect():
    """Measure every boot the journal has that isn't final in the log yet.
    Past boots are final once measured; the current one stays open (its end
    isn't known yet) and is logged to the event log once. Returns True when
    the current boot's startup is complete (systemd logged 'Startup
    finished' and the Hub is up)."""
    cur = _current_boot_id()
    with _lock:
        recs = _load()
        changed = False
        boots = _list_boots()
        for bid in boots:
            old = recs.get(bid) or {}
            if old.get("final"):
                continue
            rec = _measure(bid, bid == cur)
            rec["final"] = bid != cur
            if old.get("logged"):
                rec["logged"] = True
            if bid == cur and not rec.get("logged") and "hub_s" in rec:
                prev = [recs[b] for b in boots[:-1] if b in recs] if boots and boots[-1] == cur else []
                tail = " – davor Stromausfall ohne Herunterfahren" if prev and prev[-1].get("clean_end") is False else ""
                eventlog.log_event("power", "Pi gestartet: Laufwerke fürs Auto nach %s, WLAN nach %s, Hub nach %s%s"
                                   % (_fmt(rec.get("drives_s")), _fmt(rec.get("wifi_s")), _fmt(rec.get("hub_s")), tail),
                                   **{k: rec[k] for k in ("drives_s", "wifi_s", "hub_s", "finished_s") if k in rec})
                rec["logged"] = True
            if rec != old:
                recs[bid] = rec
                changed = True
        if changed:
            _save(recs)
        c = recs.get(cur) or {}
        return "finished_s" in c and "hub_s" in c


def loop(interval=30, tries=30):
    """Thread body for server.py: measure until the current boot is
    complete, at most ~15 min -- systemd only logs 'Startup finished' once
    every unit is up, which smbd can delay for minutes."""
    for _ in range(tries):
        time.sleep(interval)
        try:
            if collect():
                return
        except Exception as e:
            print("[hub] boot times:", e, flush=True)


def status(limit=30):
    with _lock:
        recs = _load()
    rows = sorted(recs.values(), key=lambda r: r.get("booted_at") or 0, reverse=True)[:limit]
    try:
        cur = _current_boot_id()
    except OSError:
        cur = None
    return {"boots": rows, "current": cur}


def latest():
    """The current boot's record, for MQTT (may be None before the first collect)."""
    try:
        cur = _current_boot_id()
    except OSError:
        return None
    with _lock:
        return _load().get(cur)
