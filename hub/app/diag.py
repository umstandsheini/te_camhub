"""
Diagnostics/actions for the Hub: system status, log tails, reboot, drive toggle,
sync/retention/BLE triggers. Thin wrappers over the existing teslausb scripts and
standard tools (the Hub runs as root via systemd).
"""
import os, subprocess, urllib.request, tarfile, io, json, time, threading, base64, fcntl
import hubconf

# The Pi has exactly one Bluetooth adapter; two tesla-control invocations
# at once collide ("device or resource busy") instead of queueing. Every
# BLE command (reads, actions, pairing, status) goes through this lock so
# concurrent callers (page auto-load, MQTT loop, manual retries) serialize
# instead of failing each other.
#
# This only covers callers within this process, though -- archiveloop's own
# "keep car awake" nudge (run/awake_start) invokes tesla-control directly
# from a separate bash process during every archiving run, with no
# knowledge of _ble_lock, and collided with it constantly (which is why the
# car kept falling asleep mid-archive despite the nudge task supposedly
# running). BLE_LOCKFILE extends the same serialization across processes
# via flock; awake_start takes the identical lock around its own call.
_ble_lock = threading.Lock()
BLE_LOCKFILE = "/tmp/ble.lock"

def _tc_run(args, timeout=30):
    """Run a tesla-control invocation serialized against the single
    Bluetooth adapter, both within this process (_ble_lock) and against
    archiveloop's separate keep-awake script (BLE_LOCKFILE, see above)."""
    with _ble_lock:
        with open(BLE_LOCKFILE, "w") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            try:
                return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
            finally:
                fcntl.flock(lockf, fcntl.LOCK_UN)


def _run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None

def status():
    def out(cmd):
        r = _run(cmd)
        return (r.stdout.strip() if r and r.returncode == 0 else "")
    temp = ""
    r = _run(["vcgencmd", "measure_temp"])
    if r and r.stdout:
        temp = r.stdout.strip().replace("temp=", "")
    df = {}
    r = _run(["df", "-h", "/backingfiles", "/mnt/cam"])
    if r and r.stdout:
        df["raw"] = r.stdout.strip()
    up = out(["uptime", "-p"])
    ssid = out(["iwgetid", "-r"])
    return {"temp": temp, "uptime": up, "wifi_ssid": ssid, "disks": df.get("raw", ""),
            "gadget_active": _gadget_active(), "teslausb_active": _svc_active("teslausb")}

def _gadget_active():
    try:
        return bool(open("/sys/kernel/config/usb_gadget/teslausb/UDC").read().strip())
    except Exception:
        return None

def _svc_active(name):
    r = _run(["systemctl", "is-active", name])
    return bool(r and r.stdout.strip() == "active")

def tail_log(which, lines=200):
    path = {"archiveloop": "/mutable/archiveloop.log",
            "setup": "/teslausb/teslausb-headless-setup.log",
            "sync": "/mutable/sync-to-nas.log",
            "retention": "/mutable/retention.log"}.get(which)
    if not path or not os.path.isfile(path):
        return ""
    r = _run(["tail", "-n", str(lines), path])
    return r.stdout if r else ""

def reboot():
    subprocess.Popen(["reboot"])
    return {"ok": True}

def toggle_drives():
    r = _run(["/var/www/html/cgi-bin/toggledrives.sh"], timeout=30)
    return {"ok": bool(r and r.returncode == 0)}

def trigger_sync():
    """Force an immediate archive cycle for the camera clips: restarting the
    teslausb service disconnects+reconnects the USB gadget and runs
    archiveloop's archive pass right away, instead of waiting for the car
    to go idle on its own schedule."""
    r = subprocess.run(["systemctl", "restart", "teslausb"], capture_output=True)
    return {"ok": r.returncode == 0}

# ---- BLE: multiple named keys, each pairable with its own vehicle-command
# role, so lower-privilege roles (e.g. charging_manager, vehicle_monitor)
# can be tried out independently instead of always enrolling with 'owner'
# like teslausb's original single-key cgi scripts did.
BLE_BIN = "/root/bin"
BLE_DIR = "/root/.ble"
BLE_BINARIES_URL = ("https://github.com/MikeBishop/tesla-vehicle-command-arm-binaries"
                     "/releases/latest/download/vehicle-command-binaries-linux-armv6.tar.gz")


def ble_binaries_installed():
    return os.path.isfile(f"{BLE_BIN}/tesla-control") and os.path.isfile(f"{BLE_BIN}/tesla-keygen")


