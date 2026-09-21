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
import ssl, json, argparse, threading, time, secrets, base64, posixpath, hashlib, math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

from vault import Vault, VaultError
from viewer import Viewer
from tesla_auth import TeslaAuth
import tesla_api, keybridge, hubconf, files as filemod, diag, nassync, mqtt_ha, eventlog, blackbox, canbus, keepawake, synchold, videos, assistant, osupdate, wifinets, boottime, derived, hubupdate, presence

WWW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "www")

# make_snapshot.sh links every clip into this persistent farm as it's captured,
# and entries accumulate there across snapshot generations -- unlike --scan
# (/run/teslacam-latest/mnt/TeslaCam), which is a symlink to only the SINGLE
# most recent snapshot and loses visibility into any clip whose snapshot has
# since been superseded. The Viewer browses BROWSE_ROOT so nothing recorded
# ever "disappears" from the UI; CFG["scan"]/CFG["src"] stay on --scan since
# nassync.py and key_fetch_loop's scan_items() are keyed off the live mount.
BROWSE_ROOT = "/mutable/TeslaCam"

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


def _fetch_items(items):
    got = 0
    for i in range(0, len(items), 30):
        try:
            res = tesla_api.fetch_keys(items[i:i + 30], AUTH.get_access_token())
        except tesla_api.DecryptApiError:
            break
        got += VAULT.merge_keys(res)
    return got


# basename -> time of the last key request for a link-farm clip, so clips
# Tesla returns no key for are retried hourly instead of on every pass.
_farm_tried = {}
FARM_RETRY_SEC = 3600


def _farm_items():
    """Key-request items for clips in the link farm (BROWSE_ROOT) whose key
    isn't in the vault under any layout. key_fetch_loop's main scan only
    covers the latest snapshot, so clips from older snapshot generations
    that were never the latest one while the vault was unlocked (a night
    with the vault locked, say) otherwise only got a key once someone
    opened them. Matched by basename like Viewer._by_basename; the IDs
    requested here are farm-relative, which the Viewer matches directly."""
    keys = VAULT.keys()
    known = set(keys) | {posixpath.basename(k) for k in keys}
    now = time.time()
    for b in [b for b, t in _farm_tried.items() if now - t >= FARM_RETRY_SEC]:
        del _farm_tried[b]
    pend = []
    for root, _dirs, names in os.walk(BROWSE_ROOT):
        for nm in names:
            if not nm.endswith(".mp4") or nm in known or nm in _farm_tried:
                continue
            known.add(nm)   # the same clip is linked under RecentClips/<date> and SavedClips/<event>
            _farm_tried[nm] = now
            pend.append(os.path.join(root, nm))
    return keybridge.items_for(pend, BROWSE_ROOT) if pend else []


