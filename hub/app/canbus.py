"""
BLE-OBD-Dongle (UniCarScan o.ä., ELM327 über GATT) -> Tesla-CAN-Bus.

Verbindet sich pro Aufruf frisch (gleiches Muster wie diag.py's
tesla-control-Aufrufe): GATT-Connect, ELM327 initialisieren, ein paar
Sekunden CAN-Traffic mitschneiden, bekannte Tesla-Frames dekodieren,
trennen. Kein Dauer-Polling, keine Pairing/Bonding nötig -- ein offener
BLE-GATT-Connect genügt (empirisch bestätigt).

Serialisiert gegen tesla-control über diag._ble_lock: der Pi hat genau
einen Bluetooth-Adapter (siehe diag.py).

Frame-Definitionen gegen die echte DBC geprüft: github.com/joshwardell/model3dbc
(Model3CAN.dbc, "VehicleBus" -- das ist der klassische CAN-Bus, den der
OBD-Port/ELM327 abgreift). talas9/tesla_can_signals dokumentiert dagegen den
neueren Ethernet-Bus (ModelY_ETH.compact.json) und ist hier nicht einschlägig.

CP_status (0x25D)'s CP_chargeCablePresent/CP_chargeDoorOpen-Bits stimmen laut
DBC exakt mit der hier verwendeten Bit-Rechnung überein, lieferten aber am
echten Fahrzeug (Model Y) durchgehend False, obwohl aktiv geladen wurde --
vermutlich hat diese Baureihe/Firmware die Bedeutung dieser Bits verschoben.
Deshalb wird stattdessen CP_proximity aus CP_evseStatus (0x21D) verwendet,
das live gegen den bekannten Ladezustand verifiziert wurde (LATCHED während
des Ladens).

data/tesla_model_y_merged.dbc (siehe _load_dbc_names) überlagert model3dbc,
talas9/tesla_can_signals, onyx-m2-dbc und opendbc nach Priorität und dient
ausschließlich dazu, IDs außerhalb von KNOWN_IDS unten in der "unbekannte
CAN-IDs"-Explorer-Liste mit einem Namen zu versehen -- reine Suchhilfe, keine
verifizierte Dekodierung wie bei KNOWN_IDS.
"""
import asyncio, os, re, threading, time
from bleak import BleakClient
import diag, hubconf

NOTIFY_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"
DEFAULT_MAC = "01:1D:A5:02:2C:CB"

# Merged reference DBC (joshwardell/model3dbc + talas9/tesla_can_signals +
# onyx-m2/onyx-m2-dbc + commaai/opendbc, overlaid in that priority order for
# max ID coverage -- see hub/app/data/tesla_model_y_merged.dbc). Only used to
# *name* frames outside KNOWN_IDS for the "unbekannte CAN-IDs" explorer below
# -- none of these names/layouts have been empirically checked against this
# vehicle (unlike KNOWN_IDS, which was verified live), so treat them as a
# search hint, not a decoded value.
_DBC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tesla_model_y_merged.dbc")
_BO_RE = re.compile(r'^BO_\s+(\d+)\s+(\S+?):')


def _load_dbc_names(path):
    names = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = _BO_RE.match(line)
                if m:
                    names[int(m.group(1))] = m.group(2)
    except OSError:
        pass
    return names


_DBC_NAMES = _load_dbc_names(_DBC_PATH)


def _bits_le(data, start, length):
    val = int.from_bytes(data, "little")
    return (val >> start) & ((1 << length) - 1)


def _s16le(data, offset):
    v = data[offset] | (data[offset + 1] << 8)
    return v - 65536 if v > 32767 else v


# CAN-IDs, die _parse_frames unten in benannte Werte übersetzt. Alles andere
# landet -- mit letztem gesehenen Rohbyte-Inhalt -- in "unknown_frames", damit
# man beim Fahren/Blinken/Türen öffnen selbst nach Mustern suchen kann.
KNOWN_IDS = {0x132, 0x292, 0x212, 0x33A, 0x383, 0x21D, 0x204, 0x333, 0x31C, 0x219}