def install_ble_binaries():
    """Download tesla-control/tesla-keygen -- the same official binaries
    teslausb's own setup/pi/configure.sh installs, from
    MikeBishop/tesla-vehicle-command-arm-binaries -- plus the bluez package.
    Needed on any Pi where TESLA_BLE_VIN wasn't already set during the
    original one-step setup (that's the only place teslausb installs these
    itself)."""
    if ble_binaries_installed():
        return {"ok": True, "already": True}
    try:
        with urllib.request.urlopen(BLE_BINARIES_URL, timeout=60) as resp:
            data = resp.read()
    except Exception as e:
        return {"ok": False, "error": f"Download fehlgeschlagen: {e}"[:300]}
    subprocess.run(["mount", "/", "-o", "remount,rw"], capture_output=True)
    try:
        os.makedirs(BLE_BIN, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            for member in tf.getmembers():
                base = os.path.basename(member.name)
                if base in ("tesla-control", "tesla-keygen"):
                    member.name = base
                    tf.extract(member, BLE_BIN)
                    os.chmod(os.path.join(BLE_BIN, base), 0o755)
        subprocess.run(["apt-get", "install", "-y", "--no-install-recommends", "bluez"], capture_output=True)
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
    finally:
        subprocess.run(["mount", "/", "-o", "remount,ro"], capture_output=True)
    if not ble_binaries_installed():
        return {"ok": False, "error": "Download hat die Programme nicht bereitgestellt"}
    return {"ok": True}


def _ble_keypath(name):
    d = os.path.join(BLE_DIR, name)
    return os.path.join(d, "key_private.pem"), os.path.join(d, "key_public.pem")


def _ble_ensure_key(name):
    priv, pub = _ble_keypath(name)
    if os.path.isfile(priv) and os.path.isfile(pub):
        return {"ok": True, "already": True}
    if not ble_binaries_installed():
        return {"ok": False, "error": "BLE-Programme erst installieren"}
    subprocess.run(["mount", "/", "-o", "remount,rw"], capture_output=True)
    try:
        os.makedirs(os.path.dirname(priv), exist_ok=True)
        r = subprocess.run([f"{BLE_BIN}/tesla-keygen", "-key-file", priv, "-output", pub, "create"],
                            capture_output=True, text=True, timeout=30)
        if r.returncode != 0 or not (os.path.isfile(priv) and os.path.isfile(pub)):
            return {"ok": False, "error": (r.stderr or "Schlüsselerzeugung fehlgeschlagen").strip()[:200]}
        os.chmod(priv, 0o600)
        os.chmod(pub, 0o644)
        return {"ok": True}
    finally:
        subprocess.run(["mount", "/", "-o", "remount,ro"], capture_output=True)


def ble_pair_role(name, role):
    """Generate (if needed) a named keypair and request pairing with a
    specific vehicle-command role ('driver', 'charging_manager',
    'vehicle_monitor', ...). Requires TESLA_BLE_VIN configured. After this
    call, confirm on the car: tap an existing key card to the console and
    accept the prompt on the touchscreen, same as adding any BLE key."""
    vin = hubconf.getval("TESLA_BLE_VIN")
    if not vin:
        return {"ok": False, "error": "Fahrzeug-VIN erst eintragen und speichern"}
    if not ble_binaries_installed():
        r = install_ble_binaries()
        if not r.get("ok"):
            return r
    kr = _ble_ensure_key(name)
    if not kr.get("ok"):
        return kr
    _priv, pub = _ble_keypath(name)
    r = _tc_run([f"{BLE_BIN}/tesla-control", "-ble", "-vin", vin.upper(),
                 "add-key-request", pub, role, "cloud_key"], timeout=60)
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout or "Kopplungsanfrage fehlgeschlagen").strip()[:300]}
    return {"ok": True}


# Individually invokable BLE reads/actions -- empirically confirmed working
# against the real vehicle (charging_manager role). Commands that later
# started failing with a privilege error (charge-port-open/close, honk,
# flash-lights -- worked the morning this was built, rejected by the
# vehicle a few hours later the same day, same key/role) were removed
# rather than left in for _ble_unavailable to filter out at runtime, since
# the user re-tested by hand and confirmed it's not transient.
# id -> (label, args).
BLE_READS = {
    "charge": ("Ladezustand", ["state", "charge"]),
    "closures": ("Verriegelung/Türen", ["state", "closures"]),
    "climate": ("Klimazustand", ["state", "climate"]),
    "tire_pressure": ("Reifendruck", ["state", "tire-pressure"]),
    "location": ("Standort", ["state", "location"]),
    "drive": ("Fahrzustand", ["state", "drive"]),
    "media": ("Medienstatus", ["state", "media"]),
    "media_detail": ("Medien-Details", ["state", "media-detail"]),
    "charge_schedule": ("Lade-Zeitplan", ["state", "charge-schedule"]),
    "precondition_schedule": ("Vorklimatisierungs-Zeitplan", ["state", "precondition-schedule"]),
    "software_update": ("Software-Update-Status", ["state", "software-update"]),
    "parental_controls": ("Kindersicherung-Status", ["state", "parental-controls"]),
    "body_controller": ("Basiszustand (VCSEC)", ["body-controller-state"]),
    "list_keys": ("Alle Schlüssel", ["list-keys"]),
    "ping": ("Erreichbarkeit", ["ping"]),
}

