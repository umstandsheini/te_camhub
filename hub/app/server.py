#!/usr/bin/env python3
"""
TeslaCam Hub — a single service that replaces the old stitched-together UI
(teslausb nginx/cgi + Te_FITI iframe). It serves one modern SPA over HTTPS with a
session login tied to the encrypted vault, and exposes viewer, files, settings and
diagnostics APIs. The teslausb core (gadget/snapshots/archive) is untouched.

  python server.py --port 443 --cert /mutable/tls/cert.pem --key /mutable/tls/key.pem \
                   --scan /run/teslacam-latest/mnt/TeslaCam --out /dev/shm/teslacam \
                   --state /backingfiles/decrypt-viewer-state [--redirect80]
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ssl, json, argparse, threading, time, secrets, base64, posixpath, hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

from vault import Vault, VaultError
from viewer import Viewer
from tesla_auth import TeslaAuth
import tesla_api, keybridge, hubconf, files as filemod, diag, nassync, mqtt_ha, eventlog, blackbox, canbus, keepawake, videos

WWW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "www")

CFG = {}          # filled in main()
VAULT = None
VIEWER = None
AUTH = None
_sessions = {}    # token -> expiry_ts
_sess_guard = threading.Lock()
_last_activity = time.time()

_bulk_job = {"running": False, "done": 0, "total": 0, "errors": []}
_bulk_guard = threading.Lock()


# ---------- sessions ----------------------------------------------------------
def _new_session():
    tok = secrets.token_urlsafe(24)
    with _sess_guard:
        _sessions[tok] = time.time() + 12 * 3600
    return tok

def _valid_session(tok):
    if not tok:
        return False
    with _sess_guard:
        exp = _sessions.get(tok)
        if not exp:
            return False
        if exp < time.time():
            _sessions.pop(tok, None)
            return False
    return VAULT.is_unlocked()

def _drop_sessions():
    with _sess_guard:
        _sessions.clear()

def _touch():
    global _last_activity
    _last_activity = time.time()

def sync_htpasswd(_pw):  # placeholder hook (no nginx here)
    pass


def autolock_loop():
    while True:
        time.sleep(20)
        try:
            mins = int(hubconf.getval("VAULT_AUTOLOCK_MIN") or "0")
        except ValueError:
            mins = 0
        if mins > 0 and VAULT.is_unlocked() and (time.time() - _last_activity) > mins * 60:
            VAULT.lock(); _drop_sessions(); VIEWER.clear_cache(); VIEWER.invalidate()
            print(f"[hub] auto-locked after {mins} min idle", flush=True)


def key_fetch_loop():
    """Fetch missing FEKs from Tesla once each into the vault (unlocked
    only), then mirror every currently-known key onto the Pi's own local
    TeslaCam Samba export as a sealed sidecar (see
    nassync.push_key_sidecars_local) -- independent of whether a NAS is
    configured, since that's a separate/optional destination
    (nas_sync_loop's push_key_sidecars targets the NAS, not this). Also
    generates any thumbnails that have become possible (new key, or a plain
    clip that was never viewed) -- see Viewer.ensure_thumbnails -- so the
    clip grid has images without anyone having opened it first."""
    while True:
        time.sleep(60)
        try:
            if VAULT.is_unlocked():
                if AUTH.get_access_token():
                    items = keybridge.scan_items(CFG["src"], VAULT.keys())
                    if items:
                        got = 0
                        for i in range(0, len(items), 30):
                            try:
                                res = tesla_api.fetch_keys(items[i:i + 30], AUTH.get_access_token())
                            except tesla_api.DecryptApiError:
                                break
                            got += VAULT.merge_keys(res)
                        if got:
                            VIEWER.invalidate()
                            print(f"[hub] fetched {got} new keys", flush=True)
                r = nassync.push_key_sidecars_local(VAULT)
                if r.get("written"):
                    print(f"[hub] local key sidecars: {r['written']} neu geschrieben", flush=True)
            made = VIEWER.ensure_thumbnails()
            if made:
                print(f"[hub] background thumbnails: {made} neu erzeugt", flush=True)
        except Exception as e:
            print("[hub] key fetch:", e, flush=True)


def _fetch_keys_for_clip(cid):
    """Try to fetch any missing FEKs for this clip's cameras right now (used
    when the user opens a clip, instead of waiting for the 60s background
    loop). Silently does nothing if the vault is locked or there's no valid
    Tesla token -- prepare() will then just report the cameras as locked."""
    if not (VAULT.is_unlocked() and AUTH.get_access_token()):
        return 0
    keys = VAULT.keys()
    items = []
    for path in VIEWER.clip_paths(cid).values():
        if not os.path.isfile(path):
            continue
        eid = keybridge.clip_id(CFG["src"], path)
        if eid in keys:
            continue
        try:
            with open(path, "rb") as f:
                head = f.read(keybridge.HEADER_SIZE)
        except OSError:
            continue
        if not keybridge.is_ecryptfs(head):
            continue
        try:
            wk = keybridge.parse_wrapped_key(head)
        except Exception:
            continue
        wk["id"] = eid
        items.append(wk)
    if not items:
        return 0
    try:
        res = tesla_api.fetch_keys(items, AUTH.get_access_token())
    except tesla_api.DecryptApiError:
        return 0
    got = VAULT.merge_keys(res)
    if got:
        VIEWER.invalidate()
    return got


def nas_sync_loop():
    """Periodically refresh the local-vs-NAS archive coverage percentage and
    push any newly-known per-video key sidecars to the NAS."""
    while True:
        try:
            nassync.refresh_status(CFG["scan"])
            if VAULT.is_unlocked():
                nassync.push_key_sidecars(CFG["scan"], VAULT)
                if hubconf.getval("NAS_RAW_KEYS") == "true":
                    nassync.push_raw_keys(CFG["scan"], VAULT, CFG["state"])
            if hubconf.getval("SYNC_ALL_CONTENT") == "true":
                nassync.sync_media()
            if hubconf.getval("BLACKBOX_ENABLED") == "true" and hubconf.getval("SYNC_TRIPS_ENABLED") != "false":
                nassync.sync_trips(_trip["trip_id"] if _trip["active"] else None)
        except Exception as e:
            print("[hub] nas sync:", e, flush=True)
        time.sleep(600)


def _ble_mqtt_command(action_id, value):
    """Called from mqtt_ha's paho thread when an HA button/number entity for
    a BLE action fires. Only ever the single supported key name 'awake' --
    same one the Fahrzeug(BLE) UI uses."""
    try:
        r = diag.ble_exec("awake", action_id, value=value)
        print(f"[hub] mqtt ble command {action_id}:", r, flush=True)
        if r.get("ok") and value is not None:
            mqtt_ha.publish_ble_action_state(action_id, value)
    except Exception as e:
        print("[hub] mqtt ble command error:", e, flush=True)


mqtt_ha.set_command_handler(_ble_mqtt_command)


_ble_mqtt_all_failing = False


def ble_mqtt_loop():
    """Publish BLE sensor readings to Home Assistant every 15 minutes --
    much less often than mqtt_loop's other sensors, since each read is a
    real BLE round-trip to the vehicle, not a local getval() check.

    Deliberately does NOT gate on ble_status_role()'s session-info check
    first (unlike the Hub UI's "gekoppelt?" indicator): that check has been
    observed to report paired=False even while plain reads (ping, state
    charge, ...) succeed seconds later against the same key -- it's a
    stricter/different probe than an actual read needs. Each read's own
    success/failure is what decides whether it gets published.

    If every read in a cycle fails, that's a real (not just cosmetic)
    outage -- HA is left showing stale last-known values with no
    indication why. Log it once on the transition into/out of "all
    failing" (not every 15 min while it stays down) so it's visible in the
    Ereignis-Log instead of only in the Hub's own stdout/journal."""
    global _ble_mqtt_all_failing
    time.sleep(35)  # let mqtt_loop's own connect-and-discover cycle land first
    while True:
        try:
            if hubconf.getval("MQTT_ENABLED") == "true":
                reads, _actions = diag.ble_available_commands()
                any_ok, last_err = False, None
                for read_id in reads:
                    r = diag.ble_read("awake", read_id)
                    if r.get("ok"):
                        any_ok = True
                        mqtt_ha.publish_ble_read(read_id, r.get("values") or {})
                    else:
                        last_err = r.get("error")
                if reads:
                    if not any_ok and not _ble_mqtt_all_failing:
                        eventlog.log_event("ble", f"BLE-Fahrzeugdaten aktuell nicht abrufbar: {last_err or 'unbekannter Fehler'}")
                        _ble_mqtt_all_failing = True
                    elif any_ok and _ble_mqtt_all_failing:
                        eventlog.log_event("ble", "BLE-Fahrzeugdaten wieder abrufbar")
                        _ble_mqtt_all_failing = False
        except Exception as e:
            print("[hub] ble mqtt:", e, flush=True)
        time.sleep(900)


def mqtt_loop():
    """Publish Hub status to Home Assistant via MQTT Discovery every 30s.
    No-op (cheap getval() checks only) unless MQTT_ENABLED=true."""
    while True:
        try:
            if hubconf.getval("MQTT_ENABLED") == "true":
                host = hubconf.getval("MQTT_HOST")
                if mqtt_ha.ensure_connected(host, hubconf.getval("MQTT_PORT") or 1883,
                                             hubconf.getval("MQTT_USER"), hubconf.getval("MQTT_PASSWORD")):
                    counts = VIEWER.counts()
                    st = diag.status()
                    nas = nassync.status()
                    mqtt_ha.publish_state({
                        "clips": counts.get("clips", 0),
                        "encrypted": counts.get("encrypted", 0),
                        "nas_percent": nas.get("percent", 0),
                        "temp": (st.get("temp") or "").replace("'C", "").strip(),
                        "wifi_ssid": st.get("wifi_ssid") or "–",
                        "usb_connected": bool(st.get("gadget_active")),
                        "vault_unlocked": VAULT.is_unlocked(),
                    })
            else:
                mqtt_ha.disconnect()
        except Exception as e:
            print("[hub] mqtt:", e, flush=True)
        time.sleep(30)


def temp_log_loop():
    """Write the Pi's temperature to temperature.log once a minute, and log
    a discrete event (not just the routine per-minute line) whenever it
    crosses a hot/cold-again threshold, so the event log stays readable."""
    was_hot = False
    while True:
        try:
            st = diag.status()
            raw = (st.get("temp") or "").replace("'C", "").strip()
            if raw:
                temp = float(raw)
                eventlog.log_temperature(temp)
                if temp >= 75 and not was_hot:
                    eventlog.log_event("temp", f"Pi-Temperatur hoch: {temp:.1f}°C", temp=temp)
                    was_hot = True
                elif temp < 70 and was_hot:
                    eventlog.log_event("temp", f"Pi-Temperatur wieder normal: {temp:.1f}°C", temp=temp)
                    was_hot = False
        except Exception as e:
            print("[hub] temp log:", e, flush=True)
        time.sleep(60)


def keepawake_loop():
    """Sends the periodic BLE 'wake' nudges that keep the car from sleeping
    while the switch is active (see keepawake.py for why a one-shot command
    isn't enough), and auto-turns the switch back off once its expiry
    passes. State lives on disk, so this also catches an expiry that fell
    due while the Hub was restarting/rebooting."""
    while True:
        try:
            r = keepawake.tick()
            if r is not None:
                ev = r.get("event")
                if ev == "expired":
                    eventlog.log_event("keepawake", "Wach halten automatisch beendet (Zeit abgelaufen)")
                elif ev == "nudge_failed":
                    eventlog.log_event("keepawake", f"Wake-Nudge schlägt fehl: {r.get('error') or 'unbekannter Fehler'}")
                elif ev == "nudge_recovered":
                    eventlog.log_event("keepawake", "Wake-Nudge funktioniert wieder")
        except Exception as e:
            print("[hub] keepawake loop:", e, flush=True)
        time.sleep(60)


def connectivity_log_loop():
    """Always-on, BLE-independent event log: WiFi/USB connectivity
    transitions. This is the "ohne BLE weniger" baseline -- coarse but
    needs nothing beyond what diag.status() already reads locally."""
    last_wifi = None
    last_usb = None
    while True:
        try:
            st = diag.status()
            wifi = st.get("wifi_ssid") or None
            if wifi != last_wifi:
                if wifi:
                    eventlog.log_event("wifi", f"WLAN verbunden: {wifi}")
                elif last_wifi is not None:
                    eventlog.log_event("wifi", f"WLAN getrennt (war: {last_wifi})")
                last_wifi = wifi
            usb = bool(st.get("gadget_active"))
            if last_usb is not None and usb != last_usb:
                eventlog.log_event("usb", "USB-Gadget verbunden" if usb else "USB-Gadget getrennt")
            last_usb = usb
        except Exception as e:
            print("[hub] connectivity log:", e, flush=True)
        time.sleep(20)


# Trip detection/blackbox state, owned by trip_watch_loop only.
_trip = {"active": False, "trip_id": None, "start_ts": None, "start_odometer": None,
         "locked": None, "asleep": None, "charging": None}


def _trip_tick_idle():
    """Out-of-trip cadence: cheap-ish drive-state poll to notice departure."""
    r = diag.ble_read("awake", "drive")
    if not r.get("ok"):
        return
    shift = (r.get("values") or {}).get("driveState.shiftState")
    if shift and shift not in ("Park", "Invalid"):
        _start_trip()


def _start_trip():
    ts = time.strftime("%Y-%m-%dT%H-%M-%S")
    trip_id = blackbox.start_trip(ts)
    _trip.update(active=True, trip_id=trip_id, start_ts=ts,
                  start_odometer=None, locked=None, asleep=None, charging=None)
    eventlog.log_event("trip", "Fahrt gestartet")


def _end_trip():
    summary = blackbox.trip_summary(_trip["trip_id"]) if _trip["trip_id"] else {}
    dist = summary.get("distance_km")
    msg = "Fahrt beendet"
    if dist is not None:
        msg += f" ({dist:.1f} km)"
    eventlog.log_event("trip", msg, **{k: v for k, v in summary.items() if k != "trip_id"})
    _trip.update(active=False, trip_id=None, start_ts=None,
                  start_odometer=None, locked=None, asleep=None, charging=None)


def _trip_tick_active():
    """In-trip cadence (every 10s): one location+drive read for the
    blackbox point, occasional (~every 3rd tick) closures/body_controller/
    charge reads for richer sub-events -- avoids hammering BLE with every
    category every 10s while still catching lock/sleep/charging changes
    during the drive."""
    drive = diag.ble_read("awake", "drive")
    loc = diag.ble_read("awake", "location")
    if not drive.get("ok") or not loc.get("ok"):
        return
    dv, lv = drive.get("values") or {}, loc.get("values") or {}
    shift = dv.get("driveState.shiftState")
    odometer = dv.get("driveState.odometerInHundredthsOfAMile")
    odometer_mi = (odometer / 100.0) if isinstance(odometer, (int, float)) else None
    lat, lon = lv.get("latitude"), lv.get("longitude")
    if lat is not None and lon is not None:
        blackbox.append_point(_trip["trip_id"], time.strftime("%Y-%m-%dT%H:%M:%S"),
                               lat, lon, heading=lv.get("heading"),
                               odometer_mi=odometer_mi, shift_state=shift)
    if shift == "Park":
        _end_trip()
        return

    global _trip_subcheck_counter
    _trip_subcheck_counter = (_trip_subcheck_counter + 1) % 3
    if _trip_subcheck_counter != 0:
        return
    cl = diag.ble_read("awake", "closures")
    if cl.get("ok"):
        locked = (cl.get("values") or {}).get("locked")
        if _trip["locked"] is not None and locked != _trip["locked"]:
            eventlog.log_event("trip", "Verriegelt" if locked else "Entriegelt", während_fahrt=True)
        _trip["locked"] = locked
    ch = diag.ble_read("awake", "charge")
    if ch.get("ok"):
        charging = (ch.get("values") or {}).get("chargingState")
        if _trip["charging"] is not None and charging != _trip["charging"] and charging:
            eventlog.log_event("trip", f"Ladezustand: {charging}", während_fahrt=True)
        _trip["charging"] = charging


_trip_subcheck_counter = 0


def trip_watch_loop():
    """Automatic trip detection + blackbox recording, gated behind
    BLACKBOX_ENABLED. Idle cadence 30s (just watching for departure),
    active cadence 10s (recording the actual trip) -- matches the
    explicitly requested "10s while driving, don't hammer BLE while
    parked" trade-off."""
    while True:
        try:
            if hubconf.getval("BLACKBOX_ENABLED") == "true":
                if _trip["active"]:
                    _trip_tick_active()
                else:
                    _trip_tick_idle()
        except Exception as e:
            print("[hub] trip watch:", e, flush=True)
        time.sleep(10 if _trip["active"] else 30)


def _bulk_worker():
    def progress(done, total, _cid):
        with _bulk_guard:
            _bulk_job["done"] = done
            _bulk_job["total"] = total
    try:
        res = VIEWER.bulk_prepare(on_progress=progress)
        with _bulk_guard:
            _bulk_job["errors"] = res.get("errors", [])
    except Exception as e:
        with _bulk_guard:
            _bulk_job["errors"].append(str(e))
    finally:
        with _bulk_guard:
            _bulk_job["running"] = False


# ---------- HTTP handler ------------------------------------------------------
class H(BaseHTTPRequestHandler):
    server_version = "TeslaCamHub"

    def log_message(self, *a):
        pass

    # -- helpers --
    def _cookie(self, name):
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def _auth_ok(self):
        return _valid_session(self._cookie("hub_session"))

    def _json(self, code, obj, extra=None):
        body = json.dumps(obj).encode()
        self._raw(code, body, "application/json", extra)

    def _raw(self, code, body, ctype, extra=None):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _sendfile(self, path, ctype, extra=None):
        size = os.path.getsize(path)
        rng = self.headers.get("Range")
        with open(path, "rb") as f:
            if rng and rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                start = int(a) if a else 0
                end = int(b) if b else size - 1
                end = min(end, size - 1)
                f.seek(start); chunk = f.read(end - start + 1)
                try:
                    self.send_response(206)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.send_header("Content-Length", str(len(chunk)))
                    for k, v in (extra or {}).items():
                        self.send_header(k, v)
                    self.end_headers(); self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Length", str(size))
                    for k, v in (extra or {}).items():
                        self.send_header(k, v)
                    self.end_headers()
                    while True:
                        chunk = f.read(1 << 20)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(n) if n else b""

    def _qs(self, key):
        return parse_qs(urlparse(self.path).query).get(key, [""])[0]

    # -- routing --
    def do_GET(self):
        path = urlparse(self.path).path
        # static SPA
        if path == "/" or path == "/index.html":
            return self._sendfile(os.path.join(WWW, "index.html"), "text/html",
                                  {"Cache-Control": "no-store"})
        if path.startswith("/static/"):
            fp = os.path.join(WWW, os.path.basename(path))
            if os.path.isfile(fp):
                ct = ("text/css" if fp.endswith(".css") else
                      "application/javascript" if fp.endswith(".js") else
                      "image/png" if fp.endswith(".png") else "application/octet-stream")
                return self._sendfile(fp, ct, {"Cache-Control": "max-age=86400"})
            return self._json(404, {"error": "not found"})
        # public
        if path == "/api/vault/status":
            return self._json(200, {"has_vault": VAULT.has_vault(),
                                    "unlocked": VAULT.is_unlocked(),
                                    "session": self._auth_ok()})
        # everything else needs a session
        if not self._auth_ok():
            return self._json(401, {"error": "auth"})
        _touch()

        if path == "/api/status":
            st = VIEWER.counts()
            st["login"] = AUTH.status()
            st["diag"] = diag.status()
            return self._json(200, st)
        if path == "/api/clips":
            return self._json(200, VIEWER.clips())
        if path == "/api/all_gps":
            return self._json(200, {"points": VIEWER.all_gps()})
        if path == "/api/trips":
            return self._json(200, VIEWER.trips())
        if path == "/api/event":
            ev = VIEWER.event_data(self._qs("id"))
            if ev is None:
                return self._json(404, {"error": "no event"})
            return self._json(200, ev)
        if path == "/api/thumb":
            t = VIEWER.make_thumb(self._qs("id"))
            if not t:
                return self._json(404, {"error": "no thumb"})
            return self._sendfile(t, "image/png" if t.endswith(".png") else "image/jpeg",
                                  {"Cache-Control": "max-age=86400"})
        if path.startswith("/media/"):
            full = VIEWER.resolve_media(path[len("/media/"):])
            if not full:
                return self._json(404, {"error": "not found"})
            ct = ("video/mp4" if full.endswith(".mp4") else
                  "application/json" if full.endswith(".json") else
                  "image/png" if full.endswith(".png") else "application/octet-stream")
            return self._sendfile(full, ct)
        if path == "/api/videos":
            return self._json(200, {"videos": videos.list_videos()})
        if path == "/api/videos/status":
            return self._json(200, videos.status(unquote(self._qs("name") or "")))
        if path.startswith("/media/videos/"):
            name = unquote(path[len("/media/videos/"):])
            full = videos.resolve(name)
            if not full:
                return self._json(404, {"error": "not ready"})
            return self._sendfile(full, "video/mp4")
        if path == "/api/settings":
            return self._json(200, hubconf.read_settings())
        if path == "/api/files":
            entries = filemod.listdir(self._qs("path"))
            if entries is None:
                return self._json(404, {"error": "not found"})
            return self._json(200, {"roots": filemod.roots(), "entries": entries,
                                    "path": self._qs("path")})
        if path == "/api/files/download":
            full = filemod.resolve(self._qs("path"))
            if not full:
                return self._json(404, {"error": "not found"})
            name = os.path.basename(full)
            nl = name.lower()
            ct = ("image/jpeg" if nl.endswith((".jpg", ".jpeg")) else
                  "image/png" if nl.endswith(".png") else
                  "audio/mpeg" if nl.endswith(".mp3") else
                  "audio/wav" if nl.endswith(".wav") else
                  "audio/mp4" if nl.endswith(".m4a") else
                  "audio/ogg" if nl.endswith(".ogg") else
                  "audio/flac" if nl.endswith(".flac") else
                  "audio/aac" if nl.endswith(".aac") else "application/octet-stream")
            disp = None if self._qs("inline") else {"Content-Disposition": f'attachment; filename="{name}"'}
            return self._sendfile(full, ct, disp)
        if path == "/api/log":
            return self._json(200, {"text": diag.tail_log(self._qs("which"))})
        if path == "/api/tesla/login_url":
            return self._json(200, {"url": AUTH.make_login_url()})
        if path == "/api/nas/test":
            return self._json(200, hubconf.test_nas())
        if path == "/api/bulk_prepare":
            with _bulk_guard:
                return self._json(200, dict(_bulk_job))
        if path == "/api/nas/sync_status":
            return self._json(200, nassync.status())
        if path == "/api/nas/archive_progress":
            return self._json(200, nassync.archive_progress())
        if path == "/api/nas/media_status":
            return self._json(200, nassync.media_status())
        if path == "/api/nas/trips_status":
            return self._json(200, nassync.trips_status())
        if path == "/api/ble/status":
            return self._json(200, diag.ble_status_role(self._qs("name")))
        if path == "/api/keepawake/status":
            return self._json(200, keepawake.status())
        if path == "/api/canbus/monitor/status":
            return self._json(200, canbus.monitor_status())
        if path == "/api/ap_fallback/status":
            return self._json(200, diag.ap_fallback_status())
        if path == "/api/ap_usb/status":
            return self._json(200, diag.ap_usb_status())
        if path == "/api/samba/status":
            return self._json(200, diag.samba_status())
        if path == "/api/backup/export":
            if not os.path.isfile(hubconf.CONF):
                return self._json(404, {"error": "not found"})
            return self._sendfile(hubconf.CONF, "text/plain",
                                   {"Content-Disposition": 'attachment; filename="teslausb_setup_variables.conf"'})
        if path == "/api/hotspot/status":
            return self._json(200, diag.hotspot_wifi_status())
        if path == "/api/wireguard/status":
            return self._json(200, diag.wireguard_status())
        if path == "/api/nas/raw_keys/pairing":
            return self._json(200, nassync.pairing_status(CFG["state"]))
        if path == "/api/ble/commands":
            reads, actions = diag.ble_available_commands()
            return self._json(200, {
                "reads": [{"id": i, "label": l} for i, (l, _a) in reads.items()],
                "actions": [{"id": i, "label": l} for i, (l, _a) in actions.items()],
            })
        if path == "/api/events":
            try:
                limit = int(self._qs("limit") or "200")
            except ValueError:
                limit = 200
            return self._json(200, {"events": eventlog.read_events(limit)})
        if path == "/api/temperature":
            try:
                limit = int(self._qs("limit") or "1440")
            except ValueError:
                limit = 1440
            return self._json(200, {"points": eventlog.read_temperature(limit)})
        if path == "/api/temperature/download":
            p = eventlog.temperature_log_path()
            if not os.path.isfile(p):
                return self._json(404, {"error": "not found"})
            return self._sendfile(p, "text/csv",
                                   {"Content-Disposition": 'attachment; filename="temperature.log"'})
        if path == "/api/blackbox/trips":
            return self._json(200, {"trips": blackbox.list_trips(),
                                     "active": _trip["active"]})
        if path == "/api/blackbox/export":
            trip_id = self._qs("trip") or ""
            if not trip_id or "/" in trip_id or "\\" in trip_id:
                return self._json(404, {"error": "not found"})
            try:
                gpx = blackbox.to_gpx(trip_id)
            except Exception as e:
                print(f"[hub] GPX export failed for {trip_id}: {e}", flush=True)
                return self._json(500, {"error": "export failed"})
            body = gpx.encode("utf-8")
            return self._raw(200, body, "application/gpx+xml",
                              {"Content-Disposition": f'attachment; filename="{trip_id}.gpx"'})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = json.loads(self._body() or b"{}")
        except Exception:
            body = {}

        # public auth endpoints
        if path == "/api/setup":
            if VAULT.has_vault():
                return self._json(409, {"error": "vault exists"})
            pw = body.get("pass", "")
            if not pw:
                return self._json(400, {"error": "leeres Passwort"})
            imp_k, imp_t = _import_legacy() if body.get("import") else ({}, {})
            VAULT.create(pw, import_keys=imp_k, import_token=imp_t)
            tok = _new_session(); _touch(); VIEWER.invalidate()
            return self._json(200, {"ok": True, "imported": len(imp_k)}, self._setcookie(tok))
        if path == "/api/vault/factory_reset":
            if not VAULT.has_vault():
                return self._json(200, {"ok": False, "error": "kein Tresor vorhanden"})
            if body.get("confirm") != "ZURUECKSETZEN":
                return self._json(200, {"ok": False, "error": "Bestätigung fehlt"})
            VAULT.factory_reset()
            hubconf.clear_secrets()
            _drop_sessions(); VIEWER.clear_cache(); VIEWER.invalidate()
            return self._json(200, {"ok": True})
        if path == "/api/login":
            if VAULT.unlock_with_pass(body.get("pass", "")):
                tok = _new_session(); _touch(); VIEWER.invalidate()
                return self._json(200, {"ok": True}, self._setcookie(tok))
            return self._json(200, {"ok": False, "error": "falsches Passwort"})
        if path == "/api/logout":
            _drop_sessions(); VAULT.lock(); VIEWER.clear_cache()
            return self._json(200, {"ok": True})

        if not self._auth_ok():
            return self._json(401, {"error": "auth"})
        _touch()

        if path == "/api/vault/change_pass":
            old, new = body.get("old", ""), body.get("new", "")
            if not new:
                return self._json(400, {"ok": False, "error": "neues Passwort fehlt"})
            try:
                if not VAULT.change_pass(old, new):
                    return self._json(400, {"ok": False, "error": "aktuelles Passwort falsch"})
                return self._json(200, {"ok": True})
            except VaultError as e:
                return self._json(400, {"ok": False, "error": str(e)})
        if path == "/api/system/ssh_password":
            try:
                return self._json(200, diag.set_ssh_password(body.get("password", "")))
            except Exception as e:
                return self._json(200, {"ok": False, "error": str(e)[:300]})
        if path == "/api/system/samba_password":
            try:
                return self._json(200, diag.set_samba_password(body.get("password", "")))
            except Exception as e:
                return self._json(200, {"ok": False, "error": str(e)[:300]})
        if path == "/api/backup/import":
            ok, err = hubconf.import_conf(body.get("content", ""))
            return self._json(200 if ok else 400, {"ok": ok, "error": err})
        if path == "/api/prepare":
            cid = body.get("id", "")
            _fetch_keys_for_clip(cid)
            return self._json(200, VIEWER.prepare(cid))
        if path == "/api/bulk_prepare":
            with _bulk_guard:
                if _bulk_job["running"]:
                    return self._json(200, dict(_bulk_job))
                _bulk_job.update(running=True, done=0, total=len(VIEWER.bulk_targets()), errors=[])
            threading.Thread(target=_bulk_worker, daemon=True).start()
            with _bulk_guard:
                return self._json(200, dict(_bulk_job))
        if path == "/api/settings":
            ok, err = hubconf.write_settings(body)
            if ok and "ssh_disable_password" in body:
                diag.apply_ssh(str(body.get("ssh_disable_password")) in ("true", "True", "1", "on"))
            if ok and "samba_enabled" in body:
                sr = diag.apply_samba(str(body.get("samba_enabled")) in ("true", "True", "1", "on"))
                if not sr.get("ok"):
                    ok = False
                    err = sr.get("error")
            if ok and "ap_fallback_only" in body:
                enabled = str(body.get("ap_fallback_only")) in ("true", "True", "1", "on")
                cur = hubconf.read_settings()
                # ap_pass is a SECRETS field: the frontend omits it from the request
                # whenever the user leaves the (masked) password box untouched -- which
                # is the normal case when this save is just toggling the checkbox after
                # SSID/password were already saved earlier. Falling back to the stored
                # value (server-side only, never echoed to the client) instead of
                # treating "not in this request" as "no password" -- otherwise
                # apply_ap_fallback() sees password=None on first enable, can't create
                # the TESLAUSB_AP profile, and the fallback silently never activates.
                pw = body.get("ap_pass") or hubconf.getval("AP_PASS") or None
                apr = diag.apply_ap_fallback(enabled, ssid=cur.get("ap_ssid"),
                                              password=pw, ap_ip=cur.get("ap_ip"))
                if not apr.get("ok"):
                    ok = False
                    err = apr.get("error")
            if ok and "ap_on_usb" in body:
                enabled = str(body.get("ap_on_usb")) in ("true", "True", "1", "on")
                cur = hubconf.read_settings()
                pw = body.get("ap_pass") or hubconf.getval("AP_PASS") or None
                aur = diag.apply_ap_on_usb(enabled, ssid=cur.get("ap_ssid"),
                                            password=pw, ap_ip=cur.get("ap_ip"))
                if not aur.get("ok"):
                    ok = False
                    err = aur.get("error")
            if ok and "hotspot_enabled" in body:
                enabled = str(body.get("hotspot_enabled")) in ("true", "True", "1", "on")
                cur = hubconf.read_settings()
                # same reasoning as ap_pass above: hotspot_pass is a SECRETS field and
                # gets omitted by the frontend once already saved, so fall back to the
                # stored value rather than treating "not in this request" as "no password".
                pw = body.get("hotspot_pass") or hubconf.getval("HOTSPOT_PASS") or None
                hr = diag.apply_hotspot_wifi(enabled, ssid=cur.get("hotspot_ssid"), password=pw)
                if not hr.get("ok"):
                    ok = False
                    err = hr.get("error")
            if ok and "wg_enabled" in body:
                enabled = str(body.get("wg_enabled")) in ("true", "True", "1", "on")
                cur = hubconf.read_settings()
                psk = body.get("wg_psk") or hubconf.getval("WG_PSK") or None
                privkey = body.get("wg_privkey") or hubconf.getval("WG_PRIVKEY") or None
                wr = diag.apply_wireguard(enabled, peer_pubkey=cur.get("wg_peer_pubkey"),
                                           endpoint=cur.get("wg_endpoint"), allowed_ips=cur.get("wg_allowed_ips"),
                                           address=cur.get("wg_address"), keepalive=cur.get("wg_keepalive"),
                                           psk=psk, privkey=privkey, dns=cur.get("wg_dns"))
                if not wr.get("ok"):
                    ok = False
                    err = wr.get("error")
            return self._json(200 if ok else 400, {"ok": ok, "error": err})
        if path == "/api/wireguard/import_qr":
            img = body.get("image", "") or ""
            if img.startswith("data:") and "," in img:
                img = img.split(",", 1)[1]
            return self._json(200, diag.import_wg_qr(img))
        if path == "/api/files/mkdir":
            filemod.mkdir(body.get("path", "")); return self._json(200, {"ok": True})
        if path == "/api/files/delete":
            filemod.delete(body.get("path", "")); return self._json(200, {"ok": True})
        if path == "/api/files/rename":
            filemod.rename(body.get("path", ""), body.get("name", "")); return self._json(200, {"ok": True})
        if path == "/api/files/move":
            filemod.move(body.get("path", ""), body.get("dest", "")); return self._json(200, {"ok": True})
        if path == "/api/files/lockchime":
            try:
                filemod.set_lockchime(body.get("path", ""))
                return self._json(200, {"ok": True})
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})
        if path == "/api/videos/prepare":
            try:
                return self._json(200, videos.prepare(body.get("name", "")))
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})
        if path == "/api/videos/delete_cache":
            videos.delete_cache(body.get("name", "")); return self._json(200, {"ok": True})
        if path == "/api/reboot":
            return self._json(200, diag.reboot())
        if path == "/api/toggle_drives":
            return self._json(200, diag.toggle_drives())
        if path == "/api/sync":
            return self._json(200, diag.trigger_sync())
        if path == "/api/ble/install":
            try:
                return self._json(200, diag.install_ble_binaries())
            except Exception as e:
                return self._json(200, {"ok": False, "error": str(e)[:300]})
        if path == "/api/ble/pair":
            try:
                return self._json(200, diag.ble_pair_role(body.get("name", ""), body.get("role", "")))
            except Exception as e:
                return self._json(200, {"ok": False, "error": str(e)[:300]})
        if path == "/api/ble/read":
            try:
                return self._json(200, diag.ble_read(body.get("name", ""), body.get("id", "")))
            except Exception as e:
                return self._json(200, {"ok": False, "error": str(e)[:300]})
        if path == "/api/ble/exec":
            try:
                return self._json(200, diag.ble_exec(body.get("name", ""), body.get("id", ""), body.get("value")))
            except Exception as e:
                return self._json(200, {"ok": False, "error": str(e)[:300]})
        if path == "/api/ble/reset_unavailable":
            return self._json(200, diag.ble_reset_unavailable())
        if path == "/api/keepawake/start":
            r = keepawake.start(body.get("hours"))
            if r.get("ok"):
                eventlog.log_event("keepawake", f"Wach halten aktiviert ({r.get('hours'):.1f}h, alle 5 Min. Wake-Nudge)")
            return self._json(200, r)
        if path == "/api/keepawake/stop":
            r = keepawake.stop()
            eventlog.log_event("keepawake", "Wach halten beendet")
            return self._json(200, r)
        if path == "/api/canbus/read":
            try:
                dur = int(body.get("duration") or 5)
            except (TypeError, ValueError):
                dur = 5
            return self._json(200, canbus.read(dur))
        if path == "/api/canbus/monitor/start":
            return self._json(200, canbus.start_monitor())
        if path == "/api/canbus/monitor/stop":
            return self._json(200, canbus.stop_monitor())
        if path == "/api/canbus/write_action":
            return self._json(200, canbus.write_action(body.get("id", ""), confirm=bool(body.get("confirm"))))
        if path == "/api/canbus/write_raw":
            return self._json(200, canbus.write_raw(body.get("can_id", ""), body.get("data", ""),
                                                      confirm=bool(body.get("confirm"))))
        if path == "/api/nas/sync_status/refresh":
            threading.Thread(target=lambda: nassync.refresh_status(CFG["scan"]), daemon=True).start()
            return self._json(200, {"ok": True})
        if path == "/api/nas/raw_keys/push":
            return self._json(200, nassync.push_raw_keys(CFG["scan"], VAULT, CFG["state"]))
        if path == "/api/nas/raw_keys/reset_pairing":
            return self._json(200, nassync.reset_pairing(CFG["state"]))
        if path == "/api/nas/sync_media":
            threading.Thread(target=nassync.sync_media, daemon=True).start()
            return self._json(200, {"ok": True})
        if path == "/api/nas/sync_trips":
            active = _trip["trip_id"] if _trip["active"] else None
            threading.Thread(target=nassync.sync_trips, args=(active,), daemon=True).start()
            return self._json(200, {"ok": True})
        if path == "/api/tesla/exchange":
            try:
                tok = AUTH.exchange_code(body.get("callback", ""))
                return self._json(200, {"ok": True, "refresh": bool(tok.get("refresh_token"))})
            except Exception as e:
                return self._json(400, {"ok": False, "error": str(e)})
        return self._json(404, {"error": "not found"})

    def do_PUT(self):
        # raw streaming upload: PUT /api/files/upload?path=<dir>&name=<file>
        path = urlparse(self.path).path
        if path != "/api/files/upload":
            return self._json(404, {"error": "not found"})
        if not self._auth_ok():
            return self._json(401, {"error": "auth"})
        _touch()
        destrel = self._qs("path"); name = self._qs("name")
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            saved = filemod.save_upload(destrel, name, _Limited(self.rfile, n))
            return self._json(200, {"ok": True, "name": saved})
        except Exception as e:
            return self._json(400, {"ok": False, "error": str(e)})

    def _setcookie(self, tok):
        secure = "; Secure" if CFG.get("tls") else ""
        return {"Set-Cookie": f"hub_session={tok}; HttpOnly; SameSite=Lax; Path=/{secure}"}