_HEX = set("0123456789ABCDEFabcdef")


def _is_clean_frame(parts):
    # Exakt ID + 8 Datenbytes, jedes Datenbyte-Token exakt 2 Hexzeichen --
    # verwirft verschmolzene/korrupte Zeilen (haeufig bei der BLE-Notify-
    # Kette dieses Pi-Bluetooth-Chips), die zufaellig >=9 Tokens haben,
    # aber kein echter 8-Byte-Frame sind (empirisch bei Reverse-Engineering
    # gefunden: ohne diese Pruefung taucht viel Phantom-"Rauschen" auf).
    if len(parts) != 9 or not (1 <= len(parts[0]) <= 3):
        return False
    return all(len(p) == 2 and all(c in _HEX for c in p) for p in parts[1:])


def _parse_frames(raw_text):
    values = {}
    seen = set()
    unknown = {}
    for line in raw_text.replace("\r", "\n").replace(">", "").split("\n"):
        parts = line.strip().split()
        if not _is_clean_frame(parts):
            continue
        try:
            cid = int(parts[0], 16)
            d = bytes(int(b, 16) for b in parts[1:9])
        except (ValueError, IndexError):
            continue
        seen.add(cid)
        if cid not in KNOWN_IDS:
            name = _DBC_NAMES.get(cid)
            key = f"{cid:X} {name}" if name else f"{cid:X}"
            unknown[key] = " ".join(f"{b:02X}" for b in d)
            continue

        if cid == 0x132:  # BMS_hvBusStatus
            v = round((d[0] | (d[1] << 8)) * 0.01, 1)
            a = round(_s16le(d, 2) * 0.1, 1)
            values["hv_spannung_v"] = v
            values["hv_strom_a"] = a
            values["hv_leistung_kw"] = round(v * a / 1000, 2)
        elif cid == 0x292:  # BMS_socStatus (nicht in model3dbc, aus tesla_remote.py übernommen)
            values["soc_ui_pct"] = round(_bits_le(d, 10, 10) * 0.1, 1)
        elif cid == 0x212:  # BMS_status
            cs = _bits_le(d, 11, 3)  # BMS_uiChargeStatus
            values["ladestatus"] = ["GETRENNT", "KEIN_STROM", "GLEICH_START", "LAEDT",
                                     "FERTIG", "GESTOPPT", "KALIBRIERUNG"][min(cs, 6)]
            raw_pwr = _bits_le(d, 40, 11)  # BMS_chgPowerAvailable, scale 0.125 kW
            if raw_pwr < 2047:
                values["ladeleistung_verfuegbar_kw"] = round(raw_pwr * 0.125, 1)
            values["bms_chargerequest"] = bool(_bits_le(d, 29, 1))
        elif cid == 0x33A:  # UI_rangeSOC
            values["reichweite_km"] = round(_bits_le(d, 0, 10) * 1.60934)  # UI_Range, mi->km
            # UI_SOC (bit48) laut DBC probiert, live aber grob falsch (17% statt
            # tatsächlicher ~78%) -- verworfen, soc_ui_pct (0x292) ist verlässlich.
        # 0x2B4 PCS_dcdcRailStatus (PCS_dcdcLvBusVolt) ausgelassen: liefert live
        # konstant ~22V (Soll 12-14,6V) und die LV-Stromgegenprobe absurde >200A --
        # Byte-Layout für dieses Frame passt bei diesem Fahrzeug/dieser Firmware
        # nicht zur model3dbc-Referenz, kein verlässlicher Ersatzwert gefunden.
        elif cid == 0x383:  # VCRIGHT_thsStatus
            values["innentemp_c"] = _bits_le(d, 1, 8) - 40  # VCRIGHT_thsTemperature
            values["innenfeuchte_pct"] = _bits_le(d, 17, 8)  # VCRIGHT_thsHumidity (Breite 8, nicht 7)
        elif cid == 0x21D:  # CP_evseStatus -- verlässlicher als CP_status für "Kabel gesteckt"
            prox = _bits_le(d, 2, 2)  # CP_proximity
            values["ladekabel_status"] = ["UNBEKANNT", "GETRENNT", "ENTRIEGELT", "VERRIEGELT"][prox]
            values["pilot_strom_a"] = round(_bits_le(d, 8, 8) * 0.5, 1)  # CP_pilotCurrent
        elif cid == 0x204:  # PCS_chgStatus
            values["ac_leistung_kw"] = round(_bits_le(d, 16, 8) * 0.1, 1)  # PCS_chgInstantAcPowerAvailable
            values["ac_leistung_max_kw"] = round(_bits_le(d, 24, 8) * 0.1, 1)  # PCS_chgMaxAcPowerAvailable
        elif cid == 0x333:  # UI_chargeRequest
            values["ladelimit_pct"] = round(_bits_le(d, 16, 10) * 0.1, 1)  # UI_chargeTerminationPct
            values["ladestrom_limit_a"] = _bits_le(d, 8, 7)  # UI_acChargeCurrentLimit
            values["laden_angefordert"] = bool(_bits_le(d, 2, 1))  # UI_chargeEnableRequest
        elif cid == 0x31C:  # CC_chgStatus
            values["ladekabel_phasen"] = _bits_le(d, 10, 2)  # CC_numPhases
            values["ladekabel_limit_a"] = round(_bits_le(d, 0, 8) * 0.5, 1)  # CC_currentLimit
        elif cid == 0x219:  # VCSEC_TPMSData -- ein Frame pro Rad, Index in Bit0-1.
            # Location/Voltage/Temperature zeigten live nur Sentinel-Werte (0xFF =
            # "noch kein Messwert") und keine bestätigte Rad-Zuordnung (kein VAL_ in
            # der DBC) -- nur Druck wird übernommen, Räder nummeriert statt benannt.
            idx = _bits_le(d, 0, 2)
            pressure_raw = _bits_le(d, 8, 8)
            if pressure_raw != 0xFF:
                values[f"reifendruck_rad{idx}_bar"] = round(pressure_raw * 0.025, 2)
    return values, seen, unknown


