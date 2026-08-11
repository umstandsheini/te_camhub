"""
Config access for the TeslaCam Hub: read/write the teslausb setup variables
file safely (allowlist + shell-safe single-quote escaping), plus a NAS/CIFS
connection test. Runs as root (systemd), so it can remount / rw for writes.
"""
import os, re, subprocess, tempfile

CONF = "/root/teslausb_setup_variables.conf"

# Fields the UI may edit -> conf variable name. Anything not here is ignored,
# so the browser can never inject an arbitrary variable/line.
FIELD_MAP = {
    # NAS / archive
    "archive_server": "ARCHIVE_SERVER",
    "share_name": "SHARE_NAME",
    "share_user": "SHARE_USER",
    "share_password": "SHARE_PASSWORD",
    "cifs_version": "CIFS_VERSION",
    "archive_recentclips": "ARCHIVE_RECENTCLIPS",
    "archive_savedclips": "ARCHIVE_SAVEDCLIPS",
    "archive_sentryclips": "ARCHIVE_SENTRYCLIPS",
    "archive_trackmodeclips": "ARCHIVE_TRACKMODECLIPS",
    # network
    "ssid": "SSID",
    "wifipass": "WIFIPASS",
    "ap_ssid": "AP_SSID",
    "ap_pass": "AP_PASS",
    "ap_ip": "AP_IP",
    "ap_fallback_only": "AP_FALLBACK_ONLY",
    "hotspot_ssid": "HOTSPOT_SSID",
    "hotspot_pass": "HOTSPOT_PASS",
    "hotspot_enabled": "HOTSPOT_ENABLED",
    "wg_enabled": "WG_ENABLED",
    "wg_peer_pubkey": "WG_PEER_PUBKEY",
    "wg_endpoint": "WG_ENDPOINT",
    "wg_allowed_ips": "WG_ALLOWED_IPS",
    "wg_address": "WG_ADDRESS",
    "wg_keepalive": "WG_KEEPALIVE",
    "wg_psk": "WG_PSK",
    "wg_privkey": "WG_PRIVKEY",
    "wg_dns": "WG_DNS",
    # keep-awake
    "teslafi_api_token": "TESLAFI_API_TOKEN",
    "tessie_api_token": "TESSIE_API_TOKEN",
    "tessie_vin": "TESSIE_VIN",
    "tesla_ble_vin": "TESLA_BLE_VIN",
    "canbus_mac": "CANBUS_MAC",
    # notifications (a common subset)
    "pushover_enabled": "PUSHOVER_ENABLED",
    "pushover_user_key": "PUSHOVER_USER_KEY",
    "pushover_app_key": "PUSHOVER_APP_KEY",
    "telegram_enabled": "TELEGRAM_ENABLED",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    # Home Assistant (MQTT)
    "mqtt_enabled": "MQTT_ENABLED",
    "mqtt_host": "MQTT_HOST",
    "mqtt_port": "MQTT_PORT",
    "mqtt_user": "MQTT_USER",
    "mqtt_password": "MQTT_PASSWORD",
    # system
    "time_zone": "TIME_ZONE",
    "teslausb_hostname": "TESLAUSB_HOSTNAME",
    "snapshot_interval": "SNAPSHOT_INTERVAL",
    "archive_delay": "ARCHIVE_DELAY",
    # hub features
    "sync_all_content": "SYNC_ALL_CONTENT",
    "sync_media_path": "SYNC_MEDIA_PATH",
    "nas_raw_keys": "NAS_RAW_KEYS",
    "blackbox_enabled": "BLACKBOX_ENABLED",
    "sync_trips_enabled": "SYNC_TRIPS_ENABLED",
    "retention_mode": "RETENTION_MODE",
    "retention_days": "RETENTION_DAYS",
    "retention_free_gb": "RETENTION_FREE_GB",
    "vault_autolock_min": "VAULT_AUTOLOCK_MIN",
    "ssh_disable_password": "SSH_DISABLE_PASSWORD",
    "viewer_extra_roots": "VIEWER_EXTRA_ROOTS",
    "samba_enabled": "SAMBA_ENABLED",
}
BOOLS = {"archive_recentclips", "archive_savedclips", "archive_sentryclips",
         "archive_trackmodeclips", "pushover_enabled", "telegram_enabled", "ap_fallback_only",
         "sync_all_content", "ssh_disable_password", "mqtt_enabled", "nas_raw_keys", "blackbox_enabled",
         "sync_trips_enabled", "samba_enabled", "hotspot_enabled", "wg_enabled"}
INTS = {"snapshot_interval", "archive_delay", "retention_days",
        "retention_free_gb", "vault_autolock_min", "mqtt_port", "wg_keepalive"}
SECRETS = {"share_password", "wifipass", "ap_pass", "teslafi_api_token",
           "tessie_api_token", "pushover_user_key", "pushover_app_key",
           "telegram_bot_token", "mqtt_password", "hotspot_pass",
           "wg_psk", "wg_privkey"}  # returned only as *_set, never in clear


