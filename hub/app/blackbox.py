"""
Blackbox mode: per-trip GPS/telemetry point log + GPX export.

Only active while BLACKBOX_ENABLED is set and a trip is actually detected
(see server.py's trip_watch_loop) -- this module itself just knows how to
write/list/read/convert trip files, not when to record.

One file per trip under <state_dir>/blackbox/, named by the trip's start
timestamp so files sort chronologically. Each point is
{"ts": "...", "lat":..., "lon":..., "heading":..., "odometer_mi":..., "shiftState":...}
Speed isn't recorded directly (Tesla's BLE "drive" state doesn't expose
it) -- it's derived at export/summary time from the odometer delta
between consecutive points, which is more accurate than a GPS-distance
estimate and needs no extra field.

Encrypted at rest (since 2026-09-15): a route log is a movement profile,
and a stolen SSD must not hand it out. Trips are recorded while driving,
when the vault is nearly always locked (every power cut locks it), so the
points can't be sealed with the vault's master key. Instead an RSA keypair:
the private half lives in the vault (secret SECRET), the public half next
to the trips (trips.pub). Writing needs only the public key, reading needs
the unlocked vault.

Trip file <trip_id>.tbx, append-only so a power cut costs at most the
point being written:
  b"TBX1", then records: type(1) | length(4, big-endian) | payload
  b"K": RSA-OAEP(SHA-256)-wrapped 32-byte AES key for the P records after it
  b"P": nonce(12) | tag(16) | AES-256-GCM(point as JSON)
Each Hub run starts the trip file it writes with a fresh K record. Trips
recorded before this, as plaintext <trip_id>.jsonl, are converted on the
first unlock and then deleted (migrate_plaintext).
"""
import os, json, glob, math, datetime, struct, threading
from Crypto.Cipher import AES, PKCS1_OAEP
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Random import get_random_bytes

MAGIC = b"TBX1"
KEY_BITS = 3072            # ~8 s to generate on the Pi 4, once
SECRET = "blackbox_rsa_key"
_MAX_PENDING = 10000       # points held in RAM while no key exists yet (~28 h at one per 10 s)

_state_dir = None
_pub_path = None
_vault = None
_pub = None                # cached public key
_lock = threading.RLock()
_writers = {}              # trip_id -> AES key of the K record this Hub run wrote
_live = {}                 # trip_id -> running summary of the points recorded this run
_pending = {}              # trip_id -> [point] recorded before any key existed
_summaries = {}            # trip_id -> (file stamp, summary); finished trips never change


class Locked(Exception):
    """Reading trips needs the unlocked vault."""


def init(state_dir, vault=None):
    global _state_dir, _pub_path, _vault
    _state_dir = os.path.join(state_dir, "blackbox")
    _pub_path = os.path.join(_state_dir, "trips.pub")
    _vault = vault
    os.makedirs(_state_dir, exist_ok=True)


def _trip_path(trip_id, ext=".tbx"):
    safe = os.path.basename(trip_id)  # no path traversal via the id
    return os.path.join(_state_dir, safe + ext)


# ---- keys -------------------------------------------------------------------
def _get_pub():
    global _pub
    if _pub is None:
        try:
            with open(_pub_path, "rb") as f:
                _pub = RSA.import_key(f.read())
        except (OSError, ValueError):
            return None
    return _pub


def ensure_key():
    """On an unlocked vault: create the keypair if there is none (private
    half into the vault, public half next to the trips), keep trips.pub in
    line with the vault's key, and write the points that were waiting for
    a key. Returns True when a new key was made."""
    global _pub
    if _vault is None or not _vault.is_unlocked():
        return False
    with _lock:
        pem = _vault.get_secret(SECRET)
        made = False
        if not pem:
            pem = RSA.generate(KEY_BITS).export_key().decode("ascii")
            _vault.set_secret(SECRET, pem)
            made = True
        pub = RSA.import_key(pem).publickey().export_key()
        try:
            with open(_pub_path, "rb") as f:
                cur = f.read()
        except OSError:
            cur = None
        if cur != pub:
            tmp = _pub_path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(pub)
            os.replace(tmp, _pub_path)
            _pub = None
            _writers.clear()   # their K records were wrapped for a replaced key
        for trip_id, points in list(_pending.items()):
            if _write_points(trip_id, points):
                del _pending[trip_id]
        return made


def _oaep():
    """RSA-OAEP decryptor with the vault's private key; raises Locked."""
    if _vault is None or not _vault.is_unlocked():
        raise Locked()
    try:
        pem = _vault.get_secret(SECRET)
        if not pem:
            ensure_key()
            pem = _vault.get_secret(SECRET)
    except Locked:
        raise
    except Exception:
        raise Locked()     # locked in between
    return PKCS1_OAEP.new(RSA.import_key(pem), hashAlgo=SHA256)


