"""
"Auto wach halten" switch: sends a BLE 'wake' nudge every NUDGE_INTERVAL_SEC
while active, for up to a configurable expiry.

Originally this called keep-accessory-power on/off once and left it at
that, but Tesla's own docs say that feature doesn't apply to the data port
used by Dashcam/TeslaUSB -- confirmed in practice (car fell asleep a few
minutes after the switch was flipped on). The upstream teslausb project
works around exactly this by periodically nudging the car instead (every
5 min in its bash implementation); same idea here, just using 'wake'
(tesla-control's own wake command) since the nudge command upstream uses
for BLE (charge-port-close) is one of the ones this key's charging_manager
role has been confirmed to lack privileges for (see diag.py's BLE_ACTIONS
comment).

State (active + expiry + last nudge time) is persisted to a small JSON
file so a Hub reboot/restart while the car is still supposed to stay awake
doesn't lose track -- server.py's keepawake_loop re-derives everything
from this file, not from in-memory state.
"""
import json, os, time
import diag

_state_path = None
DEFAULT_HOURS = 24
MAX_HOURS = 72
NUDGE_INTERVAL_SEC = 5 * 60


def init(state_dir):
    global _state_path
    _state_path = os.path.join(state_dir, "keepawake.json")


def _load():
    try:
        with open(_state_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"active": False, "until": None, "last_nudge": None}


def _save(state):
    tmp = _state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, _state_path)


def status():
    st = _load()
    if st.get("active") and st.get("until"):
        remaining = st["until"] - time.time()
        if remaining > 0:
            return {"active": True, "until": st["until"], "remaining_sec": int(remaining)}
    return {"active": False, "until": None, "remaining_sec": 0}


def start(hours=None):
    try:
        hours = float(hours) if hours is not None else DEFAULT_HOURS
    except (TypeError, ValueError):
        hours = DEFAULT_HOURS
    hours = max(0.5, min(hours, MAX_HOURS))
    r = diag.ble_exec("awake", "wake")
    if not r.get("ok"):
        return {"ok": False, "error": r.get("detail") or "BLE-Befehl fehlgeschlagen"}
    now = time.time()
    until = now + hours * 3600
    _save({"active": True, "until": until, "last_nudge": now})
    return {"ok": True, "until": until, "hours": hours}


def stop():
    _save({"active": False, "until": None, "last_nudge": None})
    return {"ok": True}


def tick():
    """Called about once a minute from server.py's keepawake_loop. Sends a
    'wake' nudge every NUDGE_INTERVAL_SEC while active, and auto-stops once
    the expiry passes. Returns a dict {"event": ...} with event one of
    "nudge", "nudge_failed", "nudge_still_failing", "nudge_recovered",
    "expired", or None (nothing due yet / not active).

    A failed nudge deliberately does NOT update last_nudge, so it's retried
    on the very next tick (~1 min later) instead of waiting out the full
    5-minute interval again. "failing" is tracked/persisted so the caller
    can log just the fail/recover transitions instead of once a minute for
    as long as an outage lasts."""
    st = _load()
    if not st.get("active"):
        return None
    now = time.time()
    if st.get("until") and now >= st["until"]:
        _save({"active": False, "until": None, "last_nudge": None, "failing": False})
        return {"event": "expired"}
    if now - (st.get("last_nudge") or 0) >= NUDGE_INTERVAL_SEC:
        r = diag.ble_exec("awake", "wake")
        was_failing = st.get("failing", False)
        if r.get("ok"):
            st["last_nudge"] = now
            st["failing"] = False
            _save(st)
            return {"event": "nudge_recovered" if was_failing else "nudge"}
        st["failing"] = True
        _save(st)
        return {"event": "nudge_still_failing" if was_failing else "nudge_failed",
                "error": r.get("error") or r.get("detail")}
    return None