class _Limited:
    """Read exactly n bytes from a stream (for Content-Length uploads)."""
    def __init__(self, fp, n): self.fp, self.n = fp, n
    def read(self, sz=-1):
        if self.n <= 0:
            return b""
        want = self.n if sz < 0 else min(sz, self.n)
        data = self.fp.read(want)
        self.n -= len(data)
        return data


def _import_legacy():
    keys, tok = {}, {}
    state = CFG["state"]
    kp = os.path.join(state, "teslacam_keys.json")
    if os.path.isfile(kp):
        try:
            keys = json.load(open(kp, encoding="utf-8")) or {}
        except Exception:
            keys = {}
    tp = os.path.join(state, "token_store.json")
    if os.path.isfile(tp):
        try:
            tok = json.load(open(tp, encoding="utf-8")) or {}
        except Exception:
            tok = {}
    return keys, tok


def _redirect80():
    class R(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            # Fallback-AP-Landingpage: Clients, die über die AP-eigene IP
            # verbunden sind (kein echtes Internet dahinter), zur eigenen
            # AP-IP umleiten statt den vom Client mitgeschickten Host zu
            # reflektieren -- sonst laufen iOS/Android-Captive-Portal-Checks
            # (die eine externe URL wie captive.apple.com anfragen) ins
            # Leere, und das Betriebssystem öffnet nie den
            # "Bei Netzwerk anmelden"-Dialog. Jede vom erwarteten Ergebnis
            # abweichende Antwort auf diese Probe-Requests reicht dafür.
            # Normales Heim-WLAN/-Netz (andere lokale IP) bleibt unverändert:
            # dort wird weiterhin der angefragte Host reflektiert.
            try:
                local_ip = self.connection.getsockname()[0]
            except Exception:
                local_ip = ""
            ap_ip = (hubconf.getval("AP_IP") or "192.168.66.1").strip()
            if local_ip == ap_ip:
                target = f"https://{ap_ip}/"
            else:
                host = (self.headers.get("Host", "") or "").split(":")[0]
                target = f"https://{host}{self.path}"
            self.send_response(302 if local_ip == ap_ip else 301)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()
        do_POST = do_GET
    try:
        ThreadingHTTPServer(("0.0.0.0", 80), R).serve_forever()
    except Exception as e:
        print("[hub] port80 redirect unavailable:", e, flush=True)


def main():
    global VAULT, VIEWER, AUTH, CFG
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=443)
    p.add_argument("--cert"); p.add_argument("--key")
    p.add_argument("--scan", required=True)
    p.add_argument("--out", default="/dev/shm/teslacam")
    p.add_argument("--state", default="/backingfiles/decrypt-viewer-state")
    p.add_argument("--redirect80", action="store_true")
    a = p.parse_args()
    os.makedirs(a.out, exist_ok=True); os.makedirs(a.state, exist_ok=True)
    src = os.path.join(a.scan, "EncryptedClips")
    CFG = {"scan": a.scan, "src": src, "out": a.out, "state": a.state,
           "tls": bool(a.cert and a.key)}
    VAULT = Vault(a.state)
    AUTH = TeslaAuth(VAULT)
    VIEWER = Viewer(a.scan, a.out, VAULT)
    eventlog.init(a.state)
    blackbox.init(a.state)
    keepawake.init(a.state)
    threading.Thread(target=autolock_loop, daemon=True).start()
    threading.Thread(target=key_fetch_loop, daemon=True).start()
    threading.Thread(target=nas_sync_loop, daemon=True).start()
    threading.Thread(target=mqtt_loop, daemon=True).start()
    threading.Thread(target=ble_mqtt_loop, daemon=True).start()
    threading.Thread(target=temp_log_loop, daemon=True).start()
    threading.Thread(target=connectivity_log_loop, daemon=True).start()
    threading.Thread(target=trip_watch_loop, daemon=True).start()
    threading.Thread(target=keepawake_loop, daemon=True).start()
    if a.redirect80:
        threading.Thread(target=_redirect80, daemon=True).start()
    httpd = ThreadingHTTPServer(("0.0.0.0", a.port), H)
    scheme = "http"
    if CFG["tls"]:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(a.cert, a.key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"
    print(f"Hub {scheme}://0.0.0.0:{a.port} scan={a.scan} out={a.out} "
          f"vault={'present' if VAULT.has_vault() else 'none'}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
