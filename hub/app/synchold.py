"""
Sync hold: at home, keep the car awake until everything is on the NAS.

The car cuts this Pi's USB power as soon as it falls asleep, so every home
arrival used to be a race: archiveloop's archive pass and the Hub's own NAS
jobs (coverage check, key sidecars, media, trips) only got as far as the
car's idle timer allowed. This module decides when the Hub should keep the
car awake instead:

  - it arms once per home visit, as soon as the home WiFi (SSID from the
    teslausb config) is connected AND the archive server passes the same
    reachability check archiveloop itself uses (archive-is-reachable.sh),
  - it holds until archiveloop reports this visit's archive pass as done
    (ARCHIVE_STATE_FILE, written by run/archiveloop) AND one nas_sync_loop
    cycle has run start-to-finish without errors after that -- a cycle that
    started before the pass finished can't have pushed key sidecars for the
    clips it moved. Failures never count as done: archiveloop retries a
    failed pass every 2 minutes while the Hub keeps the car awake, and
    nas_sync_loop retries a failed cycle just as soon,
  - and it gives up after SYNC_HOLD_MAX_MIN (default 120) regardless, so a
    NAS that answers but never finishes can't drain the battery.

Once ended (done, timed out or switched off) it stays ended for the rest of
the visit, and only re-arms after home WiFi/NAS have been gone for
AWAY_GRACE_SEC, i.e. after the car actually left -- otherwise a timed-out
hold would restart itself on the next tick and the cap would mean nothing.

Times are seconds of uptime (/proc/uptime), the clock archiveloop stamps
its marker with: the Pi has no RTC, so wall-clock time jumps when NTP syncs
shortly after boot, which would corrupt any elapsed-time math spanning it.
State is persisted per boot_id, so a Hub restart mid-visit neither resets
the cap nor re-arms a hold that already ended; a Pi reboot starts fresh
(the car slept and woke again, which is a new chance to sync).

This module only decides. keepawake.tick() sends the actual BLE nudges, on
one schedule shared with the manual "Auto wach halten" switch -- and those
'wake' nudges were measured on 2026-09-13 NOT to keep the car awake (see
keepawake.py). The decision logic here stands; the mechanism still has to be
replaced before the hold actually holds.
"""
import json, os, socket, subprocess, threading
import hubconf

ARCHIVE_STATE_FILE = "/tmp/teslausb_archive_state"
REACHABLE_SCRIPT = "/root/bin/archive-is-reachable.sh"
DEFAULT_MAX_MIN = 120
AWAY_GRACE_SEC = 180

_lock = threading.Lock()
_state_path = None
_st = None


def uptime():
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


def _boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return ""


def _fresh():
    return {"boot_id": _boot_id(), "phase": "idle", "start": None, "ended": None,
            "end_reason": None, "away_since": None, "present": False, "waiting_for": []}


def init(state_dir):
    global _state_path, _st
    _state_path = os.path.join(state_dir, "synchold.json")
    try:
        with open(_state_path, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = None
    if not st or st.get("boot_id") != _boot_id():
        st = _fresh()
    _st = st


def _save():
    try:
        tmp = _state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_st, f)
        os.replace(tmp, _state_path)
    except OSError as e:
        print("[hub] synchold save:", e, flush=True)


def enabled():
    return hubconf.getval("SYNC_HOLD_ENABLED") != "false"


def max_sec():
    try:
        m = int(hubconf.getval("SYNC_HOLD_MAX_MIN") or DEFAULT_MAX_MIN)
    except ValueError:
        m = DEFAULT_MAX_MIN
    return max(1, m) * 60


def _home_wifi():
    want = hubconf.getval("SSID")
    try:
        r = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True, timeout=5)
        ssid = r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        ssid = ""
    return bool(ssid) and (not want or ssid == want)


def _nas_reachable():
    server = hubconf.getval("ARCHIVE_SERVER")
    if not server:
        return False
    if os.path.isfile(REACHABLE_SCRIPT):
        try:
            return subprocess.run(["bash", REACHABLE_SCRIPT, server],
                                  capture_output=True, timeout=15).returncode == 0
        except Exception:
            return False
    try:
        with socket.create_connection((server, 445), timeout=5):
            return True
    except OSError:
        return False