async def _read_async(mac, duration):
    buf = []

    def on_notify(_sender, data):
        buf.append(data.decode("utf-8", errors="replace"))

    async def send(client, cmd, wait=1.5):
        buf.clear()
        await client.write_gatt_char(WRITE_UUID, (cmd + "\r").encode(), response=False)
        await asyncio.sleep(wait)
        return "".join(buf).strip()

    async with BleakClient(mac, timeout=15.0) as client:
        await client.start_notify(NOTIFY_UUID, on_notify)
        await send(client, "ATZ", 2.0)
        await send(client, "ATE0")
        await send(client, "ATL0")
        await send(client, "ATSP6")   # CAN 11bit/500kbps
        await send(client, "ATH1")    # Header anzeigen
        await send(client, "ATCAF0")  # Rohe CAN-Frames, kein ISO-TP-Zusammenbau
        raw = await send(client, "ATMA", float(duration))
        await send(client, "\r", 0.3)  # ATMA stoppen
        await client.stop_notify(NOTIFY_UUID)
    return raw


# The Pi's onboard UART Bluetooth chip occasionally drops out of BlueZ
# briefly ("No Bluetooth adapters found" / "Service Discovery has not been
# performed yet"), recovering on its own a couple seconds later -- same
# kind of momentary flakiness diag.py's BLE status check retries for.
# A few retries with a short pause avoid surfacing that as a hard failure.
_TRANSIENT_MARKERS = ("no bluetooth adapters found", "service discovery has not been performed")
_MAX_ATTEMPTS = 3