# charge_port_open/charge_port_close/honk/flash_lights were confirmed
# working when this was first built (2026-07-10 morning), then confirmed by
# the user to fail with INSUFFICIENT_PRIVILEGES a few hours later on the
# same day, same role, same key -- Tesla's own docs warn role capabilities
# "may change". Removed here rather than left to the runtime
# _ble_unavailable filter, since the user re-tested by hand and this is now
# a known, durable fact rather than a one-off failure to auto-recover from.
BLE_ACTIONS = {
    "charging_start": ("Laden starten", ["charging-start"]),
    "charging_stop": ("Laden stoppen", ["charging-stop"]),
    "charging_set_limit": ("Ladegrenze setzen", ["charging-set-limit", "80"]),
    "charging_set_amps": ("Ladestrom setzen", ["charging-set-amps", "16"]),
    "charging_schedule_cancel": ("Lade-Zeitplan abbrechen", ["charging-schedule-cancel"]),
    "wake": ("Auto aufwecken", ["wake"]),
    "keep_accessory_power_on": ("Zubehör-Stromversorgung an", ["keep-accessory-power", "on"]),
    "keep_accessory_power_off": ("Zubehör-Stromversorgung aus", ["keep-accessory-power", "off"]),
}
# Actuation commands (lock/unlock, trunk/frunk, climate, windows) exist in Tesla's
# own tesla-control CLI, but were deliberately NOT added here: they'd need a
# higher-privilege key role (e.g. "driver") than charging_manager, and that key
# would then sit unencrypted on the stick inside the car -- a bigger theft risk
# than the current setup. See canbus.py's write_action()/write_raw() for the
# raw-CAN alternative used for glovebox etc. instead, which reuses this same
# charging_manager-paired connection rather than requiring a stronger key.


def _ble_base(name):
    vin = hubconf.getval("TESLA_BLE_VIN")
    if not vin:
        return None, {"ok": False, "error": "Fahrzeug-VIN erst eintragen und speichern"}
    priv, _pub = _ble_keypath(name)
    if not os.path.isfile(priv):
        return None, {"ok": False, "error": "noch nicht gekoppelt"}
    return [f"{BLE_BIN}/tesla-control", "-ble", "-vin", vin.upper(), "-key-file", priv], None


def _scalarize(v):
    """Plain scalar as-is; a protobuf oneof enum serializes as {"Name": {}}
    -- reduce that to just the enum name. Anything else (nested objects with
    more than one key, lists) isn't a simple field, skip it."""
    if isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, dict) and len(v) == 1:
        return next(iter(v.keys()))
    return None


def _flatten_state(raw_stdout):
    """`tesla-control` doesn't use one consistent JSON shape across commands:
    - most `state CATEGORY` calls wrap a single nested object in one
      top-level key (e.g. {"chargeState": {...}}) -- flatten that object.
    - `state drive` wraps *two* top-level keys, each its own nested object
      (driveState + locationState) -- flatten both, prefixed to avoid
      collisions.
    - `body-controller-state` has no wrapper at all, fields are already at
      the top level -- flatten those directly.
    Handles all three so read results are never silently empty."""
    try:
        data = json.loads(raw_stdout)
    except Exception:
        return {}
    if not isinstance(data, dict) or not data:
        return {}

    if len(data) == 1:
        inner = next(iter(data.values()))
        if isinstance(inner, dict):
            out = {k: _scalarize(v) for k, v in inner.items()}
            out = {k: v for k, v in out.items() if v is not None}
            if out:
                return out

    if all(isinstance(v, dict) for v in data.values()):
        out = {}
        for wrapper, inner in data.items():
            for k, v in inner.items():
                sv = _scalarize(v)
                if sv is not None:
                    out[f"{wrapper}.{k}"] = sv
        if out:
            return out

    out = {k: _scalarize(v) for k, v in data.items()}
    return {k: v for k, v in out.items() if v is not None}


# Command ids that have actually failed with a privilege/authorization
# error against the real vehicle in this run. Tesla's own docs warn role
# capabilities "may change as new features are added" -- confirmed true in
# practice (charge-port-open/honk/flash-lights worked earlier the same day
# this was built, then started failing) -- so a command that was allowed
# once is not trusted to stay allowed. Read-only in-memory: resets on Hub
# restart, and can be cleared via ble_reset_unavailable() to let a command
# be tried again (e.g. after Tesla changes something back).
_ble_unavailable = set()
_PRIVILEGE_ERROR_MARKERS = ("INSUFFICIENT_PRIVILEGES", "UNAUTHORIZED")


def _looks_like_privilege_error(text):
    t = (text or "").upper()
    return any(m in t for m in _PRIVILEGE_ERROR_MARKERS)


def ble_available_commands():
    """BLE_READS/BLE_ACTIONS minus anything that has actually failed with a
    privilege error this run -- this is the list the UI should show."""
    return (
        {i: v for i, v in BLE_READS.items() if i not in _ble_unavailable},
        {i: v for i, v in BLE_ACTIONS.items() if i not in _ble_unavailable},
    )


