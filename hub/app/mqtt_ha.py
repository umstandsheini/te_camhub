"""
Home Assistant integration via MQTT Discovery.

Publishes the Hub as one HA device with a handful of read-only sensors
(clip counts, NAS-archive coverage, Pi temperature, USB/car connection,
vault lock state, WiFi SSID), plus a second set of entities for the BLE
vehicle-command functions the Hub actually implements (see diag.BLE_READS/
BLE_ACTIONS). The BLE entities follow the same shape as the yoziru/
esphome-tesla-ble integration (proper binary_sensor/sensor/switch/number/
button domains instead of one generic blob per read) so it looks and
behaves the same way in HA. For reads with many related fields (closures,
tire pressure, charge), one "headline" value becomes the entity's state and
the rest are exposed as that entity's attributes -- e.g. "Verriegelt"
on/off with per-door/per-window detail as attributes, mirroring how
esphome-tesla-ble's own door lock works.

Connection is optional and off by default (MQTT_ENABLED); credentials are
never logged. Uses paho-mqtt's own background network thread (loop_start),
so connect()/publish_state() here are non-blocking and safe to call from
server.py's periodic loop.
"""
import json, threading
import diag

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

DEVICE_ID = "teslacam_hub"
BASE = "teslacam_hub"
AVAIL_TOPIC = f"{BASE}/status"

DEVICE_INFO = {
    "identifiers": [DEVICE_ID],
    "name": "TeslaCam Hub",
    "manufacturer": "DIY (teslausb fork)",
    "model": "Raspberry Pi",
}

# object_id -> (component, name, device_class, unit, icon)
SENSORS = {
    "clips":     ("sensor", "Aufnahmen gesamt", None, None, "mdi:filmstrip"),
    "encrypted": ("sensor", "Verschlüsselte Aufnahmen", None, None, "mdi:lock"),
    "nas_percent": ("sensor", "NAS-Archivierung", None, "%", "mdi:cloud-upload"),
    "temp":      ("sensor", "Pi-Temperatur", "temperature", "°C", None),
    "wifi_ssid": ("sensor", "WLAN", None, None, "mdi:wifi"),
    "usb_connected": ("binary_sensor", "USB am Auto", "connectivity", None, None),
    "vault_unlocked": ("binary_sensor", "Tresor entsperrt", "lock", None, None),
}

# ---------------------------------------------------------------------------
# BLE reads: which field becomes the entity's state, everything else in that
# read's result becomes an HA attribute on the same entity. `binary` fields
# are compared against BOOL_TRUE below instead of copied as text.
# read_id -> (component, name, primary_field, icon, unit, device_class)
BLE_READ_ENTITIES = {
    "closures": ("binary_sensor", "Verriegelt", "locked", "mdi:car-door-lock", None, "lock"),
    "body_controller": ("binary_sensor", "Schläft", "vehicleSleepStatus", "mdi:sleep", None, None),
    "charge": ("sensor", "Ladezustand", "chargingState", "mdi:ev-station", None, None),
    "tire_pressure": ("sensor", "Reifendruck", "timestamp", "mdi:car-tire-alert", None, "timestamp"),
    "climate": ("sensor", "Innentemperatur", "insideTempCelsius", "mdi:thermometer", "°C", None),
    # "state location" (standalone) has no locationName field, unlike the
    # locationState nested inside "state drive" -- use latitude as the
    # headline value, longitude/heading/accuracy/... land in attributes.
    "location": ("sensor", "Standort", "latitude", "mdi:map-marker", "°", None),
    # "state drive" uniquely returns two wrapper keys (driveState +
    # locationState) instead of one, so _flatten_state prefixes its fields.
    "drive": ("sensor", "Schaltstellung", "driveState.shiftState", "mdi:car-shift-pattern", None, None),
    "media": ("sensor", "Medienstatus", None, "mdi:play-circle", None, None),
    "media_detail": ("sensor", "Medien-Details", None, "mdi:music-note", None, None),
    "charge_schedule": ("sensor", "Lade-Zeitplan", None, "mdi:calendar-clock", None, None),
    "precondition_schedule": ("sensor", "Vorklimatisierungs-Zeitplan", None, "mdi:calendar-clock", None, None),
    "software_update": ("sensor", "Software-Update-Status", "status", "mdi:update", None, None),
    "parental_controls": ("sensor", "Kindersicherung", None, "mdi:account-child", None, None),
    "list_keys": ("sensor", "Anzahl Schlüssel", "anzahl_schluessel", "mdi:key-chain", None, None),
    "ping": ("sensor", "Erreichbarkeit", "erreichbar", "mdi:bluetooth-connect", None, None),
}
_BOOL_TRUE = {"true", "1", "yes", "vehicle_sleep_status_asleep"}