def _friendly_ble_error(msg):
    """'Device with address <mac> was not found.' (bleak, wenn der Dongle beim
    Verbinden nicht (mehr) sichtbar ist) klingt wie ein Programmfehler, ist
    hier aber praktisch immer: Dongle nicht im OBD-Port, oder Auto schläft
    (der Port hat dann keinen Strom, der Dongle sendet nichts) -- dieselbe
    Ursache wie beim "keine CAN-Frames empfangen"-Fall unten, nur schon beim
    Verbindungsaufbau statt erst beim Lesen."""
    if "was not found" in msg.lower():
        return "Dongle nicht gefunden -- steckt er im OBD-Port? Auto wach (Port hat nur dann Strom)?"
    return msg[:200]


def read(duration=5):
    """Verbindet sich mit dem Dongle, liest `duration` Sekunden CAN-Traffic,
    dekodiert bekannte Tesla-Frames, trennt wieder."""
    mac = (hubconf.getval("CANBUS_MAC") or DEFAULT_MAC).strip()
    if not mac:
        return {"ok": False, "error": "keine Dongle-Adresse konfiguriert"}
    duration = max(2, min(15, int(duration or 5)))
    with diag._ble_lock:
        for attempt in range(_MAX_ATTEMPTS):
            try:
                raw = asyncio.run(_read_async(mac, duration))
                break
            except Exception as e:
                msg = str(e)
                if attempt < _MAX_ATTEMPTS - 1 and any(m in msg.lower() for m in _TRANSIENT_MARKERS):
                    time.sleep(2)
                    continue
                return {"ok": False, "error": _friendly_ble_error(msg)}
    values, seen_ids, unknown = _parse_frames(raw)
    if not seen_ids:
        return {"ok": False, "error": "keine CAN-Frames empfangen (Dongle in Reichweite? Auto wach?)"}
    # Auf 80 begrenzen (typischerweise deutlich mehr unbekannte als bekannte
    # IDs pro Fenster) -- reicht zum Muster-Suchen, ohne die UI zu fluten.
    unknown_capped = dict(sorted(unknown.items())[:80])
    return {"ok": True, "values": values, "can_ids_seen": len(seen_ids), "mac": mac,
            "unknown_frames": unknown_capped, "unknown_count": len(unknown)}


# ---------------------------------------------------------------------------
# Kontinuierliche Überwachung: wiederholte kurze Lesezyklen in einem
# Hintergrund-Thread (Start/Stop über den UI-Schalter), statt einer einzigen
# Dauerverbindung -- damit der Pi's einzige Bluetooth-Adapter zwischen den
# Zyklen (_MONITOR_PAUSE_S) auch für tesla-control-Aufrufe (Wachhalten,
# Fahrzeugbefehle, MQTT-Sensoren) frei wird, statt komplett blockiert zu
# sein. Werte/unbekannte Frames werden über Zyklen hinweg gemerged (letzter
# gesehener Wert je Adresse bleibt stehen), damit "Status der Adressen" auch
# dann vollständig bleibt, wenn eine bestimmte ID nicht in jedem einzelnen
# 3-Sekunden-Fenster sendet.
_monitor_lock = threading.Lock()
_monitor_seen_ids = set()
_monitor_state = {"active": False, "values": {}, "unknown_frames": {}, "can_ids_seen": 0,
                   "cycles": 0, "last_update": None, "error": None}
_monitor_thread = None
_monitor_stop = threading.Event()
_MONITOR_CYCLE_S = 3
_MONITOR_PAUSE_S = 2