def ble_reset_unavailable():
    _ble_unavailable.clear()
    return {"ok": True}


def ble_read(name, read_id):
    """Run exactly one confirmed-allowed read command and return parsed
    values, for the "read now" UI and for MQTT sensor publishing."""
    spec = BLE_READS.get(read_id)
    if not spec:
        return {"ok": False, "error": "unbekannter Lesebefehl"}
    label, args = spec
    base, err = _ble_base(name)
    if err:
        return err
    r = _tc_run(base + args)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "Fehler").strip()[:300]
        if _looks_like_privilege_error(err):
            _ble_unavailable.add(read_id)
        return {"ok": False, "error": err}
    if read_id == "list_keys":
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        return {"ok": True, "label": label, "values": {"anzahl_schluessel": len(lines)}}
    if read_id == "ping":
        return {"ok": True, "label": label, "values": {"erreichbar": True}}
    return {"ok": True, "label": label, "values": _flatten_state(r.stdout)}


def ble_exec(name, action_id, value=None):
    """Run exactly one confirmed-allowed action command."""
    spec = BLE_ACTIONS.get(action_id)
    if not spec:
        return {"ok": False, "error": "unbekannter Befehl"}
    label, args = spec
    base, err = _ble_base(name)
    if err:
        return err
    final_args = list(args)
    if action_id in ("charging_set_limit", "charging_set_amps") and value is not None:
        try:
            final_args[-1] = str(int(value))
        except (TypeError, ValueError):
            return {"ok": False, "error": "ungültiger Wert"}
    r = _tc_run(base + final_args)
    ok = r.returncode == 0
    lines = (r.stderr or r.stdout or "").strip().splitlines()
    detail = lines[-1] if lines else ("OK" if ok else "Fehler")
    if not ok and _looks_like_privilege_error(detail):
        _ble_unavailable.add(action_id)
    return {"ok": ok, "label": label, "detail": detail[:200]}


def ble_status_role(name):
    """session-info has been observed to report a false negative
    (paired=False) in situations where a plain read succeeds moments
    later against the same key -- it's a stricter/different probe than an
    actual command needs, apparently sensitive to momentary BLE flakiness.
    One retry before giving up avoids that intermittent false negative
    from hiding the whole BLE UI/blocking the MQTT loop."""
    vin = hubconf.getval("TESLA_BLE_VIN")
    priv, _pub = _ble_keypath(name)
    if not vin or not os.path.isfile(priv) or not ble_binaries_installed():
        return {"paired": False}
    args = [f"{BLE_BIN}/tesla-control", "-ble", "-vin", vin.upper(),
            "session-info", priv, "infotainment"]
    for attempt in range(2):
        r = _tc_run(args, timeout=20)
        if r.returncode == 0:
            return {"paired": True}
    return {"paired": False}

def _set_ap_autoconnect(enabled):
    """Directly patches the autoconnect= line in TESLAUSB_AP's keyfile and
    asks NetworkManager to reload it, instead of `nmcli con modify ...
    connection.autoconnect`. `nmcli con modify` can't be used here at all:
    NetworkManager.service runs with ProtectSystem=true, so /etc is
    read-only from *that process's* point of view no matter what our own
    mount rw/ro state is -- confirmed empirically (`nmcli con modify`
    returns "Read-only file system" even with `/` freshly remounted rw).
    Writing the file ourselves (this Hub process runs outside that sandbox)
    and reloading works the same way ap-ensure.sh's profile creation does."""
    path = "/etc/NetworkManager/system-connections/TESLAUSB_AP.nmconnection"
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    val = "true" if enabled else "false"
    for i, l in enumerate(lines):
        if l.startswith("autoconnect="):
            lines[i] = f"autoconnect={val}\n"
            break
    else:
        for i, l in enumerate(lines):
            if l.strip() == "[connection]":
                lines.insert(i + 1, f"autoconnect={val}\n")
                break
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    subprocess.run(["nmcli", "con", "reload"], capture_output=True)


def _usb_wifi_device():
    """Find a WiFi interface attached via USB (any chipset/brand) by sysfs
    device path rather than a hardcoded interface name -- wlan1 isn't
    guaranteed to stay wlan1 across reboots/replugs, but "the wifi
    interface whose /sys/class/net/<if>/device path routes through a
    /usbN/ bus" is stable as long as only one USB WiFi adapter is ever
    attached (the onboard chip's path routes through mmc/sdio instead)."""
    try:
        ifaces = sorted(os.listdir("/sys/class/net"))
    except OSError:
        return None
    for ifname in ifaces:
        if not ifname.startswith("wlan"):
            continue
        try:
            devpath = os.path.realpath(f"/sys/class/net/{ifname}/device")
        except OSError:
            continue
        if "/usb" in devpath:
            return ifname
    return None