def getval(name):
    try:
        with open(CONF, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("export " + name + "="):
                    v = line[len("export " + name + "="):].strip()
                    if len(v) >= 2 and v[0] in "'\"" and v[-1] == v[0]:
                        v = v[1:-1]
                    return v
    except Exception:
        pass
    return ""


def read_settings():
    """Return the full editable config as a dict; secrets masked to *_set."""
    out = {}
    for field, var in FIELD_MAP.items():
        if field in SECRETS:
            out[field + "_set"] = _isset(var)
        else:
            out[field] = getval(var)
    if not out.get("sync_media_path"):
        share = (out.get("share_name") or "").split("/")[0]
        if share:
            out["sync_media_path"] = share + "/Sonstiges"
    if not out.get("vault_autolock_min"):
        out["vault_autolock_min"] = "180"
    if not out.get("sync_trips_enabled"):
        out["sync_trips_enabled"] = "true"   # opt-out, not opt-in -- matches nas_sync_loop's default
    return out


def _isset(var):
    try:
        with open(CONF, encoding="utf-8", errors="replace") as f:
            return any(l.startswith("export " + var + "=") for l in f)
    except Exception:
        return False


def write_settings(values: dict):
    """Apply an incoming {field: value} dict. Returns (ok, error)."""
    updates = []
    for field, raw in values.items():
        if field not in FIELD_MAP:
            continue
        var = FIELD_MAP[field]
        if field in SECRETS and (raw is None or raw == ""):
            continue  # blank secret => leave unchanged
        val = "" if raw is None else str(raw)
        if field in BOOLS:
            val = "true" if val in ("true", "1", "on", "True") else "false"
        elif field in INTS:
            if val and not re.fullmatch(r"\d+", val):
                return False, f"{field} muss eine Zahl sein"
        elif field == "retention_mode":
            if val not in ("off", "time", "space", ""):
                return False, "retention_mode ungültig"
        esc = "'" + val.replace("'", "'\\''") + "'"
        updates.append((var, "export %s=%s" % (var, esc)))
    if not updates:
        return True, None

    subprocess.run(["mount", "/", "-o", "remount,rw"], capture_output=True)
    try:
        with open(CONF, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        for var, newline in updates:
            found = False
            for i, l in enumerate(lines):
                if l.startswith("export " + var + "="):
                    lines[i] = newline + "\n"; found = True; break
            if not found:
                lines.append(newline + "\n")
        tmp = CONF + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.replace(tmp, CONF)
    finally:
        subprocess.run(["mount", "/", "-o", "remount,ro"], capture_output=True)
    return True, None


def import_conf(text):
    """Restore /root/teslausb_setup_variables.conf verbatim from a prior
    export (GET /api/backup/export) -- the safety net for migrations like
    reflashing the stick with a bigger root partition: export before,
    import after, instead of retyping every field by hand, including
    secrets the regular Settings UI deliberately never echoes back
    (SECRETS are masked to *_set in read_settings()). Minimal sanity
    check only -- this is a trusted admin action, not attacker input --
    just enough to stop an obviously-wrong file from wiping the config."""
    if not text or "export " not in text:
        return False, "Datei sieht nicht wie eine gültige teslausb-Konfiguration aus"
    subprocess.run(["mount", "/", "-o", "remount,rw"], capture_output=True)
    try:
        tmp = CONF + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, CONF)
    finally:
        subprocess.run(["mount", "/", "-o", "remount,ro"], capture_output=True)
    return True, None


def clear_secrets():
    """Forgot-password recovery: remove every SECRETS field's line from the
    conf file (NAS/WiFi/AP passwords, API tokens, bot/MQTT credentials).
    Called alongside Vault.factory_reset() -- a stick pulled after reset must
    not still hand out these credentials in clear text."""
    subprocess.run(["mount", "/", "-o", "remount,rw"], capture_output=True)
    try:
        with open(CONF, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        secret_vars = {FIELD_MAP[field] for field in SECRETS}
        lines = [l for l in lines
                 if not any(l.startswith("export " + var + "=") for var in secret_vars)]
        tmp = CONF + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.replace(tmp, CONF)
    finally:
        subprocess.run(["mount", "/", "-o", "remount,ro"], capture_output=True)


def test_nas(server=None, share=None, user=None, password=None):
    """Try a temporary CIFS mount; empty args fall back to stored conf."""
    server = server or getval("ARCHIVE_SERVER")
    share = share or getval("SHARE_NAME")
    user = user or getval("SHARE_USER")
    password = password or getval("SHARE_PASSWORD")
    vers = getval("CIFS_VERSION") or "3.0"
    if not server or not share:
        return {"ok": False, "error": "Server/Share fehlt"}
    mnt = "/tmp/hub_nastest"
    os.makedirs(mnt, exist_ok=True)
    creds = tempfile.NamedTemporaryFile("w", delete=False)
    creds.write("username=%s\npassword=%s\n" % (user, password)); creds.close()
    os.chmod(creds.name, 0o600)
    writable = False
    try:
        r = subprocess.run(["mount", "-t", "cifs", "//%s/%s" % (server, share), mnt,
                            "-o", "credentials=%s,vers=%s,iocharset=utf8,rw" % (creds.name, vers)],
                           capture_output=True, text=True, timeout=25)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or "Mount fehlgeschlagen").splitlines()[-1][:200]}
        try:
            probe = os.path.join(mnt, ".hub_write_test")
            open(probe, "w").close(); os.remove(probe); writable = True
        except Exception:
            writable = False
        return {"ok": True, "writable": writable}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    finally:
        subprocess.run(["umount", mnt], capture_output=True)
        try: os.remove(creds.name)
        except OSError: pass