def archive_state():
    """(phase, uptime) from run/archiveloop's marker, or None when there is
    no marker -- archiveloop isn't running this boot, so there's no archive
    pass to wait for."""
    try:
        with open(ARCHIVE_STATE_FILE, encoding="utf-8") as f:
            phase, up = f.read().split()[:2]
        return phase, float(up)
    except (OSError, ValueError):
        return None


def _end(st, now, reason):
    st.update(phase="ended", ended=now, end_reason=reason)


def _reset(st):
    st.update(phase="idle", start=None, ended=None, end_reason=None,
              away_since=None, waiting_for=[])


def tick(nas):
    """Called every ~30s from server.py's keepawake_loop.

    nas: {"started": uptime the current/last nas_sync_loop cycle started,
          "completed_start": uptime the last error-free cycle started,
          "failed_start"/"error": the last failed cycle, if any}
    (any may be None).

    Returns (event, want_nas_cycle, holding): event is None or a dict with
    "event" one of started/complete/timeout/disabled/left; want_nas_cycle
    asks the caller to start a nas_sync_loop cycle now instead of waiting
    out its 10-minute sleep; holding says whether the car should be kept
    awake right now."""
    now = uptime()
    present = _home_wifi() and _nas_reachable()
    with _lock:
        st = _st
        changed = present != st.get("present")
        st["present"] = present
        event, want_cycle = None, False

        if not present:
            if st["phase"] != "idle":
                if st["away_since"] is None:
                    st["away_since"] = now
                    changed = True
                elif now - st["away_since"] >= AWAY_GRACE_SEC:
                    if st["phase"] == "holding":
                        event = {"event": "left", "elapsed": now - st["start"]}
                    _reset(st)
                    changed = True
        else:
            if st["away_since"] is not None:
                st["away_since"] = None
                changed = True
            if st["phase"] == "idle" and enabled():
                st.update(phase="holding", start=now, ended=None, end_reason=None)
                event = {"event": "started", "max_min": max_sec() // 60}
                changed = True

        # A brief WiFi/NAS dropout (inside AWAY_GRACE_SEC) doesn't pause the
        # hold: the car must stay awake through it, or the transfer that
        # resumes afterwards has no power to finish on.
        if st["phase"] == "holding":
            if not enabled():
                event = {"event": "disabled", "elapsed": now - st["start"]}
                _end(st, now, "disabled")
                changed = True
            else:
                arch = archive_state()
                arch_done = arch is None or arch[0] == "done"
                threshold = max(st["start"], arch[1] if arch and arch_done else 0)
                hub_done = (nas.get("completed_start") or -1) >= threshold
                waiting = []
                if not arch_done:
                    waiting.append("Archivierung (neuer Versuch nach Fehler)"
                                   if arch[0] == "failed" else "Archivierung")
                if not hub_done:
                    err = nas.get("error")
                    if err and (nas.get("failed_start") or -1) >= threshold:
                        waiting.append(f"NAS-Abgleich (letzter Fehler: {err[:120]})")
                    else:
                        waiting.append("NAS-Abgleich")
                if waiting != st["waiting_for"]:
                    st["waiting_for"] = waiting
                    changed = True
                if arch_done and not hub_done and (nas.get("started") or -1) < threshold:
                    want_cycle = True
                if not waiting:
                    event = {"event": "complete", "elapsed": now - st["start"]}
                    _end(st, now, "complete")
                    changed = True
                elif now - st["start"] >= max_sec():
                    event = {"event": "timeout", "elapsed": now - st["start"], "waiting_for": waiting}
                    _end(st, now, "timeout")
                    changed = True

        if changed:
            _save()
        return event, want_cycle, st["phase"] == "holding"


def holding():
    with _lock:
        return bool(_st) and _st["phase"] == "holding"


def status():
    with _lock:
        st = dict(_st or _fresh())
    out = {"enabled": enabled(), "max_min": max_sec() // 60, "phase": st["phase"],
           "present": bool(st.get("present")), "waiting_for": list(st.get("waiting_for") or []),
           "end_reason": st.get("end_reason")}
    try:
        now = uptime()
    except OSError:
        now = None
    if st["phase"] == "holding" and now is not None and st.get("start") is not None:
        elapsed = now - st["start"]
        out["elapsed_sec"] = int(elapsed)
        out["remaining_sec"] = int(max(0, max_sec() - elapsed))
    if st["phase"] == "ended" and st.get("start") is not None and st.get("ended") is not None:
        out["ended_after_sec"] = int(st["ended"] - st["start"])
    return out