def ap_usb_status():
    """Live status for the settings UI: is a USB WiFi adapter plugged in
    right now, and is the AP currently bound to it (vs. still on the
    onboard chip's ap0, or off)."""
    usb_if = _usb_wifi_device()
    bound_to_usb = False
    if usb_if:
        r = _run(["nmcli", "-t", "-f", "NAME,DEVICE", "c", "show", "--active"])
        active_on_usb = f"TESLAUSB_AP:{usb_if}" in (r.stdout or "").splitlines() if r and r.returncode == 0 else False
        bound_to_usb = active_on_usb
    return {"usb_available": usb_if is not None, "usb_device": usb_if, "ap_on_usb": bound_to_usb}


def apply_ap_on_usb(enabled, ssid=None, password=None, ap_ip=None):
    """Permanently move the TESLAUSB_AP hotspot onto a plugged-in USB WiFi
    adapter (see hub/ap-usb-ensure.sh for why: eliminates the onboard
    chip's AP+STA contention that the AP-Fallback UI already warns can
    briefly disrupt home WiFi). Disabling just stops/removes the
    USB-bound profile -- it does not automatically recreate the onboard
    ap0 setup; use the regular AP-Fallback toggle for that."""
    subprocess.run(["mount", "/", "-o", "remount,rw"], capture_output=True)
    try:
        if not enabled:
            subprocess.run(["nmcli", "con", "down", "TESLAUSB_AP"], capture_output=True)
            return {"ok": True}
        usb_if = _usb_wifi_device()
        if not usb_if:
            return {"ok": False, "error": "kein USB-WLAN-Adapter gefunden -- erst einstecken"}
        r = subprocess.run(["nmcli", "-t", "-f", "NAME", "c", "show"], capture_output=True, text=True)
        has_ap = "TESLAUSB_AP" in (r.stdout or "").splitlines()
        if not has_ap and not (ssid and password):
            return {"ok": False, "error": "zuerst Access-Point-SSID und -Passwort eintragen und speichern"}
        if ssid and password:
            r = subprocess.run(["bash", "/opt/teslacam-hub/ap-usb-ensure.sh", ssid, password, ap_ip or "192.168.66.1"],
                                capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                return {"ok": False, "error": (r.stderr or "AP-Einrichtung auf USB fehlgeschlagen").strip()[:200]}
        else:
            return {"ok": False, "error": "Access-Point-SSID/-Passwort fehlen"}
        subprocess.run(["systemctl", "disable", "--now", "teslacam-ap-fallback.timer"], capture_output=True)
        return {"ok": True}
    finally:
        subprocess.run(["mount", "/", "-o", "remount,ro"], capture_output=True)


def apply_ap_fallback(enabled, ssid=None, password=None, ap_ip=None):
    """Toggle 'AP only as fallback when home WiFi is unavailable' mode.

    Enabling: makes sure the TESLAUSB_AP NetworkManager profile exists
    (creating it via ap-ensure.sh if needed -- requires ssid+password the
    first time; ap-ensure.sh's template already bakes in autoconnect=false)
    and enables the watcher timer that brings it up/down based on WLAN
    connectivity.
    Disabling: stops the watcher and reverts to teslausb's normal
    always-on secondary-AP behavior (autoconnect back on, AP started now).

    Every branch here ends up writing to the root filesystem (NetworkManager's
    keyfile plugin persists connection profiles under
    /etc/NetworkManager/system-connections/, systemctl enable/disable writes
    unit symlinks under /etc/systemd/system/) -- same reason
    install_ble_binaries()/apply_ssh()/set_ssh_password() below bracket their
    writes in a remount. Unlike those, this used to skip the bracket, which
    left the enable-timer/create-profile path unable to actually persist
    anything, since server.py already remounted back to ro right before
    calling this (via hubconf.write_settings()'s own bracket).
    """
    subprocess.run(["mount", "/", "-o", "remount,rw"], capture_output=True)
    try:
        r = subprocess.run(["nmcli", "-t", "-f", "NAME", "c", "show"], capture_output=True, text=True)
        has_ap = "TESLAUSB_AP" in (r.stdout or "").splitlines()

        if enabled:
            if not has_ap and not (ssid and password):
                return {"ok": False, "error": "zuerst Access-Point-SSID und -Passwort eintragen und speichern"}
            if ssid and password:
                r = subprocess.run(["bash", "/opt/teslacam-hub/ap-ensure.sh", ssid, password, ap_ip or "192.168.66.1"],
                                    capture_output=True, text=True, timeout=30)
                if r.returncode != 0:
                    return {"ok": False, "error": (r.stderr or "AP-Einrichtung fehlgeschlagen").strip()[:200]}
            else:
                _set_ap_autoconnect(False)
            subprocess.run(["systemctl", "enable", "--now", "teslacam-ap-fallback.timer"], capture_output=True)
            subprocess.run(["bash", "/opt/teslacam-hub/ap-fallback-watch.sh"], capture_output=True)
        else:
            subprocess.run(["systemctl", "disable", "--now", "teslacam-ap-fallback.timer"], capture_output=True)
            if has_ap:
                _set_ap_autoconnect(True)
                subprocess.run(["nmcli", "con", "up", "TESLAUSB_AP"], capture_output=True)
        return {"ok": True}
    finally:
        subprocess.run(["mount", "/", "-o", "remount,ro"], capture_output=True)


def ap_fallback_status():
    """Live status for the UI's on/off button: whether the feature is
    enabled, the watcher timer is running, and -- read straight from
    NetworkManager's active-connection list, same fields
    ap-fallback-watch.sh itself checks -- whether the AP is broadcasting
    right now vs. home WiFi is currently connected. Note: while the AP is
    up, home_wifi_connected reflects whatever NetworkManager currently
    reports for wlan0, but this Pi's chip has shown flakiness running
    AP+STA at once (see apply_ap_fallback's docstring) -- treat a stale
    or slow-to-update reading here as a symptom of that, not a bug in this
    status check itself."""
    enabled = hubconf.getval("AP_FALLBACK_ONLY") == "true"
    timer_active = _svc_active("teslacam-ap-fallback.timer")
    r = subprocess.run(["nmcli", "-t", "-f", "TYPE,DEVICE", "c", "show", "--active"],
                        capture_output=True, text=True)
    active_wifi_devices = [l.split(":", 1)[1] for l in (r.stdout or "").splitlines()
                            if l.startswith("802-11-wireless:")]
    return {"enabled": enabled, "timer_active": timer_active,
            "ap_broadcasting": "ap0" in active_wifi_devices,
            "home_wifi_connected": any(d != "ap0" for d in active_wifi_devices)}


def apply_hotspot_wifi(enabled, ssid=None, password=None):
    """Toggle the phone-hotspot WiFi client profile (TESLAUSB_HOTSPOT).
    Enabling (re)writes the NetworkManager keyfile via hotspot-ensure.sh
    with autoconnect on at a lower priority than home WiFi (see that
    script's header), so the Pi only falls back to the hotspot when home
    WiFi isn't in range. Disabling just deletes the profile."""
    subprocess.run(["mount", "/", "-o", "remount,rw"], capture_output=True)
    try:
        if enabled:
            if not (ssid and password):
                return {"ok": False, "error": "zuerst Hotspot-SSID und -Passwort eintragen und speichern"}
            r = subprocess.run(["bash", "/opt/teslacam-hub/hotspot-ensure.sh", ssid, password],
                                capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                return {"ok": False, "error": (r.stderr or "Hotspot-Einrichtung fehlgeschlagen").strip()[:200]}
        else:
            subprocess.run(["nmcli", "con", "delete", "TESLAUSB_HOTSPOT"], capture_output=True)
        return {"ok": True}
    finally:
        subprocess.run(["mount", "/", "-o", "remount,ro"], capture_output=True)


def hotspot_wifi_status():
    """Live status for the settings toggle: whether the profile exists and
    whether it's the one currently providing the active connection."""
    r = subprocess.run(["nmcli", "-t", "-f", "NAME", "c", "show"], capture_output=True, text=True)
    profile_exists = "TESLAUSB_HOTSPOT" in (r.stdout or "").splitlines()
    r2 = subprocess.run(["nmcli", "-t", "-f", "NAME", "c", "show", "--active"], capture_output=True, text=True)
    connected_now = "TESLAUSB_HOTSPOT" in (r2.stdout or "").splitlines()
    return {"enabled": hubconf.getval("HOTSPOT_ENABLED") == "true",
            "profile_exists": profile_exists, "connected_now": connected_now}


def apply_wireguard(enabled, peer_pubkey=None, endpoint=None, allowed_ips=None,
                     address=None, keepalive=None, psk=None, privkey=None, dns=None):
    """Toggle the home WireGuard tunnel (wg0). Enabling (re)writes
    /etc/wireguard/wg0.conf via wg-ensure.sh -- passing settings as
    KEY=VALUE lines on stdin rather than argv, since privkey/psk can come
    from an imported QR code and stdin keeps secrets off the process list
    (see that script's header). If privkey is omitted, wg-ensure.sh keeps
    this Pi's own previously-generated key (generating one on first run)
    -- so a QR import that includes a private key from the home server
    takes priority over that self-generated one. Then (re)starts
    wg-quick@wg0 so a changed config takes effect immediately rather than
    only after the next reboot."""
    if not enabled:
        subprocess.run(["systemctl", "disable", "--now", "wg-quick@wg0"], capture_output=True)
        return {"ok": True}
    if not (peer_pubkey and endpoint and address):
        return {"ok": False, "error": "Peer-Public-Key, Endpoint und Tunnel-Adresse werden benötigt"}
    stdin = "".join(f"{k}={v}\n" for k, v in [
        ("PEER_PUBKEY", peer_pubkey), ("ENDPOINT", endpoint),
        ("ALLOWED_IPS", allowed_ips or "0.0.0.0/0"), ("ADDRESS", address),
        ("KEEPALIVE", keepalive or 25), ("PSK", psk or ""),
        ("PRIVKEY", privkey or ""), ("DNS", dns or ""),
    ])
    subprocess.run(["mount", "/", "-o", "remount,rw"], capture_output=True)
    try:
        r = subprocess.run(["bash", "/opt/teslacam-hub/wg-ensure.sh"], input=stdin,
                            capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or "WireGuard-Einrichtung fehlgeschlagen").strip()[:200]}
    finally:
        subprocess.run(["mount", "/", "-o", "remount,ro"], capture_output=True)
    subprocess.run(["systemctl", "enable", "wg-quick@wg0"], capture_output=True)
    subprocess.run(["systemctl", "restart", "wg-quick@wg0"], capture_output=True)
    return {"ok": True}


def _parse_wg_config(text):
    """Parse a wg-quick-style config -- the plain text a WireGuard QR code
    (or an app's 'export config' feature) encodes -- into the flat fields
    this Hub's settings use."""
    section = None
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip().lower(), val.strip()
        if section == "interface":
            if key == "privatekey":
                out["privkey"] = val
            elif key == "address":
                out["address"] = val
            elif key == "dns":
                # A wg-quick config may list DNS twice (e.g. one line for
                # IPv4 servers, one for IPv6) -- accumulate instead of
                # overwriting, or the first line silently gets dropped.
                out["dns"] = (out["dns"] + "," + val) if out.get("dns") else val
        elif section == "peer":
            if key == "publickey":
                out["peer_pubkey"] = val
            elif key == "endpoint":
                out["endpoint"] = val
            elif key == "allowedips":
                out["allowed_ips"] = val
            elif key == "presharedkey":
                out["psk"] = val
            elif key == "persistentkeepalive":
                out["keepalive"] = val
    return out


def import_wg_qr(image_b64):
    """Decode an uploaded image (base64) as a WireGuard QR code via
    pyzbar/Pillow (packages python3-pyzbar, python3-pil, libzbar0 --
    installed by hub/install.sh) and parse the embedded wg-quick config.
    Deliberately not the zbarimg CLI (package zbar-tools): that drags in
    the full ImageMagick/libmagickwand stack just to load the image, which
    is enough to exhaust a Pi's small root partition (hit exactly that
    during development) -- pyzbar+Pillow decode the same QR with a much
    smaller dependency footprint. Returns the parsed fields for the UI to
    prefill -- doesn't write/apply anything itself, same as the rest of
    the settings form (review, then the big 'Speichern' button saves)."""
    try:
        raw = base64.b64decode(image_b64, validate=True)
    except Exception:
        return {"ok": False, "error": "ungültige Bilddaten"}
    if not raw:
        return {"ok": False, "error": "kein Bild empfangen"}
    if len(raw) > 8 * 1024 * 1024:
        return {"ok": False, "error": "Bild zu groß (max. 8 MB)"}
    try:
        from pyzbar.pyzbar import decode as zbar_decode
        from PIL import Image
    except ImportError:
        return {"ok": False, "error": "QR-Decoder nicht installiert -- hub/install.sh erneut ausführen"}
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        return {"ok": False, "error": "Datei ist kein lesbares Bild"}
    try:
        results = zbar_decode(img)
    except Exception as e:
        return {"ok": False, "error": f"QR-Decoder-Fehler: {str(e)[:150]}"}
    if not results:
        return {"ok": False, "error": "Kein QR-Code im Bild gefunden"}
    text = results[0].data.decode("utf-8", errors="replace")
    parsed = _parse_wg_config(text)
    if not (parsed.get("peer_pubkey") and parsed.get("endpoint")):
        return {"ok": False, "error": "QR-Code enthält keine gültige WireGuard-Konfiguration"}
    return {"ok": True, "config": parsed}


def wireguard_status():
    """Live status for the settings toggle: service state, this Pi's own
    public key (to paste into the home server's peer config), and --
    parsed from `wg show ... dump` -- the last handshake age and transfer
    counters so the UI can show something better than just 'active'."""
    enabled = hubconf.getval("WG_ENABLED") == "true"
    active = _svc_active("wg-quick@wg0")
    own_pubkey = ""
    try:
        with open("/etc/wireguard/publickey", encoding="utf-8") as f:
            own_pubkey = f.read().strip()
    except Exception:
        pass
    handshake, transfer = "", ""
    r = _run(["wg", "show", "wg0", "dump"])
    if r and r.returncode == 0 and r.stdout:
        lines = r.stdout.strip().splitlines()
        if len(lines) >= 2:
            parts = lines[1].split("\t")
            if len(parts) >= 7:
                try:
                    latest_hs = int(parts[4] or 0)
                    if latest_hs:
                        age = max(0, int(time.time()) - latest_hs)
                        handshake = f"vor {age}s" if age < 120 else f"vor {age // 60} min"
                    rx, tx = int(parts[5]), int(parts[6])
                    transfer = f"↓{rx // 1024} KiB / ↑{tx // 1024} KiB"
                except ValueError:
                    pass
    return {"enabled": enabled, "active": active, "own_pubkey": own_pubkey,
            "handshake": handshake, "transfer": transfer}


def set_ssh_password(password):
    """Set/reset the Linux login password for the 'pi' user -- the SSH
    login used throughout setup and by this Hub's own deploy workflow.
    Deliberately a separate secret from the vault passphrase (not derived
    from or synced to it): the vault password is never stored in
    retrievable plaintext and can be reset independently (forgot-password
    flow), so tying SSH auth to it would be both technically awkward and a
    good way to accidentally lock out SSH access."""
    if not password or len(password) < 8:
        return {"ok": False, "error": "Passwort muss mindestens 8 Zeichen haben"}
    subprocess.run(["mount", "/", "-o", "remount,rw"], capture_output=True)
    try:
        r = subprocess.run(["chpasswd"], input=f"pi:{password}\n", text=True,
                            capture_output=True, timeout=10)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or "chpasswd fehlgeschlagen").strip()[:200]}
        return {"ok": True}
    finally:
        subprocess.run(["mount", "/", "-o", "remount,ro"], capture_output=True)