# ---- file format ------------------------------------------------------------
def _record(kind, payload):
    return kind + struct.pack(">I", len(payload)) + payload


def _scan(data):
    """([(kind, payload)], end) for the complete records after the magic;
    end is where the last complete one stops (a power cut can leave a torn
    record behind it)."""
    recs, i = [], len(MAGIC)
    while i + 5 <= len(data):
        n = struct.unpack(">I", data[i + 1:i + 5])[0]
        if i + 5 + n > len(data):
            break
        recs.append((data[i:i + 1], data[i + 5:i + 5 + n]))
        i += 5 + n
    return recs, i


def _seal(key, point):
    nonce = get_random_bytes(12)
    ct, tag = AES.new(key, AES.MODE_GCM, nonce=nonce).encrypt_and_digest(
        json.dumps(point, ensure_ascii=False).encode("utf-8"))
    return nonce + tag + ct


def _new_key_record(pub):
    key = get_random_bytes(32)
    return key, _record(b"K", PKCS1_OAEP.new(pub, hashAlgo=SHA256).encrypt(key))


def _prepare(path):
    """Before a trip file gets a new K record: the magic for a new or empty
    file; otherwise cut off a record torn by a power loss, which would
    swallow everything written after it."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return MAGIC
    if not data.startswith(MAGIC):
        os.truncate(path, 0)   # empty, or its very first write was cut off
        return MAGIC
    end = _scan(data)[1]
    if end < len(data):
        os.truncate(path, end)
    return b""


def _write_points(trip_id, points):
    """Append points under this run's data key (a fresh K record first if
    this run hasn't written to the trip yet). False without a public key."""
    pub = _get_pub()
    if pub is None:
        return False
    path = _trip_path(trip_id)
    key = _writers.get(trip_id)
    head = b""
    if key is None:
        key, krec = _new_key_record(pub)
        head = _prepare(path) + krec
    with open(path, "ab") as f:
        f.write(head + b"".join(_record(b"P", _seal(key, p)) for p in points))
    _writers[trip_id] = key
    return True


# ---- recording --------------------------------------------------------------
def start_trip(start_ts):
    """start_ts: 'YYYY-MM-DDTHH-MM-SS' (colons already replaced -- safe for
    a filename). Returns the trip_id to pass to append_point/end_trip. The
    file appears with the first point."""
    return start_ts


def append_point(trip_id, ts, lat, lon, heading=None, odometer_mi=None, shift_state=None):
    entry = {"ts": ts, "lat": lat, "lon": lon}
    if heading is not None:
        entry["heading"] = heading
    if odometer_mi is not None:
        entry["odometer_mi"] = odometer_mi
    if shift_state is not None:
        entry["shiftState"] = shift_state
    with _lock:
        s = _live.get(trip_id)
        if s is None:
            _live[trip_id] = {"first": entry, "last": entry, "n": 1, "gps_km": 0.0}
        else:
            s["gps_km"] += _haversine_km(s["last"]["lat"], s["last"]["lon"], lat, lon)
            s.update(last=entry, n=s["n"] + 1)
        try:
            if _pending.get(trip_id) and _write_points(trip_id, _pending[trip_id]):
                del _pending[trip_id]
            if trip_id in _pending or not _write_points(trip_id, [entry]):
                # no key yet (the vault was never unlocked since this
                # update): keep it in RAM, never on disk in clear
                if sum(len(v) for v in _pending.values()) < _MAX_PENDING:
                    _pending.setdefault(trip_id, []).append(entry)
        except Exception:
            pass


def end_trip(trip_id):
    """Summary of the trip just finished, from RAM -- the vault is usually
    locked while driving, so the file can't be read back here."""
    with _lock:
        s = _live.pop(trip_id, None)
        _writers.pop(trip_id, None)
    if not s:
        return {"trip_id": trip_id, "points": 0}
    return _summary(trip_id, s["first"], s["last"], s["n"], s["gps_km"])


# ---- reading (unlocked vault) -----------------------------------------------
def _parse_lines(f):
    out = []
    for line in f:
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def _read_points(trip_id, oaep=None):
    """All points of a trip, oldest first. Raises Locked."""
    oaep = oaep or _oaep()
    out = []
    try:
        with open(_trip_path(trip_id, ".jsonl"), encoding="utf-8") as f:   # not converted yet
            out += _parse_lines(f)
    except OSError:
        pass
    try:
        with open(_trip_path(trip_id), "rb") as f:
            data = f.read()
    except OSError:
        return out
    if not data.startswith(MAGIC):
        return out
    key = None
    for kind, payload in _scan(data)[0]:
        try:
            if kind == b"K":
                key = oaep.decrypt(payload)
            elif kind == b"P" and key:
                out.append(json.loads(AES.new(key, AES.MODE_GCM, nonce=payload[:12])
                                      .decrypt_and_verify(payload[28:], payload[12:28])))
        except ValueError:
            if kind == b"K":
                key = None
    return out