def key_fetch_loop():
    """Fetch missing FEKs from Tesla once each into the vault (unlocked
    only) -- for the latest snapshot and, via _farm_items, for older clips
    in the link farm -- then mirror every currently-known key onto the Pi's
    own local TeslaCam Samba export as a sealed sidecar (see
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
                    got = _fetch_items(items) if items else 0
                    got += _fetch_items(_farm_items())
                    if got:
                        VIEWER.invalidate()
                        print(f"[hub] fetched {got} new keys", flush=True)
                r = nassync.push_key_sidecars_local(VAULT)
                if r.get("written"):
                    print(f"[hub] local key sidecars: {r['written']} neu geschrieben", flush=True)
                sealed = VIEWER.seal_ram_telemetry()
                if sealed:
                    print(f"[hub] derived: sealed {sealed} drive-data files", flush=True)
                if blackbox.ensure_key():
                    print("[hub] blackbox: generated key pair for the trips", flush=True)
                moved = blackbox.migrate_plaintext()
                if moved:
                    print(f"[hub] blackbox: encrypted {moved} plaintext trips", flush=True)
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
    rels = VIEWER.clip_rel_paths(cid)
    for cam, path in VIEWER.clip_paths(cid).items():
        if not os.path.isfile(path):
            continue
        eid = rels[cam]
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


# The sync hold (synchold.py) needs to know whether a full, error-free cycle
# has run since archiveloop's archive pass finished, and wakes this loop
# early via _nas_kick rather than letting the car idle through the
# 10-minute sleep. Uptime stamps (synchold.uptime()), not wall clock -- see
# synchold.py.
_nas_kick = threading.Event()
_nas_cycle = {"started": None, "completed_start": None, "failed_start": None, "error": None}
# While the sync hold keeps the car awake, a failed cycle is retried this
# soon instead of after the usual 10 minutes -- the hold gives up after
# SYNC_HOLD_MAX_MIN either way.
NAS_RETRY_DURING_HOLD_SEC = 120


def _nas_step(errors, label, r):
    if isinstance(r, dict) and r.get("ok") is False:
        detail = r.get("error") or "; ".join(r.get("errors") or []) or "error"
        errors.append(f"{label}: {detail}")


def nas_sync_loop():
    """Periodically refresh the local-vs-NAS archive coverage percentage and
    push any newly-known per-video key sidecars to the NAS."""
    while True:
        _nas_kick.clear()
        started = synchold.uptime()
        _nas_cycle["started"] = started
        errors = []
        try:
            _nas_step(errors, "Refresh", nassync.refresh_status(CFG["scan"]))
            if VAULT.is_unlocked():
                _nas_step(errors, "Keys", nassync.push_key_sidecars(CFG["scan"], VAULT))
                if hubconf.getval("NAS_RAW_KEYS") == "true":
                    _nas_step(errors, "Raw keys", nassync.push_raw_keys(CFG["scan"], VAULT, CFG["state"]))
            # event.json/thumb.png next to the decrypted clips -- no vault needed
            _nas_step(errors, "Event-Daten", nassync.mirror_event_files())
            if hubconf.getval("SYNC_ALL_CONTENT") == "true":
                _nas_step(errors, "Medien", nassync.sync_media())
            if hubconf.getval("BLACKBOX_ENABLED") == "true" and hubconf.getval("SYNC_TRIPS_ENABLED") != "false":
                _nas_step(errors, "Trips", nassync.sync_trips(_trip["trip_id"] if _trip["active"] else None))
        except Exception as e:
            print("[hub] nas sync:", e, flush=True)
            errors.append(str(e))
        if errors:
            _nas_cycle.update(failed_start=started, error="; ".join(errors)[:300])
        else:
            _nas_cycle.update(completed_start=started, error=None)
        _nas_kick.wait(NAS_RETRY_DURING_HOLD_SEC if errors and synchold.holding() else 600)


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
                        eventlog.log_event("ble", f"BLE vehicle data currently unavailable: {last_err or 'unknown error'}")
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
                    bt = boottime.latest() or {}
                    mqtt_ha.publish_state({
                        "clips": counts.get("clips", 0),
                        "encrypted": counts.get("encrypted", 0),
                        "nas_percent": nas.get("percent", 0),
                        "temp": (st.get("temp") or "").replace("'C", "").strip(),
                        "wifi_ssid": st.get("wifi_ssid") or "–",
                        "usb_connected": bool(st.get("gadget_active")),
                        "in_car": bool(presence.status().get("in_car")),
                        "vault_unlocked": VAULT.is_unlocked(),
                        # only once measured -- a numeric HA sensor can't take a placeholder
                        **{k: v for k, v in (("boot_drives", bt.get("drives_s")), ("boot_hub", bt.get("hub_s")))
                           if v is not None},
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


def _log_sync_hold_event(ev):
    kind = ev["event"]
    mins = int(ev.get("elapsed", 0) // 60)
    if kind == "started":
        eventlog.log_event("keepawake", "Home and NAS reachable: car stays awake until everything "
                                        f"is synced (max. {ev['max_min']} min)")
        return
    if kind in ("complete", "timeout", "disabled"):
        # The car is likely asleep -- and this Pi without power -- within
        # minutes now; flush first, same reasoning as the sleep guard.
        os.sync()
    if kind == "complete":
        eventlog.log_event("keepawake", f"Sync complete after {mins} min: car may sleep")
    elif kind == "timeout":
        eventlog.log_event("keepawake", f"Sync limit reached after {mins} min, still open: "
                                        f"{', '.join(ev.get('waiting_for') or [])}. Car may sleep")
    elif kind == "disabled":
        eventlog.log_event("keepawake", "Keep-awake for the sync turned off: car may sleep")
    elif kind == "left":
        eventlog.log_event("keepawake", f"Home Wi-Fi/NAS gone: keep-awake for the sync ended after {mins} min")


def keepawake_loop():
    """Drives both reasons to keep the car awake: the manual switch (with
    its expiry) and the sync hold (synchold.py: at home, until the NAS sync
    is done). One BLE nudge schedule serves both, see keepawake.tick() --
    and keepawake.py for why a one-shot command isn't enough. State lives on
    disk, so this also catches an expiry that fell due while the Hub was
    restarting/rebooting. Runs every 30s so the sync hold's 2-minute nudge
    interval doesn't drift -- though 'wake' turned out not to hold the car
    awake at any interval (see keepawake.py's docstring)."""
    while True:
        holding = False
        try:
            ev, want_cycle, holding = synchold.tick(dict(_nas_cycle))
            if want_cycle:
                _nas_kick.set()
            if ev:
                _log_sync_hold_event(ev)
        except Exception as e:
            print("[hub] sync hold:", e, flush=True)
        try:
            r = keepawake.tick(hold_active=holding)
            if r is not None:
                ev = r.get("event")
                if ev == "expired":
                    eventlog.log_event("keepawake", "Keep-awake stopped automatically (time expired)")
                elif ev == "nudge_failed":
                    eventlog.log_event("keepawake", f"Wake nudge failing: {r.get('error') or 'unknown error'}")
                elif ev == "nudge_recovered":
                    eventlog.log_event("keepawake", "Wake-Nudge funktioniert wieder")
        except Exception as e:
            print("[hub] keepawake loop:", e, flush=True)
        time.sleep(30)


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
                    eventlog.log_event("wifi", f"Wi-Fi connected: {wifi}")
                elif last_wifi is not None:
                    eventlog.log_event("wifi", f"Wi-Fi disconnected (was: {last_wifi})")
                last_wifi = wifi
            usb = bool(st.get("gadget_active"))
            if last_usb is not None and usb != last_usb:
                eventlog.log_event("usb", "USB gadget connected" if usb else "USB gadget disconnected")
            last_usb = usb
        except Exception as e:
            print("[hub] connectivity log:", e, flush=True)
        time.sleep(20)


# Trip detection/blackbox state, owned by trip_watch_loop only.
_trip = {"active": False, "trip_id": None, "start_ts": None, "start_odometer": None,
         "locked": None, "asleep": None, "charging": None}


# A learned home zone is only rewritten when the car parks this far from it.
HOME_LEARN_MIN_M = 60


def _remember_location(lat, lon):
    """Last known position of the car, plain (it is where the car is parked,
    not a route -- the route itself is encrypted, see blackbox.py) and
    readable without the vault: wifi-watch.sh uses it to notice that the
    car is standing at home when the home WiFi can't be seen in a scan (a
    hidden SSID). Learning the home zone happens in home_zone_loop()."""
    try:
        tmp = os.path.join(CFG["state"], "last_location.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"lat": lat, "lon": lon, "ts": time.time()}, f)
        os.replace(tmp, os.path.join(CFG["state"], "last_location.json"))
    except OSError:
        pass


def _read_location():
    try:
        with open(os.path.join(CFG["state"], "last_location.json"), encoding="utf-8") as f:
            p = json.load(f)
        return float(p["lat"]), float(p["lon"]), float(p["ts"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def home_zone_loop():
    """Learn where "home" is: whenever the Pi is on the home WiFi and a fresh
    position is known, that position is the home zone (HOME_LAT/HOME_LON).
    wifi-watch.sh needs it to get the Pi off a phone hotspot at home
    even when the home SSID never shows up in a scan. Written only when it
    moves more than HOME_LEARN_MIN_M, so the config isn't rewritten (and the
    root filesystem remounted) for every GPS wobble."""
    while True:
        time.sleep(300)
        try:
            home = hubconf.getval("SSID")
            if not home or diag.status().get("wifi_ssid") != home:
                continue
            pos = _read_location()
            if not pos or time.time() - pos[2] > 7 * 86400:
                continue
            lat, lon = pos[0], pos[1]
            old_lat, old_lon = hubconf.getval("HOME_LAT"), hubconf.getval("HOME_LON")
            if old_lat and old_lon:
                try:
                    if _distance_m(float(old_lat), float(old_lon), lat, lon) < HOME_LEARN_MIN_M:
                        continue
                except ValueError:
                    pass
            hubconf.write_settings({"home_lat": "%.6f" % lat, "home_lon": "%.6f" % lon})
            print("[hub] home zone learned from the home WiFi", flush=True)
        except Exception as e:
            print("[hub] home zone:", e, flush=True)


def _distance_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


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
    eventlog.log_event("trip", "Drive started")


def _end_trip():
    summary = blackbox.end_trip(_trip["trip_id"]) if _trip["trip_id"] else {}
    dist = summary.get("distance_km")
    msg = "Drive ended"
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
        _remember_location(lat, lon)
    presence.note_ble_success()   # the car just answered: it is right here
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
            eventlog.log_event("trip", "Locked" if locked else "Unlocked", during_trip=True)
        _trip["locked"] = locked
    ch = diag.ble_read("awake", "charge")
    if ch.get("ok"):
        charging = (ch.get("values") or {}).get("chargingState")
        if _trip["charging"] is not None and charging != _trip["charging"] and charging:
            eventlog.log_event("trip", f"Charge state: {charging}", during_trip=True)
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


# Sleep-guard state, owned by sleep_guard_loop only.
_sleep_guard = {"idle_since": None, "prepared": False}
# Community-reported (not an official Tesla spec) time-to-sleep is ~15 min
# once locked/parked/Sentry-off/not-charging. Trigger well under that so a
# too-fast or too-slow read cadence still leaves margin.
SLEEP_GUARD_THRESHOLD_SEC = 8 * 60


def _sleep_guard_tick():
    """The car (glovebox USB power, see project memory) cuts this Pi's own
    supply on its own schedule when it sleeps -- unclean, unannounced, and
    not something the Pi can prevent or be told about in advance. This
    can't fix that: cam_disk.bin's dirty-bit/fsck cost after a cut is
    entirely down to the car's own software never getting a chance to
    close its view of that filesystem, which the Pi has no channel to
    influence. What IS in reach: minimizing how much of the Pi's OWN state
    (vault, hubconf settings, archiveloop.log, blackbox trip data, ...) is
    still just dirty pages in RAM when the cut actually happens.

    BLE (the paired "charging_manager" role) can't read Sentry state at
    all, so this doesn't try to detect it -- it stays conservative and
    treats locked + parked as "possibly heading to sleep" regardless of
    Sentry, since a spurious sync() when Sentry is actually keeping the car
    awake costs nothing. Once that's held continuously for
    SLEEP_GUARD_THRESHOLD_SEC, calls os.sync() (flushes every dirty page
    system-wide) -- deliberately nothing more invasive than that: no
    gadget disconnect, no pausing archiveloop's own operations, since
    acting on what's ultimately a guess about vehicle state must never risk
    disrupting a still-active connection to the car."""
    drive = diag.ble_read("awake", "drive")
    closures = diag.ble_read("awake", "closures")
    if not drive.get("ok") or not closures.get("ok"):
        return
    shift = (drive.get("values") or {}).get("driveState.shiftState")
    locked = (closures.get("values") or {}).get("locked")
    idle_eligible = locked is True and shift in ("Park", "Invalid", None)

    now = time.time()
    if not idle_eligible:
        if _sleep_guard["prepared"]:
            eventlog.log_event("power", "Activity detected, sync preparation reset")
        _sleep_guard.update(idle_since=None, prepared=False)
        return

    if _sleep_guard["idle_since"] is None:
        _sleep_guard["idle_since"] = now
        return

    if now - _sleep_guard["idle_since"] >= SLEEP_GUARD_THRESHOLD_SEC:
        os.sync()
        if not _sleep_guard["prepared"]:
            mins = int((now - _sleep_guard["idle_since"]) / 60)
            eventlog.log_event("power", f"Car locked/parked for {mins} min -- "
                                         "Pi state synced preventively (possible sleep soon)")
            _sleep_guard["prepared"] = True


def sleep_guard_loop():
    while True:
        try:
            _sleep_guard_tick()
        except Exception as e:
            print("[hub] sleep guard:", e, flush=True)
        time.sleep(60)


def _bulk_worker():
    def progress(done, total, _cid):
        with _bulk_guard:
            _bulk_job["done"] = done
            _bulk_job["total"] = total
    try:
        # Scan once, here, so total is known as soon as the (possibly slow --
        # full /mutable/TeslaCam walk) scan finishes, instead of only once the
        # first clip's decrypt+thumbnail work also completes. bulk_prepare()
        # gets the result handed in so it doesn't scan a second time.
        targets = VIEWER.bulk_targets()
        with _bulk_guard:
            _bulk_job["total"] = len(targets)
        res = VIEWER.bulk_prepare(on_progress=progress, targets=targets)
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
                length = max(0, end - start + 1)
                f.seek(start)
                try:
                    self.send_response(206)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.send_header("Content-Length", str(length))
                    for k, v in (extra or {}).items():
                        self.send_header(k, v)
                    self.end_headers()
                    # Stream the range in 1 MB pieces: a player's "bytes=0-"
                    # asks for the whole ~36 MB clip, and reading that in one
                    # go per request (4 cameras, again on every seek) blew
                    # the Hub past 400 MB on the 1 GB Pi (2026-09-15).
                    left = length
                    while left > 0:
                        chunk = f.read(min(1 << 20, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
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

    def _index(self):
        """index.html with ?v=<mtime> on app.js/style.css: /static/ is
        served with a one-day max-age, so without this a redeploy stayed
        invisible to a browser that had loaded the page earlier that day."""
        with open(os.path.join(WWW, "index.html"), encoding="utf-8") as f:
            html = f.read()
        for name in ("app.js", "style.css"):
            try:
                v = int(os.path.getmtime(os.path.join(WWW, name)))
            except OSError:
                continue
            html = html.replace(f'static/{name}"', f'static/{name}?v={v}"')
        return self._raw(200, html.encode("utf-8"), "text/html; charset=utf-8", {"Cache-Control": "no-store"})

    # -- routing --
    def do_GET(self):
        path = urlparse(self.path).path
        # static SPA
        if path == "/" or path == "/index.html":
            return self._index()
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
        if path == "/api/net_tx_bytes":
            return self._json(200, diag.net_tx_bytes())
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
        if path == "/api/os/status":
            return self._json(200, osupdate.status())
        if path == "/api/hub/update_status":
            return self._json(200, hubupdate.status())
        if path == "/api/presence":
            return self._json(200, presence.status())
        if path == "/api/headline":
            # the two lines the header shows on every page: is the Hub at its
            # car, and is the paired NAS reachable -- small payload on purpose,
            # /api/nas/sync_status carries the whole per-clip map.
            nas, pres = nassync.status(), presence.status()
            return self._json(200, {
                "in_car": pres.get("in_car"), "car_configured": pres.get("configured"),
                "car_last_seen": pres.get("last_seen"), "car_checked": pres.get("checked"),
                "usb_host": pres.get("usb_host"),
                "nas_configured": bool(hubconf.getval("ARCHIVE_SERVER")),
                "nas_ok": nas.get("ok"), "nas_error": nas.get("error"),
                "nas_checked": nas.get("t"), "nas_percent": nas.get("percent"),
                "nas_paired": nassync.pairing_status(CFG["state"]).get("paired"),
            })
        if path == "/api/assistant/state":
            try:
                since = int(self._qs("since") or 0)
            except ValueError:
                since = 0
            return self._json(200, assistant.state(since))
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
            st = keepawake.status()
            st["sync_hold"] = dict(synchold.status(), failing=keepawake.nudge_failing())
            return self._json(200, st)
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
        if path == "/api/wifi/networks":
            return self._json(200, wifinets.status())
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
        if path == "/api/boottimes":
            return self._json(200, boottime.status())
        if path == "/api/temperature/series":
            try:
                hours = max(1, min(60 * 24, int(self._qs("hours") or "24")))
            except ValueError:
                hours = 24
            return self._json(200, eventlog.read_temperature_series(hours))
        if path == "/api/temperature/download":
            p = eventlog.temperature_log_path()
            if not os.path.isfile(p):
                return self._json(404, {"error": "not found"})
            return self._sendfile(p, "text/csv",
                                   {"Content-Disposition": 'attachment; filename="temperature.log"'})
        if path == "/api/blackbox/trips":
            try:
                trips, locked = blackbox.list_trips(), False
            except blackbox.Locked:   # trips are encrypted, see blackbox.py
                trips, locked = [], True
            return self._json(200, {"trips": trips, "locked": locked, "active": _trip["active"]})
        if path == "/api/blackbox/export":
            trip_id = self._qs("trip") or ""
            if not trip_id or "/" in trip_id or "\\" in trip_id:
                return self._json(404, {"error": "not found"})
            try:
                gpx = blackbox.to_gpx(trip_id)
            except blackbox.Locked:
                return self._json(423, {"error": "Vault locked"})
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

        # Several endpoints below remount / themselves; their remount,ro in a
        # finally block would pull the root out from under a running dpkg.
        if osupdate.running() and not path.startswith("/api/os/") and path not in ("/api/login", "/api/logout"):
            return self._json(409, {"ok": False, "error": "OS update is currently running – please wait until it finishes"})
        if hubupdate.running() and not path.startswith("/api/hub/") and path not in ("/api/login", "/api/logout"):
            return self._json(409, {"ok": False, "error": "Hub update is currently running – please wait until it finishes"})

        # public auth endpoints
        if path == "/api/setup":
            if VAULT.has_vault():
                return self._json(409, {"error": "vault exists"})
            pw = body.get("pass", "")
            if not pw:
                return self._json(400, {"error": "empty password"})
            imp_k, imp_t = _import_legacy() if body.get("import") else ({}, {})
            VAULT.create(pw, import_keys=imp_k, import_token=imp_t)
            tok = _new_session(); _touch(); VIEWER.invalidate()
            return self._json(200, {"ok": True, "imported": len(imp_k)}, self._setcookie(tok))
        if path == "/api/vault/factory_reset":
            if not VAULT.has_vault():
                return self._json(200, {"ok": False, "error": "no vault present"})
            if body.get("confirm") != "RESET":
                return self._json(200, {"ok": False, "error": "Confirmation missing"})
            VAULT.factory_reset()
            hubconf.clear_secrets()
            DERIVED.drop()   # sealed thumbnails/telemetry: unreadable without the old master key anyway
            blackbox.drop()  # encrypted trips: same, their private key was in the vault
            _drop_sessions(); VIEWER.clear_cache(); VIEWER.invalidate()
            return self._json(200, {"ok": True})
        if path == "/api/login":
            if VAULT.unlock_with_pass(body.get("pass", "")):
                tok = _new_session(); _touch(); VIEWER.invalidate()
                return self._json(200, {"ok": True}, self._setcookie(tok))
            return self._json(200, {"ok": False, "error": "wrong password"})
        if path == "/api/logout":
            _drop_sessions(); VAULT.lock(); VIEWER.clear_cache()
            return self._json(200, {"ok": True})

        if not self._auth_ok():
            return self._json(401, {"error": "auth"})
        _touch()

        if path == "/api/vault/change_pass":
            old, new = body.get("old", ""), body.get("new", "")
            if not new:
                return self._json(400, {"ok": False, "error": "new password missing"})
            try:
                if not VAULT.change_pass(old, new):
                    return self._json(400, {"ok": False, "error": "current password wrong"})
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
                # total is filled in by _bulk_worker itself, in the background --
                # computing it here (VIEWER.bulk_targets() -> clips() -> a full
                # /mutable/TeslaCam scan of every clip ever recorded, not just the
                # latest snapshot) can take long enough that the POST response
                # itself stalls, leaving the button stuck on "startet..." before
                # the frontend's poll loop ever gets to run. Same fix Te_FITI uses
                # for its equivalent endpoint (bg(ensure_all) starts the scan in a
                # thread and replies immediately; _dec_job/total is populated once
                # the thread has actually counted the work).
                _bulk_job.update(running=True, done=0, total=0, errors=[])
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
            if ok and "nas_skip_deleted" in body:
                # re-count now: labels clips deleted on the NAS, or (switched off) re-queues them
                threading.Thread(target=lambda: nassync.refresh_status(CFG["scan"]), daemon=True).start()
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
        if path in ("/api/wifi/add", "/api/wifi/remove", "/api/wifi/move"):
            try:
                if path.endswith("/add"):
                    return self._json(200, wifinets.add(body.get("ssid", ""), body.get("password", "")))
                if path.endswith("/remove"):
                    return self._json(200, wifinets.remove(body.get("ssid", "")))
                return self._json(200, wifinets.move(body.get("ssid", ""), body.get("delta", 0)))
            except (ValueError, TypeError) as e:
                return self._json(200, {"ok": False, "error": str(e)})
        if path == "/api/os/check":
            return self._json(200, osupdate.check())
        if path == "/api/os/upgrade":
            return self._json(200, osupdate.start_upgrade())
        if path == "/api/presence/check":
            return self._json(200, presence.check())
        if path == "/api/hub/update_check":
            return self._json(200, hubupdate.check())
        if path == "/api/hub/update_install":
            return self._json(200, hubupdate.start_update())
        if path == "/api/assistant/send":
            return self._json(200, assistant.send(body.get("text", "")))
        if path == "/api/assistant/confirm":
            return self._json(200, assistant.confirm(body.get("id", ""), bool(body.get("approve"))))
        if path == "/api/assistant/reset":
            return self._json(200, assistant.reset())
        if path == "/api/assistant/key":
            try:
                return self._json(200, assistant.set_key(body.get("key", "")))
            except VaultError as e:
                return self._json(200, {"ok": False, "error": str(e)})
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
            eventlog.log_event("keepawake", "Keep-awake stopped")
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


class _QuietServer(ThreadingHTTPServer):
    """A browser that walks away mid-response -- cancelled thumbnail loads
    while scrolling the clip grid are the usual case -- makes the TLS layer
    raise, and socketserver prints a full traceback per occurrence. Six of
    them showed up in a single browsing session on 2026-09-18, which buries
    the entries that matter. These are not errors of ours: drop them, keep
    every other one."""

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ssl.SSLEOFError, ssl.SSLZeroReturnError,
                                          BrokenPipeError, ConnectionResetError, TimeoutError)):
            return
        super().handle_error(request, client_address)


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
    global VAULT, VIEWER, AUTH, CFG, DERIVED
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
    DERIVED = derived.Derived(os.path.join(a.state, "derived"), VAULT)
    VIEWER = Viewer(BROWSE_ROOT, a.out, VAULT, derived=DERIVED)
    threading.Thread(target=DERIVED.prune, daemon=True).start()
    eventlog.init(a.state)
    blackbox.init(a.state, VAULT)
    keepawake.init(a.state)
    synchold.init(a.state)
    assistant.init(VAULT)
    boottime.init(a.state)
    threading.Thread(target=boottime.loop, daemon=True).start()
    hubupdate.init(a.state)
    threading.Thread(target=hubupdate.check_loop, daemon=True).start()
    presence.init(a.state)
    presence.set_trip_probe(lambda: _trip["active"])
    threading.Thread(target=presence.loop, daemon=True).start()
    threading.Thread(target=home_zone_loop, daemon=True).start()
    threading.Thread(target=autolock_loop, daemon=True).start()
    threading.Thread(target=key_fetch_loop, daemon=True).start()
    threading.Thread(target=nas_sync_loop, daemon=True).start()
    threading.Thread(target=mqtt_loop, daemon=True).start()
    threading.Thread(target=ble_mqtt_loop, daemon=True).start()
    threading.Thread(target=temp_log_loop, daemon=True).start()
    threading.Thread(target=connectivity_log_loop, daemon=True).start()
    threading.Thread(target=trip_watch_loop, daemon=True).start()
    threading.Thread(target=keepawake_loop, daemon=True).start()
    threading.Thread(target=sleep_guard_loop, daemon=True).start()
    if a.redirect80:
        threading.Thread(target=_redirect80, daemon=True).start()
    httpd = _QuietServer(("0.0.0.0", a.port), H)
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
