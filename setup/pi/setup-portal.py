#!/usr/bin/env python3
"""
First-install WiFi setup without editing a file: a hotspot plus a one-page
form (teslacam-setup-portal.service, enabled by the image's firstrun.sh).

Runs on every boot until setup-teslausb has finished:
  - a config whose WiFi gets online within WAIT_ONLINE seconds: exit, and
    rc.local's setup carries on
  - otherwise (no config yet, or its WiFi doesn't connect -- a typo in the
    password): scan the WiFi networks, open the hotspot HOTSPOT_SSID on
    wlan0 with every DNS name pointing at the Pi (phones then pop the page up
    by themselves), serve the form on port 80, write
    teslausb_setup_variables.conf from it, apply it (apply-first-config.sh),
    close the hotspot and reboot.

Standard library only: nothing can be installed before there is WiFi. The
hotspot password is public (it's in the README), so anyone close by during
setup could join; filling in the config file before the first boot avoids
the hotspot entirely.
"""
import html, os, re, subprocess, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

HOTSPOT_SSID = "TeslaCam-Setup"
HOTSPOT_PASS = "teslacam-setup"
HOTSPOT_IP = "192.168.4.1"
PORT = 80
WAIT_ONLINE = 240
BOOT = "/boot/firmware"
SRC = "/root/te_camhub"
CONF = "/root/teslausb_setup_variables.conf"
TEMPLATES = [CONF, CONF + ".template", SRC + "/image/teslausb_setup_variables.conf"]
APPLY = SRC + "/setup/pi/apply-first-config.sh"
AP_ID = "TESLACAM_SETUP"
AP_FILE = "/etc/NetworkManager/system-connections/%s.nmconnection" % AP_ID
DNS_FILE = "/etc/NetworkManager/dnsmasq-shared.d/teslacam-setup.conf"
FINISHED = BOOT + "/TESLAUSB_SETUP_FINISHED"
LOG = BOOT + "/teslausb-headless-setup.log"
PLACEHOLDERS = {"DEIN_WLAN", "DEIN_WLAN_PASSWORT"}

# (variable, label, input type, advanced)
FIELDS = [
    ("SSID", "WLAN-Name (zu Hause)", "text", False),
    ("WIFIPASS", "WLAN-Passwort", "password", False),
    ("TESLAUSB_HOSTNAME", "Gerätename – später im Browser: https://<name>.local", "text", False),
    ("ARCHIVE_SERVER", "NAS: Name oder IP-Adresse (leer lassen = ohne NAS)", "text", False),
    ("SHARE_NAME", "NAS: Freigabe/Ordner, z. B. Video/TeslaCam", "text", False),
    ("SHARE_USER", "NAS: Benutzer", "text", False),
    ("SHARE_PASSWORD", "NAS: Passwort (leer lassen = unverändert)", "password", False),
    ("CAM_SIZE", "Größe des Dashcam-Laufwerks, z. B. 40G", "text", False),
    ("WIFI_COUNTRY", "WLAN-Land (Ländercode)", "text", True),
    ("TIME_ZONE", "Zeitzone", "text", True),
    ("MUSIC_SIZE", "Größe Musik-Laufwerk (0 = keins)", "text", True),
    ("LIGHTSHOW_SIZE", "Größe LightShow-Laufwerk (0 = keins)", "text", True),
    ("BOOMBOX_SIZE", "Größe Boombox-Laufwerk (0 = keins)", "text", True),
    ("SSH_PUBKEY", "SSH-Schlüssel für den Benutzer pi (optional)", "text", True),
]
SECRETS = {"WIFIPASS", "SHARE_PASSWORD"}
SIZE_RE = re.compile(r"\d+[KMG]?")


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s : setup-portal: %s\n" % (time.strftime("%a %d %b %H:%M:%S %Z %Y"), msg))
    except OSError:
        pass


def run(cmd, timeout=60):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


# ---- config file ------------------------------------------------------------
def read_conf(path):
    vals = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"\s*export\s+([A-Z_][A-Z0-9_]*)=(.*)$", line.rstrip("\r\n"))
                if m:
                    v = m.group(2).strip()
                    if len(v) >= 2 and v[0] in "'\"" and v[-1] == v[0]:
                        v = v[1:-1].replace("'\\''", "'")
                    vals[m.group(1)] = v
    except OSError:
        pass
    return vals