def trip_ids():
    """Trip ids, newest first -- file names only, no key needed."""
    ids = {os.path.splitext(os.path.basename(f))[0]
           for pat in ("*.tbx", "*.jsonl") for f in glob.glob(os.path.join(_state_dir, pat))}
    return sorted(ids, reverse=True)


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _summary(trip_id, first, last, n, gps_km):
    distance_km = None
    if "odometer_mi" in first and "odometer_mi" in last:
        try:
            distance_km = (last["odometer_mi"] - first["odometer_mi"]) * 1.60934
        except Exception:
            distance_km = None
    if distance_km is None:
        distance_km = gps_km   # fall back to summing GPS point-to-point distance
    return {
        "trip_id": trip_id,
        "start": first["ts"],
        "end": last["ts"],
        "points": n,
        "distance_km": round(distance_km, 2),
    }


def trip_summary(trip_id, oaep=None):
    points = _read_points(trip_id, oaep)
    if not points:
        return {"trip_id": trip_id, "points": 0}
    gps_km = sum(_haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) for a, b in zip(points, points[1:]))
    return _summary(trip_id, points[0], points[-1], len(points), gps_km)


def _stamp(trip_id):
    st = []
    for ext in (".tbx", ".jsonl"):
        try:
            s = os.stat(_trip_path(trip_id, ext))
            st.append((ext, s.st_size, s.st_mtime_ns))
        except OSError:
            pass
    return tuple(st)


def list_trips():
    """Summaries, newest first. Raises Locked while the vault is locked.
    Each trip is decrypted once per Hub run and whenever its file changes."""
    oaep = _oaep()
    out = []
    for trip_id in trip_ids():
        stamp = _stamp(trip_id)
        hit = _summaries.get(trip_id)
        if not hit or hit[0] != stamp:
            hit = _summaries[trip_id] = (stamp, trip_summary(trip_id, oaep))
        out.append(hit[1])
    return out


# ---- housekeeping -----------------------------------------------------------
def _shred(path):
    """Overwrite, then delete. Best effort: an SSD may keep the old blocks
    around until it reuses them."""
    try:
        size = os.path.getsize(path)
        with open(path, "r+b") as f:
            f.write(b"\0" * size)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    try:
        os.remove(path)
    except OSError:
        pass


def migrate_plaintext():
    """Encrypt every trip still stored as plaintext .jsonl (recorded before
    2026-09-15) and delete the plaintext. A trip that also has an encrypted
    file gets the plaintext points in front of it. Needs the public key
    (ensure_key). Returns the number of trips converted."""
    n = 0
    for legacy in sorted(glob.glob(os.path.join(_state_dir, "*.jsonl"))):
        trip_id = os.path.splitext(os.path.basename(legacy))[0]
        with _lock:
            pub = _get_pub()
            if pub is None:
                return n
            try:
                with open(legacy, encoding="utf-8") as f:
                    points = _parse_lines(f)
            except OSError:
                continue
            path = _trip_path(trip_id)
            blob = MAGIC
            if points:
                key, krec = _new_key_record(pub)
                blob += krec + b"".join(_record(b"P", _seal(key, p)) for p in points)
            try:
                with open(path, "rb") as f:
                    old = f.read()
                if old.startswith(MAGIC):
                    blob += old[len(MAGIC):_scan(old)[1]]
            except OSError:
                pass
            tmp = path + ".new"
            with open(tmp, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            _writers.pop(trip_id, None)   # next point starts with a fresh K record
            _shred(legacy)
            n += 1
    return n


def drop():
    """Vault reset: encrypted trips can't be read without the old private
    key, so they go, with the public key. Plaintext trips not converted yet
    stay and get the new key."""
    global _pub
    with _lock:
        for f in glob.glob(os.path.join(_state_dir, "*.tbx")):
            try:
                os.remove(f)
            except OSError:
                pass
        try:
            os.remove(_pub_path)
        except OSError:
            pass
        _pub = None
        _writers.clear()
        _summaries.clear()


# ---- GPX --------------------------------------------------------------------
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
    that don't know it just ignore it, same as any GPX extension.
    Raises Locked while the vault is locked."""
    points = _read_points(trip_id)
    try:
        dt = datetime.datetime.strptime(trip_id, "%Y-%m-%dT%H-%M-%S")
        name = f"Trip {dt.strftime('%Y-%m-%d %H:%M')}"
    except ValueError:
        name = f"Trip {trip_id}"
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="TeslaCam Hub" '
        'xmlns="http://www.topografix.com/GPX/1/1" '
        'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1" '
        'xmlns:tchub="https://github.com/bernd780/te_camhub">',
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
