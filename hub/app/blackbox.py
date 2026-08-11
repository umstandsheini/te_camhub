"""
Blackbox mode: per-trip GPS/telemetry point log + GPX export.

Only active while BLACKBOX_ENABLED is set and a trip is actually detected
(see server.py's trip_watch_loop) -- this module itself just knows how to
write/list/read/convert trip files, not when to record.

One JSONL file per trip under <state_dir>/blackbox/, named by the trip's
start timestamp so files sort chronologically. Each line is one point:
{"ts": "...", "lat":..., "lon":..., "heading":..., "odometer_mi":..., "shiftState":...}
Speed isn't recorded directly (Tesla's BLE "drive" state doesn't expose
it) -- it's derived at export/summary time from the odometer delta
between consecutive points, which is more accurate than a GPS-distance
estimate and needs no extra field.
"""
import os, json, glob, math, datetime

_state_dir = None


def init(state_dir):
    global _state_dir
    _state_dir = os.path.join(state_dir, "blackbox")
    os.makedirs(_state_dir, exist_ok=True)


def _trip_path(trip_id):
    safe = os.path.basename(trip_id)  # no path traversal via the id
    return os.path.join(_state_dir, safe + ".jsonl")


def start_trip(start_ts):
    """start_ts: 'YYYY-MM-DDTHH-MM-SS' (colons already replaced -- safe for
    a filename). Returns the trip_id to pass to append_point/end_trip."""
    trip_id = start_ts
    open(_trip_path(trip_id), "a", encoding="utf-8").close()
    return trip_id


def append_point(trip_id, ts, lat, lon, heading=None, odometer_mi=None, shift_state=None):
    entry = {"ts": ts, "lat": lat, "lon": lon}
    if heading is not None:
        entry["heading"] = heading
    if odometer_mi is not None:
        entry["odometer_mi"] = odometer_mi
    if shift_state is not None:
        entry["shiftState"] = shift_state
    try:
        with open(_trip_path(trip_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _read_points(trip_id):
    p = _trip_path(trip_id)
    if not os.path.isfile(p):
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def trip_summary(trip_id):
    points = _read_points(trip_id)
    if not points:
        return {"trip_id": trip_id, "points": 0}
    first, last = points[0], points[-1]
    distance_km = None
    if "odometer_mi" in first and "odometer_mi" in last:
        try:
            distance_km = (last["odometer_mi"] - first["odometer_mi"]) * 1.60934
        except Exception:
            distance_km = None
    if distance_km is None:
        # fall back to summing GPS point-to-point distance
        distance_km = 0.0
        for a, b in zip(points, points[1:]):
            distance_km += _haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
    return {
        "trip_id": trip_id,
        "start": first["ts"],
        "end": last["ts"],
        "points": len(points),
        "distance_km": round(distance_km, 2) if distance_km is not None else None,
    }


def list_trips():
    files = sorted(glob.glob(os.path.join(_state_dir, "*.jsonl")), reverse=True)
    out = []
    for f in files:
        trip_id = os.path.splitext(os.path.basename(f))[0]
        out.append(trip_summary(trip_id))
    return out


def _speed_mps(prev, cur):
    """Speed between two consecutive points, derived the same way
    trip_summary() derives trip distance: from the odometer delta (more
    accurate than a GPS-distance estimate), not from Tesla's BLE data
    (which doesn't expose speed directly)."""
    if not prev or "odometer_mi" not in prev or "odometer_mi" not in cur:
        return None
    try:
        dt = (datetime.datetime.fromisoformat(cur["ts"]) - datetime.datetime.fromisoformat(prev["ts"])).total_seconds()
        if dt <= 0:
            return None
        d_mi = cur["odometer_mi"] - prev["odometer_mi"]
        if d_mi < 0:  # odometer never decreases; a drop means a bad/duplicate reading
            return None
        return (d_mi * 1609.34) / dt  # m/s, the unit GPX/Garmin's speed extension expects
    except Exception:
        return None


def to_gpx(trip_id):
    """heading and a derived speed go into each trkpt's <extensions> using
    Garmin's TrackPointExtension namespace (gpxtpx) -- the de facto
    standard most GPX viewers/analysis tools already understand, rather
    than a custom schema nobody else can read. shiftState (Park/Drive/...)
    has no standard GPX equivalent, so it gets its own namespace; readers
    that don't know it just ignore it, same as any GPX extension."""
    points = _read_points(trip_id)
    try:
        dt = datetime.datetime.strptime(trip_id, "%Y-%m-%dT%H-%M-%S")
        name = f"Fahrt {dt.strftime('%Y-%m-%d %H:%M')}"
    except ValueError:
        name = f"Fahrt {trip_id}"
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="TeslaCam Hub" '
        'xmlns="http://www.topografix.com/GPX/1/1" '
        'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1" '
        'xmlns:tchub="https://github.com/umstandsheini/te_camhub">',
        f"  <trk><name>{name}</name><trkseg>",
    ]
    prev_with_odo = None
    for p in points:
        lat, lon, ts = p.get("lat"), p.get("lon"), p.get("ts")
        if lat is None or lon is None or not ts:
            continue  # tolerate malformed/legacy point rows instead of failing the whole export
        if "T" in ts and not ts.endswith("Z"):
            ts_out = ts + "Z" if len(ts) <= 19 else ts
        else:
            ts_out = ts
        tpx = []
        heading = p.get("heading")
        if heading is not None:
            tpx.append(f"<gpxtpx:course>{heading}</gpxtpx:course>")
        speed = _speed_mps(prev_with_odo, p)
        if speed is not None:
            tpx.append(f"<gpxtpx:speed>{speed:.2f}</gpxtpx:speed>")
        ext = ""
        if tpx or p.get("shiftState"):
            inner = ("<gpxtpx:TrackPointExtension>" + "".join(tpx) + "</gpxtpx:TrackPointExtension>") if tpx else ""
            if p.get("shiftState"):
                inner += f"<tchub:shiftState>{p['shiftState']}</tchub:shiftState>"
            ext = f"<extensions>{inner}</extensions>"
        parts.append(f'    <trkpt lat="{lat}" lon="{lon}"><time>{ts_out}</time>{ext}</trkpt>')
        if "odometer_mi" in p:
            prev_with_odo = p
    parts.append("  </trkseg></trk>")
    parts.append("</gpx>")
    return "\n".join(parts)
