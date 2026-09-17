"""
"Auto wach halten" switch: sends a BLE 'wake' nudge every NUDGE_INTERVAL_SEC
while active, for up to a configurable expiry. The same nudge schedule also
serves synchold.py's sync hold (at home, keep the car awake until the NAS
sync is done), at the shorter HOLD_NUDGE_INTERVAL_SEC.

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

Measured 2026-09-13 (README, "Aufwecken ist nicht Wachhalten"): 'wake' does
NOT keep a locked, parked car awake at any cadence. It wakes a sleeping car
(not even reliably on the first try), but a wake sent to an awake car
doesn't extend its ~1-4 min window -- even one nudge per minute let it fall
asleep in between. Upstream's charge-port-close works because it is a real
vehicle action, which this key can't send. So neither the manual switch nor
synchold's hold actually holds the car today; the nudge schedule below only
stays until a working mechanism (e.g. Sentry Mode through a key that may
set it) replaces it.

State (active + expiry + last nudge time) is persisted to a small JSON
file so a Hub reboot/restart while the car is still supposed to stay awake
doesn't lose track -- server.py's keepawake_loop re-derives everything
from this file, not from in-memory state.
"""
import json, os, time
import diag

_state_path = None
DEFAULT_HOURS = 24
MAX_HOURS = 48
NUDGE_INTERVAL_SEC = 5 * 60
# While synchold says the car must stay awake for a sync. Picked to stay well
# under the ~3-3.5 min one 'wake' seemed to buy; the 2026-09-13 measurement
# then showed that no interval holds the car (see the module docstring).
HOLD_NUDGE_INTERVAL_SEC = 2 * 60


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
            return {"active": True, "until": st["until"], "remaining_sec": int(remaining),
                    "failing": bool(st.get("failing"))}
    return {"active": False, "until": None, "remaining_sec": 0, "failing": False}


def start(hours=None):
    try:
        hours = float(hours) if hours is not None else DEFAULT_HOURS
    except (TypeError, ValueError):
        hours = DEFAULT_HOURS
    hours = max(0.5, min(hours, MAX_HOURS))
    now = time.time()
    until = now + hours * 3600
    r = diag.ble_exec("awake", "wake")
    # Activate regardless of whether this first nudge succeeds. BLE can be
    # flaky for a single attempt (range, the car's own connection-slot
    # limit, a momentary drop) without the vehicle being unreachable
    # overall -- tick() already retries every ~minute and tracks
    # failing/recovered transitions for exactly this. Gating activation on
    # one synchronous attempt made the switch silently fail to turn on
    # right when BLE trouble makes it needed most. last_nudge=0 on failure
    # so tick()'s very next check retries immediately instead of waiting
    # out the full 5-minute interval.
    _save({"active": True, "until": until, "last_nudge": now if r.get("ok") else 0})
    if not r.get("ok"):
        return {"ok": True, "until": until, "hours": hours,
                "warning": r.get("detail") or r.get("error") or "Erster Weck-Befehl fehlgeschlagen -- wird automatisch wiederholt"}
    return {"ok": True, "until": until, "hours": hours}


def stop():
    _save({"active": False, "until": None, "last_nudge": None})
    return {"ok": True}


def nudge_failing():
    return bool(_load().get("failing"))


def tick(hold_active=False):
    """Called about every 30s from server.py's keepawake_loop. Sends a
    'wake' nudge every NUDGE_INTERVAL_SEC while the manual switch is active,
    or every HOLD_NUDGE_INTERVAL_SEC while hold_active (synchold: at home,
    sync still pending) -- one shared schedule, so both at once never double
    up on BLE. Auto-stops the manual switch once its expiry passes. Returns
    a dict {"event": ...} with event one of "nudge", "nudge_failed",
    "nudge_still_failing", "nudge_recovered", "expired", or None (nothing
    due yet / nothing active).

    A failed nudge deliberately does NOT update last_nudge, so it's retried
    on the very next tick instead of waiting out the full interval again.
    "failing" is tracked/persisted so the caller can log just the
    fail/recover transitions instead of on every tick for as long as an
    outage lasts."""
    st = _load()
    now = time.time()
    manual = bool(st.get("active"))
    if manual and st.get("until") and now >= st["until"]:
        _save({"active": False, "until": None, "last_nudge": None, "failing": False})
        return {"event": "expired"}
    if not manual and not hold_active:
        if st.get("failing"):
            st["failing"] = False
            _save(st)
        return None
    interval = HOLD_NUDGE_INTERVAL_SEC if hold_active else NUDGE_INTERVAL_SEC
    if now - (st.get("last_nudge") or 0) >= interval:
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