def apply_ssh(disable):
    """Enable/disable SSH password login via a drop-in (audit hardening)."""
    subprocess.run(["mount", "/", "-o", "remount,rw"], capture_output=True)
    dropin = "/etc/ssh/sshd_config.d/99-teslausb.conf"
    if disable:
        with open(dropin, "w") as f:
            f.write("PasswordAuthentication no\n")
    elif os.path.exists(dropin):
        os.remove(dropin)
    subprocess.run(["mount", "/", "-o", "remount,ro"], capture_output=True)
    subprocess.run(["systemctl", "reload", "ssh"], capture_output=True) or \
        subprocess.run(["systemctl", "reload", "sshd"], capture_output=True)
    return {"ok": True}


def _has_smbd():
    return subprocess.run(["bash", "-c", "hash smbd 2>/dev/null"]).returncode == 0


def samba_status():
    """Live status for the settings toggle: package present, service active,
    and the UNC path to show the user once it's up."""
    return {"installed": _has_smbd(), "active": _svc_active("smbd"),
            "share": r"\\%s\TeslaCam" % (hubconf.getval("TESLAUSB_HOSTNAME") or "teslausb")}


def apply_samba(enabled):
    """Toggle the read-only SMB export of TeslaCam. The package itself and
    the 'pi' Samba account are provisioned once by hub/install.sh (default
    on, random password generated there if none exists yet) -- this only
    flips smbd/nmbd on or off, so it stays fast enough for a synchronous
    settings-save request. If the package was never installed (e.g. an old
    Hub build never re-ran install.sh), tell the user instead of silently
    no-op'ing.

    systemctl enable/disable write unit symlinks under
    /etc/systemd/system/, which lives on the normally-read-only root fs --
    bracket in a remount like apply_ssh()/apply_ap_fallback() do, or
    "disable" silently no-ops (start/stop alone don't need this)."""
    if not enabled:
        subprocess.run(["mount", "/", "-o", "remount,rw"], capture_output=True)
        try:
            subprocess.run(["systemctl", "disable", "--now", "smbd", "nmbd"], capture_output=True)
        finally:
            subprocess.run(["mount", "/", "-o", "remount,ro"], capture_output=True)
        return {"ok": True}
    if not _has_smbd():
        return {"ok": False, "error": "Samba ist nicht installiert -- hub/install.sh erneut ausführen"}
    subprocess.run(["mount", "/", "-o", "remount,rw"], capture_output=True)
    try:
        subprocess.run(["systemctl", "enable", "--now", "smbd", "nmbd"], capture_output=True)
    finally:
        subprocess.run(["mount", "/", "-o", "remount,ro"], capture_output=True)
    return {"ok": True}


def set_samba_password(password):
    """Set/reset the SMB login for the 'pi' Samba account (separate from
    both the vault passphrase and the Linux/SSH password -- same reasoning
    as set_ssh_password: independent secrets, no risk of one reset locking
    out another). Samba's passdb lives under /mutable (see
    setup/pi/configure-samba.sh), so unlike set_ssh_password this needs no
    root-fs remount."""
    if not password or len(password) < 8:
        return {"ok": False, "error": "Passwort muss mindestens 8 Zeichen haben"}
    if not _has_smbd():
        return {"ok": False, "error": "Samba ist nicht installiert -- hub/install.sh erneut ausführen"}
    r = subprocess.run(["smbpasswd", "-s", "-a", "pi"], input=f"{password}\n{password}\n",
                        text=True, capture_output=True, timeout=10)
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or "smbpasswd fehlgeschlagen").strip()[:200]}
    return {"ok": True}