# Two related actions collapse into one HA switch each (matches
# esphome-tesla-ble's switch.*_charger pattern) instead of two separate
# stateless buttons.
BLE_SWITCHES = {
    "charging": ("Laden", "mdi:battery-charging", "charging_start", "charging_stop"),
    "accessory_power": ("Zubehör-Stromversorgung", "mdi:power-plug", "keep_accessory_power_on", "keep_accessory_power_off"),
}
# action_id -> (label, min, max, step, unit)
BLE_NUMBERS = {
    "charging_set_limit": ("Ladegrenze", 50, 100, 1, "%"),
    "charging_set_amps": ("Ladestrom", 5, 32, 1, "A"),
}
# action_id -> label
BLE_BUTTONS = {
    "wake": "Auto aufwecken",
    "charging_schedule_cancel": "Lade-Zeitplan abbrechen",
}

_lock = threading.Lock()
_client = None
_connected = False
_command_handler = None  # fn(action_id: str, value: str|None) -> None


def _topic(kind, oid):
    return f"{BASE}/{oid}/{kind}"


def _ble_topic(kind, object_id):
    return f"{BASE}/ble_{object_id}/{kind}"


def set_command_handler(fn):
    """Register the callback invoked when an HA entity for a BLE action
    fires: fn(action_id, value_or_None). value is the number's numeric
    string for number entities, None for buttons and switch-off, "on" is
    translated to the switch's own on_action before this is called."""
    global _command_handler
    _command_handler = fn


def _on_message(client, userdata, msg):
    if _command_handler is None:
        return
    try:
        object_id = msg.topic.split("/")[1]
        if object_id.startswith("ble_"):
            object_id = object_id[len("ble_"):]
        payload = msg.payload.decode("utf-8", "replace").strip()
        if object_id in BLE_SWITCHES:
            _label, _icon, on_action, off_action = BLE_SWITCHES[object_id]
            _command_handler(on_action if payload.upper() == "ON" else off_action, None)
            return
        _command_handler(object_id, payload or None)
    except Exception as e:
        print("[hub] mqtt command:", e, flush=True)


def _on_connect(client, userdata, flags, rc, properties=None):
    global _connected
    _connected = (rc == 0)
    if not _connected:
        return
    client.publish(AVAIL_TOPIC, "online", retain=True)
    for oid, (component, name, device_class, unit, icon) in SENSORS.items():
        payload = {
            "name": name,
            "unique_id": f"{DEVICE_ID}_{oid}",
            "state_topic": _topic("state", oid),
            "availability_topic": AVAIL_TOPIC,
            "device": DEVICE_INFO,
        }
        if device_class:
            payload["device_class"] = device_class
        if unit:
            payload["unit_of_measurement"] = unit
        if icon:
            payload["icon"] = icon
        if component == "binary_sensor":
            payload["payload_on"] = "ON"
            payload["payload_off"] = "OFF"
        client.publish(f"homeassistant/{component}/{DEVICE_ID}/{oid}/config",
                        json.dumps(payload), retain=True)

    for read_id, (component, name, _field, icon, unit, device_class) in BLE_READ_ENTITIES.items():
        payload = {
            "name": name,
            "unique_id": f"{DEVICE_ID}_ble_{read_id}",
            "state_topic": _ble_topic("state", read_id),
            "json_attributes_topic": _ble_topic("attributes", read_id),
            "availability_topic": AVAIL_TOPIC,
            "device": DEVICE_INFO,
            "icon": icon,
        }
        if device_class:
            payload["device_class"] = device_class
        if unit:
            payload["unit_of_measurement"] = unit
        if component == "binary_sensor":
            payload["payload_on"] = "ON"
            payload["payload_off"] = "OFF"
        client.publish(f"homeassistant/{component}/{DEVICE_ID}/ble_{read_id}/config",
                        json.dumps(payload), retain=True)

    for object_id, (label, icon, _on_a, _off_a) in BLE_SWITCHES.items():
        payload = {
            "name": label,
            "unique_id": f"{DEVICE_ID}_ble_{object_id}",
            "command_topic": _ble_topic("set", object_id),
            "state_topic": _ble_topic("state", object_id),
            "availability_topic": AVAIL_TOPIC,
            "device": DEVICE_INFO,
            "icon": icon,
            "payload_on": "ON",
            "payload_off": "OFF",
            "optimistic": object_id == "accessory_power",  # no readback command exists for this one
        }
        client.publish(f"homeassistant/switch/{DEVICE_ID}/ble_{object_id}/config",
                        json.dumps(payload), retain=True)
        client.subscribe(_ble_topic("set", object_id))

    for action_id, (label, lo, hi, step, unit) in BLE_NUMBERS.items():
        payload = {
            "name": label,
            "unique_id": f"{DEVICE_ID}_ble_{action_id}",
            "command_topic": _ble_topic("set", action_id),
            "state_topic": _ble_topic("state", action_id),
            "availability_topic": AVAIL_TOPIC,
            "device": DEVICE_INFO,
            "icon": "mdi:tune",
            "min": lo, "max": hi, "step": step, "unit_of_measurement": unit,
        }
        client.publish(f"homeassistant/number/{DEVICE_ID}/ble_{action_id}/config",
                        json.dumps(payload), retain=True)
        client.subscribe(_ble_topic("set", action_id))

    for action_id, label in BLE_BUTTONS.items():
        payload = {
            "name": label,
            "unique_id": f"{DEVICE_ID}_ble_{action_id}",
            "command_topic": _ble_topic("set", action_id),
            "availability_topic": AVAIL_TOPIC,
            "device": DEVICE_INFO,
            "icon": "mdi:remote",
        }
        client.publish(f"homeassistant/button/{DEVICE_ID}/ble_{action_id}/config",
                        json.dumps(payload), retain=True)
        client.subscribe(_ble_topic("set", action_id))


