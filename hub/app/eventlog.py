"""
Event/telemetry logging for the Hub, independent of and complementary to
teslausb's own archiveloop.log.

Two on-disk logs (JSON Lines / CSV, both size-capped like archiveloop.log's
own truncate-to-last-N-lines pattern so they can't grow unbounded on the
SD card):

- events.log:  discrete, human-readable events. An always-on subset needs
  no BLE (WiFi/USB connectivity changes, temperature threshold crossings,
  vault lock state); a richer subset only fires when BLE is paired and the
  trip-watch loop is active (drive start/stop with trip summary, lock/
  sleep/charging state changes during a trip) -- "mit BLE mehr, ohne BLE
  weniger" per the explicit request this was built for.
- temperature.log: one CSV line per minute, Pi temperature only, kept
  separate from events.log so routine per-minute data doesn't drown out
  the discrete event stream.

Blackbox trip GPS points live in their own per-trip files under
blackbox/ (see blackbox.py) -- not part of this module.
"""
import os, json, time, threading

_lock = threading.Lock()
_state_dir = None
MAX_LINES = 20000
MAX_BYTES = 2_000_000


def init(state_dir):
    global _state_dir
    _state_dir = state_dir
    os.makedirs(_state_dir, exist_ok=True)


def _path(name):
    return os.path.join(_state_dir, name)


def temperature_log_path():
    return _path("temperature.log")


def _append(path, line):
    with _lock:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            return
        _maybe_truncate(path)


def _maybe_truncate(path):
    try:
        if os.path.getsize(path) <= MAX_BYTES:
            return
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= MAX_LINES:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines[-MAX_LINES:])
    except Exception:
        pass


def log_event(category, message, **fields):
    """category: short machine tag ('wifi', 'usb', 'temp', 'trip', 'ble', ...).
    message: human-readable German text for direct display.
    fields: optional extra structured data (e.g. duration_s, distance_km)."""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "category": category,
        "message": message,
    }
    if fields:
        entry["data"] = fields
    _append(_path("events.log"), json.dumps(entry, ensure_ascii=False))


def log_temperature(temp_c):
    _append(_path("temperature.log"), f"{time.strftime('%Y-%m-%dT%H:%M:%S')},{temp_c}")


def read_events(limit=200):
    p = _path("events.log")
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8") as f:
        lines = f.readlines()[-limit:]
    out = []
    for l in lines:
        l = l.strip()
        if not l:
            continue
        try:
            out.append(json.loads(l))
        except Exception:
            pass
    out.reverse()  # newest first
    return out


def read_temperature(limit=1440):
    """Default limit 1440 = last 24h at one point/minute."""
    p = _path("temperature.log")
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8") as f:
        lines = f.readlines()[-limit:]
    out = []
    for l in lines:
        parts = l.strip().split(",")
        if len(parts) == 2:
            try:
                out.append({"ts": parts[0], "temp": float(parts[1])})
            except ValueError:
                pass
    return out


def read_temperature_series(hours=24, max_points=720):
    """Temperature for the last `hours` for the Hub's chart, folded into
    about max_points time buckets (min/avg/max each; one more when the
    window straddles a bucket edge): 30 days at one reading per minute
    would otherwise be ~43k points. Times go out as
    epoch seconds; the log's timestamps are the Pi's local time. Parses the
    timestamp by slicing instead of strptime, which is slow enough on the
    Pi to matter across a whole month of lines."""
    p = _path("temperature.log")
    empty = {"points": [], "bucket_sec": 60, "hours": hours, "latest": None}
    if not os.path.isfile(p):
        return empty
    since = time.time() - hours * 3600
    since_s = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(since))
    rows = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            ts, _, v = line.strip().partition(",")
            if len(ts) != 19 or ts < since_s:
                continue
            try:
                t = time.mktime((int(ts[0:4]), int(ts[5:7]), int(ts[8:10]),
                                 int(ts[11:13]), int(ts[14:16]), int(ts[17:19]), 0, 0, -1))
                rows.append((t, float(v)))
            except ValueError:
                continue
    if not rows:
        return empty
    rows.sort()   # a reboot before NTP sync can log a few lines out of order
    bucket = max(60, -(-hours * 3600 // (max_points * 60)) * 60)
    agg = {}
    for t, v in rows:
        b = agg.get(int(t // bucket))
        if b is None:
            agg[int(t // bucket)] = [v, v, v, 1]
        else:
            b[0] = min(b[0], v); b[1] = max(b[1], v); b[2] += v; b[3] += 1
    pts = [{"t": k * bucket, "min": b[0], "max": b[1], "avg": round(b[2] / b[3], 2)}
           for k, b in sorted(agg.items())]
    return {"points": pts, "bucket_sec": bucket, "hours": hours,
            "latest": {"t": rows[-1][0], "temp": rows[-1][1]}}