def _monitor_loop():
    mac = (hubconf.getval("CANBUS_MAC") or DEFAULT_MAC).strip()
    while not _monitor_stop.is_set():
        raw, err = None, None
        with diag._ble_lock:
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    raw = asyncio.run(_read_async(mac, _MONITOR_CYCLE_S))
                    break
                except Exception as e:
                    msg = str(e)
                    if attempt < _MAX_ATTEMPTS - 1 and any(m in msg.lower() for m in _TRANSIENT_MARKERS):
                        time.sleep(2)
                        continue
                    err = _friendly_ble_error(msg)
        with _monitor_lock:
            _monitor_state["cycles"] += 1
            _monitor_state["last_update"] = time.time()
            if raw is not None:
                values, seen_ids, unknown = _parse_frames(raw)
                if seen_ids:
                    _monitor_seen_ids.update(seen_ids)
                    _monitor_state["values"].update(values)
                    _monitor_state["unknown_frames"].update(unknown)
                    _monitor_state["can_ids_seen"] = len(_monitor_seen_ids)
                    _monitor_state["error"] = None
                else:
                    _monitor_state["error"] = "keine CAN-Frames empfangen (Auto wach? Dongle in Reichweite?)"
            else:
                _monitor_state["error"] = err or "Lesefehler"
        if _monitor_stop.wait(_MONITOR_PAUSE_S):
            break
    with _monitor_lock:
        _monitor_state["active"] = False


def start_monitor():
    global _monitor_thread
    if _monitor_thread and _monitor_thread.is_alive():
        return {"ok": True, "already": True}
    mac = (hubconf.getval("CANBUS_MAC") or DEFAULT_MAC).strip()
    if not mac:
        return {"ok": False, "error": "keine Dongle-Adresse konfiguriert"}
    with _monitor_lock:
        _monitor_state.update(active=True, error=None, cycles=0, last_update=None,
                               values={}, unknown_frames={}, can_ids_seen=0)
        _monitor_seen_ids.clear()
    _monitor_stop.clear()
    _monitor_thread = threading.Thread(target=_monitor_loop, daemon=True)
    _monitor_thread.start()
    return {"ok": True}


def stop_monitor():
    _monitor_stop.set()
    with _monitor_lock:
        _monitor_state["active"] = False
    return {"ok": True}


def monitor_status():
    with _monitor_lock:
        unknown_capped = dict(sorted(_monitor_state["unknown_frames"].items())[:80])
        return {"active": _monitor_state["active"], "values": dict(_monitor_state["values"]),
                "unknown_frames": unknown_capped, "unknown_count": len(_monitor_state["unknown_frames"]),
                "can_ids_seen": _monitor_state["can_ids_seen"], "cycles": _monitor_state["cycles"],
                "last_update": _monitor_state["last_update"], "error": _monitor_state["error"]}


# ---------------------------------------------------------------------------
# EXPERIMENTELL: Rohes Schreiben auf den Fahrzeug-CAN-Bus.
#
# Das ist bewusst etwas völlig anderes als diag.py's BLE-Befehle: dort läuft
# jeder Befehl über Teslas signierten Vehicle-Command-Kanal (tesla-control),
# das Auto prüft Rolle/Signatur und lehnt unerlaubte Befehle sauber mit
# INSUFFICIENT_PRIVILEGES ab. Hier gibt es keine Signatur, keine
# Rollenprüfung, keine Bestätigung durch das Auto -- der Frame wird einfach
# auf den Bus gelegt, den der OBD-Dongle ohnehin schon mitliest. Ob das
# Gateway ihn überhaupt durchlässt und ob das Zielsteuergerät ihn ohne den
# in echten Tesla-Frames üblichen Zähler/Prüfsumme-Bytes akzeptiert, ist
# NICHT verifiziert -- anders als KNOWN_IDS oben, das live gegengeprüft
# wurde. Deshalb: nur über write_action()/write_raw() erreichbar, beide
# verlangen confirm=True (server.py setzt das nur, wenn die UI-Bestätigung
# "Ich weiß, was ich tue" tatsächlich angeklickt wurde).
#
# Bit-Layout für glovebox_open aus der gemergten DBC (data/tesla_model_y_merged.dbc,
# Quelle: joshwardell/model3dbc): BO_ 947 ID3B3UI_vehicleControl2, 4 Byte,
# SG_ UI_gloveboxRequest : 0|1@1+ -> Byte0 Bit0. Alle anderen Bits dieser
# Botschaft (u. a. UI_alarmTriggerRequest, UI_autopilotPowerStateRequest) auf
# 0 gelassen ("keine Anfrage") statt sie zu raten.
RAW_ACTIONS = {
    "glovebox_open": ("Handschuhfach öffnen", 0x3B3, "01000000"),
}