def _on_disconnect(client, userdata, rc, properties=None):
    global _connected
    _connected = False


def ensure_connected(host, port, user, password):
    """(Re)connect if not already connected with the current settings. No-op
    if paho-mqtt isn't installed or host is empty."""
    global _client
    if mqtt is None or not host:
        return False
    with _lock:
        if _client is not None and _connected:
            return True
        try:
            if _client is not None:
                try:
                    _client.loop_stop()
                except Exception:
                    pass
            try:
                # paho-mqtt >=2.0 requires an explicit callback API version;
                # VERSION1 keeps the on_connect/on_disconnect signatures below
                # working unchanged across both 1.x and 2.x.
                c = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                                 client_id="teslacam-hub")
            except AttributeError:
                c = mqtt.Client(client_id="teslacam-hub", protocol=mqtt.MQTTv311)
            if user:
                c.username_pw_set(user, password or "")
            c.will_set(AVAIL_TOPIC, "offline", retain=True)
            c.on_connect = _on_connect
            c.on_disconnect = _on_disconnect
            c.on_message = _on_message
            c.connect(host, int(port or 1883), keepalive=60)
            c.loop_start()
            _client = c
            return True
        except Exception as e:
            print("[hub] mqtt connect failed:", e, flush=True)
            _client = None
            return False


def publish_state(values: dict):
    """values: {object_id: value}. Only publishes known sensors."""
    if _client is None or not _connected:
        return
    for oid, val in values.items():
        spec = SENSORS.get(oid)
        if not spec:
            continue
        component = spec[0]
        if component == "binary_sensor":
            val = "ON" if val else "OFF"
        try:
            _client.publish(_topic("state", oid), str(val))
        except Exception:
            pass


def publish_ble_read(read_id, values: dict):
    """Publish one BLE read result the esphome-tesla-ble way: the
    configured headline field becomes the entity's state, every other
    field in the same read becomes an attribute on that entity."""
    if _client is None or not _connected:
        return
    spec = BLE_READ_ENTITIES.get(read_id)
    if not spec:
        return
    component, _name, field, *_rest = spec
    if field and field in values:
        state = values[field]
    elif values:
        state = next(iter(values.values()))
    else:
        state = "unknown"
    if component == "binary_sensor":
        state = "ON" if str(state).strip().lower() in _BOOL_TRUE else "OFF"
    attrs = {k: v for k, v in values.items() if k != field}
    try:
        _client.publish(_ble_topic("state", read_id), str(state), retain=True)
        _client.publish(_ble_topic("attributes", read_id), json.dumps(attrs), retain=True)
    except Exception:
        pass

    # Keep the "Laden" switch's state in sync with real charging status
    # whenever a fresh charge read comes in, instead of staying optimistic.
    if read_id == "charge" and "chargingState" in values and _client is not None:
        try:
            on = str(values["chargingState"]).strip().lower() == "charging"
            _client.publish(_ble_topic("state", "charging"), "ON" if on else "OFF", retain=True)
        except Exception:
            pass


def publish_ble_action_state(action_id, value):
    """Reflect the last commanded value for number-type BLE actions
    (charging_set_limit/charging_set_amps) back to their HA state_topic."""
    if _client is None or not _connected:
        return
    try:
        _client.publish(_ble_topic("state", action_id), str(value), retain=True)
    except Exception:
        pass


def disconnect():
    global _client, _connected
    with _lock:
        if _client is not None:
            try:
                _client.publish(AVAIL_TOPIC, "offline", retain=True)
                _client.loop_stop()
                _client.disconnect()
            except Exception:
                pass
        _client = None
        _connected = False
