"""
"Is the Hub in its car?" -- verified over BLE against the paired vehicle.

The Pi has no way to tell from the USB side whether it is plugged into *the*
car: the gadget only knows that some host configured the drives. The one
thing that identifies the vehicle is the BLE key pairing -- tesla-control
talks to exactly the VIN from the config, authenticated with the key the car
accepted. So a successful BLE ping means: this Pi is within Bluetooth range
(a few metres) of that car, right now.

What it proves and what it doesn't:
  - proves: the paired car is nearby and answering
  - does NOT prove the Pi is plugged into it (BLE reaches through the glove
    box just as well from the parking space next to it), and a car that is
    asleep may simply not answer -- absence is not proof of theft.
So this is a hint, not an alarm: used for the status display, an event-log
line when it changes, and a Home Assistant sensor.

Cheap on purpose: one `ping` (BLE_READS["ping"], the shortest command
tesla-control has) every CHECK_EVERY seconds, and never while another part
of the Hub holds the single BLE connection slot -- diag's own BLE lock takes
care of that. A check is skipped entirely while the car is known to be
driving (the trip loop is using BLE anyway) and it is never the reason the
car gets woken: `ping` does not wake a sleeping car.
"""
import json, os, threading, time
import hubconf
import diag
import eventlog

STATE_FILE = "presence.json"
CHECK_EVERY = 600          # 10 min
FAIL_GRACE = 3             # consecutive failures before "not in the car" is reported
KEY_NAME = "awake"         # the named BLE key the rest of the Hub uses

_guard = threading.Lock()
_state_dir = None
_state = {"in_car": None, "checked": None, "last_seen": None, "since": None,
          "fails": 0, "error": None, "vin_set": False}


def init(state_dir):
    global _state_dir
    _state_dir = state_dir
    stored = _read()
    if stored:
        with _guard:
            _state.update({k: stored.get(k, _state[k]) for k in
                           ("in_car", "checked", "last_seen", "since", "error")})
            _state["fails"] = 0      # a restart is no evidence either way


def _path():
    return os.path.join(_state_dir, STATE_FILE)


def _read():
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save():
    if not _state_dir:
        return
    try:
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_state, f)
        os.replace(tmp, _path())
    except OSError:
        pass


def configured():
    return bool(hubconf.getval("TESLA_BLE_VIN")) and diag.ble_binaries_installed()


def status():
    with _guard:
        s = dict(_state)
    s["vin_set"] = bool(hubconf.getval("TESLA_BLE_VIN"))
    s["configured"] = configured()
    s["usb_host"] = bool(diag.status().get("gadget_active"))
    return s


def check():
    """One BLE ping against the paired car. Returns the new status."""
    if not configured():
        with _guard:
            _state.update(checked=time.time(), error="No paired BLE key / no VIN configured")
        _save()
        return status()
    r = diag.ble_read(KEY_NAME, "ping")
    now = time.time()
    with _guard:
        was = _state["in_car"]
        _state["checked"] = now
        if r.get("ok"):
            _state.update(in_car=True, last_seen=now, fails=0, error=None)
            if was is not True:
                _state["since"] = now
        else:
            _state["fails"] += 1
            _state["error"] = (r.get("error") or "no answer")[:200]
            # One failed ping means little: the car may be asleep or the
            # connection slot was busy. Only a run of them flips the state.
            if _state["fails"] >= FAIL_GRACE and was is not False:
                _state.update(in_car=False, since=now)
            elif _state["fails"] < FAIL_GRACE and was is True:
                pass        # keep the old state until the grace is used up
        new = _state["in_car"]
    _save()
    if new != was:
        if new:
            eventlog.log_event("ble", "Vehicle confirmed over Bluetooth: the Hub is with the paired car")
        elif new is False:
            eventlog.log_event("ble", "Vehicle no longer reachable over Bluetooth "
                                      "(car asleep, out of range – or the Hub is no longer in the car)")
    return status()


def loop():
    """Background check. Skips while a trip is running: the trip loop is
    already talking to the car every 10 s, which answers the same question."""
    time.sleep(90)
    while True:
        try:
            if configured() and not _trip_active():
                check()
        except Exception as e:
            print("[hub] presence:", e, flush=True)
        time.sleep(CHECK_EVERY)


_trip_active_fn = None


def set_trip_probe(fn):
    """server.py hands in a callable telling us whether a trip is being
    recorded right now (it owns that state)."""
    global _trip_active_fn
    _trip_active_fn = fn


def _trip_active():
    try:
        return bool(_trip_active_fn and _trip_active_fn())
    except Exception:
        return False


def note_ble_success():
    """Any other successful BLE read/command is just as good a proof that
    the car is nearby -- server.py calls this from the trip loop, so a
    driving car doesn't need an extra ping."""
    now = time.time()
    with _guard:
        was = _state["in_car"]
        _state.update(in_car=True, last_seen=now, checked=now, fails=0, error=None)
        if was is not True:
            _state["since"] = now
            changed = True
        else:
            changed = False
    _save()
    if changed:
        eventlog.log_event("ble", "Vehicle confirmed over Bluetooth: the Hub is with the paired car")