def template_text():
    for p in TEMPLATES:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                return f.read().replace("\r\n", "\n")
        except OSError:
            continue
    return ""


def shell_quote(v):
    return "'" + v.replace("'", "'\\''") + "'"


def render_conf(text, values):
    """Set each variable's first uncommented export line, append the rest."""
    lines = text.split("\n")
    for var, val in values.items():
        new = "export %s=%s" % (var, shell_quote(val))
        for i, line in enumerate(lines):
            if re.match(r"\s*export\s+%s=" % var, line):
                lines[i] = new
                break
        else:
            lines.append(new)
    return "\n".join(lines).rstrip("\n") + "\n"


def validate(form, current):
    """(values to write, error messages)"""
    g = lambda k: (form.get(k) or "").strip()
    errors, vals = [], {}
    for var, _, _, _ in FIELDS:
        if any(c in (form.get(var) or "") for c in "\r\n\0"):
            errors.append("Zeilenumbrüche sind nicht erlaubt")
            return {}, errors
    ssid = form.get("SSID") or ""
    if not ssid.strip() or len(ssid.encode("utf-8")) > 32:
        errors.append("WLAN-Name fehlt oder ist länger als 32 Zeichen")
    vals["SSID"] = ssid
    open_net = form.get("OPEN_WIFI") == "1"
    pw = form.get("WIFIPASS") or ""
    if open_net:
        pw = ""
    elif not pw and current.get("WIFIPASS") and current.get("WIFIPASS") not in PLACEHOLDERS \
            and current.get("SSID") == ssid:
        pw = current["WIFIPASS"]
    elif not 8 <= len(pw) <= 63:
        errors.append("WLAN-Passwort muss 8 bis 63 Zeichen lang sein (oder „Offenes WLAN“ ankreuzen)")
    vals["WIFIPASS"] = pw
    host = g("TESLAUSB_HOSTNAME") or "teslausb"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", host):
        errors.append("Gerätename: nur Buchstaben, Ziffern und Bindestrich")
    vals["TESLAUSB_HOSTNAME"] = host
    server = g("ARCHIVE_SERVER")
    if server:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", server):
            errors.append("NAS-Server: Name oder IP-Adresse ohne Leer- und Sonderzeichen")
        if not g("SHARE_NAME"):
            errors.append("NAS: Freigabe/Ordner fehlt")
        vals.update(ARCHIVE_SYSTEM="cifs", ARCHIVE_SERVER=server, SHARE_NAME=g("SHARE_NAME").strip("/"),
                    SHARE_USER=g("SHARE_USER"),
                    SHARE_PASSWORD=form.get("SHARE_PASSWORD") or current.get("SHARE_PASSWORD", ""))
    else:
        vals["ARCHIVE_SYSTEM"] = "none"
    for var in ("CAM_SIZE", "MUSIC_SIZE", "LIGHTSHOW_SIZE", "BOOMBOX_SIZE"):
        v = g(var).upper()
        if v:
            if not SIZE_RE.fullmatch(v):
                errors.append("%s: Größe wie 40G, 512M oder 0" % var)
            vals[var] = v
    if not vals.get("CAM_SIZE") and not current.get("CAM_SIZE"):
        errors.append("Größe des Dashcam-Laufwerks fehlt")
    country = g("WIFI_COUNTRY").upper()
    if country:
        if not re.fullmatch(r"[A-Z]{2}", country):
            errors.append("WLAN-Land: zwei Buchstaben, z. B. DE")
        vals["WIFI_COUNTRY"] = country
    tz = g("TIME_ZONE")
    if tz:
        if not re.fullmatch(r"[A-Za-z_]+(/[A-Za-z0-9_+-]+){0,2}", tz):
            errors.append("Zeitzone wie Europe/Berlin")
        vals["TIME_ZONE"] = tz
    key = g("SSH_PUBKEY")
    if key:
        if not re.fullmatch(r"(ssh-(ed25519|rsa)|ecdsa-sha2-nistp\d+|sk-\S+) [A-Za-z0-9+/=]+( .*)?", key):
            errors.append("SSH-Schlüssel: eine Zeile wie „ssh-ed25519 AAAA… name“")
        vals["SSH_PUBKEY"] = key
    return vals, errors


