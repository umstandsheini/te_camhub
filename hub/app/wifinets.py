"""
Additional WiFi networks (phone hotspots etc.) besides the home WiFi.

Home WiFi stays SSID/WIFIPASS (setup-teslausb, synchold and osupdate key off
it) and keeps NetworkManager's default priority 0. Every extra network gets
its own keyfile profile TESLAUSB_WIFI_<n> with autoconnect-priority -10, -11,
... in list order, so home wins whenever it's in range, then the first extra
network that is. Written as keyfiles directly (see hub/ap-ensure.sh for why
not `nmcli con add/modify`) and picked up via `nmcli con reload`; written
from Python rather than a helper script so passwords never show up in a
process list.

The list lives in the conf as WIFI_NETWORKS (JSON; a SECRETS field, so it's
masked in /api/settings, included in the backup export and wiped by factory
reset). It supersedes the single HOTSPOT_SSID/HOTSPOT_PASS profile, which is
taken over as the first entry on the first change.
"""
import os, json, uuid, subprocess
import hubconf

NM_DIR = "/etc/NetworkManager/system-connections"
PREFIX = "TESLAUSB_WIFI_"
LEGACY = "TESLAUSB_HOTSPOT"
BASE_PRIORITY = -10
MAX_NETWORKS = 10


def _load():
    raw = hubconf.getval("WIFI_NETWORKS")
    if raw:
        try:
            return [{"ssid": str(n["ssid"]), "psk": str(n.get("psk") or "")}
                    for n in json.loads(raw) if n.get("ssid")]
        except (ValueError, TypeError, KeyError, AttributeError):
            return []
    if hubconf.getval("HOTSPOT_ENABLED") == "true" and hubconf.getval("HOTSPOT_SSID"):
        return [{"ssid": hubconf.getval("HOTSPOT_SSID"), "psk": hubconf.getval("HOTSPOT_PASS")}]
    return []


def _validate(ssid, psk):
    if not ssid or len(ssid.encode("utf-8")) > 32:
        raise ValueError("SSID missing or longer than 32 bytes")
    if any(c in ssid + psk for c in "\r\n\0"):
        raise ValueError("SSID/password contains invalid characters")
    hex64 = len(psk) == 64 and all(c in "0123456789abcdefABCDEF" for c in psk)
    if psk and not (8 <= len(psk) <= 63 or hex64):
        raise ValueError("Wi-Fi password must be 8–63 characters long (leave empty for an open network)")


def _esc(s):
    """GKeyFile string escaping: backslashes, and a leading space."""
    s = s.replace("\\", "\\\\")
    return "\\s" + s[1:] if s.startswith(" ") else s


def _profile_text(cid, uid, ssid, psk, prio):
    sec = "\n[wifi-security]\nkey-mgmt=wpa-psk\npsk=%s\n" % _esc(psk) if psk else ""
    return ("[connection]\nid=%s\nuuid=%s\ntype=wifi\nautoconnect=true\nautoconnect-priority=%d\n\n"
            "[wifi]\nmode=infrastructure\nssid=%s\n%s\n[ipv4]\nmethod=auto\n\n[ipv6]\nmethod=auto\n"
            % (cid, uid, prio, _esc(ssid), sec))


def _existing_uuid(path):
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("uuid="):
                    return line[5:].strip()
    except OSError:
        pass
    return str(uuid.uuid4())


def _apply(nets):
    subprocess.run(["mount", "/", "-o", "remount,rw"], capture_output=True)
    try:
        wanted = set()
        old_umask = os.umask(0o077)
        try:
            for i, n in enumerate(nets):
                cid = "%s%d" % (PREFIX, i + 1)
                path = os.path.join(NM_DIR, cid + ".nmconnection")
                wanted.add(path)
                with open(path + ".tmp", "w", encoding="utf-8") as f:
                    f.write(_profile_text(cid, _existing_uuid(path), n["ssid"], n["psk"], BASE_PRIORITY - i))
                os.chmod(path + ".tmp", 0o600)
                os.replace(path + ".tmp", path)
        finally:
            os.umask(old_umask)
        for fn in os.listdir(NM_DIR):
            p = os.path.join(NM_DIR, fn)
            if fn.endswith(".nmconnection") and (fn.startswith(PREFIX) or fn == LEGACY + ".nmconnection") \
                    and p not in wanted:
                os.remove(p)
        os.sync()
    finally:
        subprocess.run(["mount", "/", "-o", "remount,ro"], capture_output=True)
    subprocess.run(["nmcli", "con", "reload"], capture_output=True, timeout=30)
    # No single quotes in the stored JSON: hubconf.getval() strips the outer
    # quotes but doesn't undo the '\'' escaping write_settings() applies.
    data = json.dumps(nets, ensure_ascii=True).replace("'", "\\u0027")
    ok, err = hubconf.write_settings({"wifi_networks": data, "hotspot_enabled": "false"})
    if not ok:
        return {"ok": False, "error": err}
    return dict(status(), ok=True)


def _nmcli_lines(args):
    try:
        r = subprocess.run(["nmcli", "-t"] + args, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [l.replace("\\:", ":") for l in (r.stdout or "").splitlines() if l]


def status():
    nets = _load()
    try:
        cur = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        cur = ""
    visible = set(_nmcli_lines(["-f", "SSID", "dev", "wifi", "list", "--rescan", "no"]))
    home = hubconf.getval("SSID")
    return {"home": {"ssid": home, "connected": bool(home) and cur == home, "in_range": home in visible},
            "networks": [{"ssid": n["ssid"], "has_password": bool(n["psk"]), "connected": cur == n["ssid"],
                          "in_range": n["ssid"] in visible} for n in nets],
            "current": cur}


def add(ssid, psk):
    """Add a network, or change the password of one already in the list
    (an empty password then keeps the stored one)."""
    ssid, psk = str(ssid or "").strip(), str(psk or "")
    if ssid and ssid == hubconf.getval("SSID"):
        raise ValueError("This is the home Wi-Fi – set it above under \"Network\"")
    nets = _load()
    existing = next((n for n in nets if n["ssid"] == ssid), None)
    if existing and not psk:
        psk = existing["psk"]
    _validate(ssid, psk)
    if existing:
        existing["psk"] = psk
    elif len(nets) >= MAX_NETWORKS:
        raise ValueError("At most %d additional Wi-Fi networks" % MAX_NETWORKS)
    else:
        nets.append({"ssid": ssid, "psk": psk})
    return _apply(nets)


def remove(ssid):
    return _apply([n for n in _load() if n["ssid"] != ssid])


def move(ssid, delta):
    nets = _load()
    i = next((k for k, n in enumerate(nets) if n["ssid"] == ssid), None)
    if i is None:
        raise ValueError("Network not found")
    j = max(0, min(len(nets) - 1, i + (1 if int(delta) > 0 else -1)))
    nets[i], nets[j] = nets[j], nets[i]
    return _apply(nets)