def _hex_to_bytes(s):
    s = (s or "").strip().replace(" ", "")
    if not re.fullmatch(r"[0-9A-Fa-f]*", s) or len(s) % 2:
        raise ValueError("ungültige Hex-Daten")
    if len(s) // 2 > 8:
        raise ValueError("maximal 8 Datenbytes (klassischer CAN-Frame)")
    return bytes.fromhex(s)


async def _write_async(mac, can_id, data: bytes):
    buf = []

    def on_notify(_sender, chunk):
        buf.append(chunk.decode("utf-8", errors="replace"))

    async def send(client, cmd, wait=1.0):
        buf.clear()
        await client.write_gatt_char(WRITE_UUID, (cmd + "\r").encode(), response=False)
        await asyncio.sleep(wait)
        return "".join(buf).strip()

    async with BleakClient(mac, timeout=15.0) as client:
        await client.start_notify(NOTIFY_UUID, on_notify)
        await send(client, "ATZ", 2.0)
        await send(client, "ATE0")
        await send(client, "ATL0")
        await send(client, "ATSP6")               # CAN 11bit/500kbps
        await send(client, "ATCAF0")               # rohe Frames, kein ISO-TP
        await send(client, "ATR0")                 # keine Antwort erwarten -- reines Senden
        await send(client, f"ATSH{can_id:03X}")    # Sende-ID setzen
        resp = await send(client, data.hex().upper(), 1.0)
        await client.stop_notify(NOTIFY_UUID)
    return resp


def _write(can_id, data, confirm):
    if not confirm:
        return {"ok": False, "error": "Bestätigung fehlt (\"Ich weiß, was ich tue\" nicht angeklickt)"}
    if not (0 <= can_id <= 0x7FF):
        return {"ok": False, "error": "CAN-ID außerhalb 11-Bit-Bereich (0-7FF)"}
    mac = (hubconf.getval("CANBUS_MAC") or DEFAULT_MAC).strip()
    if not mac:
        return {"ok": False, "error": "keine Dongle-Adresse konfiguriert"}
    with diag._ble_lock:
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = asyncio.run(_write_async(mac, can_id, data))
                break
            except Exception as e:
                msg = str(e)
                if attempt < _MAX_ATTEMPTS - 1 and any(m in msg.lower() for m in _TRANSIENT_MARKERS):
                    time.sleep(2)
                    continue
                return {"ok": False, "error": _friendly_ble_error(msg)}
    return {"ok": True, "can_id": f"{can_id:03X}", "data": data.hex().upper(),
            "dongle_response": resp[:200]}


def write_action(action_id, confirm=False):
    """Sendet eine der kuratierten RAW_ACTIONS (z. B. glovebox_open)."""
    spec = RAW_ACTIONS.get(action_id)
    if not spec:
        return {"ok": False, "error": "unbekannte Aktion"}
    label, can_id, data_hex = spec
    r = _write(can_id, bytes.fromhex(data_hex), confirm)
    if r.get("ok"):
        r["label"] = label
    return r


def write_raw(can_id_hex, data_hex, confirm=False):
    """Sendet einen frei gewählten Frame (ID + bis zu 8 Datenbytes, je als Hex-Text)."""
    try:
        can_id = int((can_id_hex or "").strip(), 16)
    except ValueError:
        return {"ok": False, "error": "ungültige CAN-ID (Hex erwartet, z. B. 3B3)"}
    try:
        data = _hex_to_bytes(data_hex)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return _write(can_id, data, confirm)