def write_conf(vals):
    run(["mount", "/", "-o", "remount,rw"])
    text = render_conf(template_text(), vals)
    tmp = CONF + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, CONF)
    for leftover in (CONF + ".template", BOOT + "/teslausb_setup_variables.conf"):
        try:
            os.remove(leftover)   # rc.local would move a boot-partition copy over this one
        except OSError:
            pass


# ---- network ----------------------------------------------------------------
def online():
    r = run(["nmcli", "-t", "networking", "connectivity", "check"], 30)
    return r.stdout.strip() == "full"


def wait_online(seconds):
    end = time.time() + seconds
    while time.time() < end:
        if online():
            return True
        time.sleep(5)
    return False


def scan():
    r = run(["nmcli", "-t", "-f", "SSID,SIGNAL", "device", "wifi", "list", "--rescan", "yes"], 45)
    best = {}
    for line in r.stdout.splitlines():
        m = re.match(r"^(.*):(\d+)$", line)
        if m:
            ssid = m.group(1).replace("\\:", ":").replace("\\\\", "\\")
            if ssid and ssid != HOTSPOT_SSID:
                best[ssid] = max(best.get(ssid, 0), int(m.group(2)))
    return sorted(best, key=lambda s: -best[s])


def start_hotspot():
    os.makedirs(os.path.dirname(DNS_FILE), exist_ok=True)
    with open(DNS_FILE, "w", encoding="utf-8") as f:
        f.write("address=/#/%s\n" % HOTSPOT_IP)
    fd = os.open(AP_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("[connection]\nid=%s\nuuid=%s\ntype=wifi\ninterface-name=wlan0\nautoconnect=false\n\n"
                "[wifi]\nmode=ap\nband=bg\nssid=%s\n\n"
                "[wifi-security]\nkey-mgmt=wpa-psk\nproto=rsn\npairwise=ccmp\ngroup=ccmp\npmf=1\npsk=%s\n\n"
                "[ipv4]\nmethod=shared\naddress1=%s/24\n\n[ipv6]\nmethod=disabled\n"
                % (AP_ID, uuid.uuid4(), HOTSPOT_SSID, HOTSPOT_PASS, HOTSPOT_IP))
    run(["nmcli", "connection", "reload"])
    r = run(["nmcli", "connection", "up", AP_ID], 60)
    log("Hotspot %s %s" % (HOTSPOT_SSID, "offen" if r.returncode == 0 else "fehlgeschlagen: " + r.stderr.strip()[:200]))
    return r.returncode == 0


def stop_hotspot():
    run(["nmcli", "connection", "down", AP_ID], 30)
    for p in (AP_FILE, DNS_FILE):
        try:
            os.remove(p)
        except OSError:
            pass
    run(["nmcli", "connection", "reload"])


# ---- web page ---------------------------------------------------------------
STYLE = """body{font:16px/1.45 system-ui,sans-serif;margin:0;background:#0e1116;color:#e7edf3}
main{max-width:520px;margin:0 auto;padding:20px 16px 40px}h1{font-size:22px}
label{display:block;margin:14px 0 4px;color:#93a1b0;font-size:14px}
input[type=text],input[type=password]{width:100%;box-sizing:border-box;padding:11px;border-radius:9px;
border:1px solid #2b3543;background:#161b22;color:#e7edf3;font:inherit}
.check{display:flex;gap:8px;align-items:center;margin-top:8px;color:#e7edf3}
button{margin-top:22px;width:100%;padding:13px;border:0;border-radius:10px;background:#e63946;color:#fff;font:inherit;font-weight:600}
.err{background:#3a1d22;border:1px solid #f85149;padding:10px;border-radius:9px}
.note{color:#93a1b0;font-size:14px}details{margin-top:18px}summary{color:#4aa3ff}"""


def page(body):
    return ("<!doctype html><html lang=de><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>TeslaCam Hub einrichten</title><style>%s</style></head><body><main>%s</main></body></html>"
            % (STYLE, body)).encode("utf-8")


def form_page(values, networks, errors=(), reason=None):
    e = html.escape
    out = ["<h1>TeslaCam Hub einrichten</h1>"]
    if reason:
        out.append("<p class=err>%s</p>" % e(reason))
    for msg in errors:
        out.append("<p class=err>%s</p>" % e(msg))
    out.append("<p class=note>Nach dem Speichern startet der Pi neu und richtet sich selbst ein "
               "(20–40 Minuten, mehrere Neustarts – bitte nicht vom Strom trennen).</p>")
    out.append("<form method=post action='/'>")
    out.append("<datalist id=nets>%s</datalist>" % "".join("<option value='%s'>" % e(n) for n in networks))
    advanced_open = False
    for var, label, typ, advanced in FIELDS:
        if advanced and not advanced_open:
            out.append("<details><summary>Weitere Einstellungen</summary>")
            advanced_open = True
        val = "" if var in SECRETS else values.get(var, "")
        if val in PLACEHOLDERS:
            val = ""
        extra = " list=nets autocomplete=off" if var == "SSID" else ""
        out.append("<label for=%s>%s</label><input type=%s id=%s name=%s value='%s'%s>"
                   % (var, e(label), typ, var, var, e(val), extra))
        if var == "WIFIPASS":
            out.append("<label class=check><input type=checkbox name=OPEN_WIFI value=1> Offenes WLAN (ohne Passwort)</label>")
    if advanced_open:
        out.append("</details>")
    out.append("<button type=submit>Speichern und einrichten</button></form>")
    return page("".join(out))


def done_page(host):
    return page("<h1>Gespeichert ✓</h1><p>Der Pi startet jetzt neu und richtet sich ein. Das dauert 20–40 Minuten "
                "mit mehreren Neustarts – bitte nicht vom Strom trennen.</p>"
                "<p>Danach mit dem Heim-WLAN verbinden und <b>https://%s.local</b> öffnen "
                "(oder die IP-Adresse des Pi aus dem Router). Dort das Tresor-Passwort festlegen.</p>"
                "<p class=note>Kommt der Pi mit den Angaben nicht ins WLAN, öffnet er nach ein paar Minuten "
                "wieder „%s“.</p>" % (html.escape(host), HOTSPOT_SSID))


class Portal:
    def __init__(self, networks, reason=None):
        self.networks = networks
        self.reason = reason
        self.finished = threading.Event()

    def handler(self):
        portal = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", headers=None):
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.split("?")[0] != "/":
                    # Captive-portal probes (generate_204, hotspot-detect.html, ...)
                    # and any other name: send phones to the form.
                    return self._send(302, headers={"Location": "http://%s/" % HOTSPOT_IP})
                self._send(200, form_page(read_conf(CONF) or read_conf(CONF + ".template"), portal.networks,
                                          reason=portal.reason), {"Content-Type": "text/html; charset=utf-8"})

            def do_POST(self):
                n = min(int(self.headers.get("Content-Length") or 0), 65536)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "replace"),
                                                      keep_blank_values=True).items()}
                current = read_conf(CONF) or read_conf(CONF + ".template")
                vals, errors = validate(form, current)
                if errors:
                    shown = dict(current)
                    shown.update({k: v for k, v in form.items() if k not in SECRETS})
                    return self._send(200, form_page(shown, portal.networks, errors),
                                      {"Content-Type": "text/html; charset=utf-8"})
                write_conf(vals)
                r = run(["bash", APPLY, CONF], 120)
                log("Konfiguration aus dem Formular gespeichert (WLAN %s), apply rc=%d" % (vals["SSID"], r.returncode))
                self._send(200, done_page(vals["TESLAUSB_HOSTNAME"]), {"Content-Type": "text/html; charset=utf-8"})
                portal.finished.set()

        return H


def main():
    if os.path.exists(FINISHED):
        return 0
    conf = read_conf(CONF)
    reason = None
    if conf.get("SSID") and conf["SSID"] not in PLACEHOLDERS:
        if wait_online(WAIT_ONLINE):
            return 0
        reason = "Keine Internetverbindung über das WLAN „%s“ – bitte Name und Passwort prüfen." % conf["SSID"]
        log("kein Internet über WLAN %s nach %d s – öffne %s" % (conf["SSID"], WAIT_ONLINE, HOTSPOT_SSID))
    networks = scan()
    if not start_hotspot():
        return 1
    portal = Portal(networks, reason)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), portal.handler())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    portal.finished.wait()
    time.sleep(5)   # let the confirmation page reach the phone
    server.shutdown()
    stop_hotspot()
    log("Neustart für die Einrichtung")
    run(["systemctl", "reboot"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
