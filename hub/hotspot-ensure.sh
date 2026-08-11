#!/bin/bash -eu
# hub/hotspot-ensure.sh <ssid> <pass>
#
# Ensures a NetworkManager wifi-client profile for a phone hotspot, so the
# Hub can pick up connectivity away from home WiFi (e.g. service visit,
# parked somewhere without the home network in range) whenever the phone's
# hotspot is broadcasting. Mirrors ap-ensure.sh's approach: written as a
# keyfile directly under /etc/NetworkManager/system-connections/ and
# picked up via `nmcli con reload` -- see ap-ensure.sh's header comment for
# why (`nmcli con add`/`modify` don't reliably persist on this Pi's
# NetworkManager setup). Idempotent: safe to call repeatedly, always
# rewrites the whole keyfile rather than diffing/patching it.
#
# autoconnect-priority is set below NetworkManager's default (0), which is
# whatever priority the home WiFi profile has -- so home WiFi wins
# whenever both are in range, and the hotspot is a fallback for
# connectivity, not a replacement.

SSID="${1:?ssid required}"
PASS="${2:?password required}"

CONFFILE=/etc/NetworkManager/system-connections/TESLAUSB_HOTSPOT.nmconnection
if [ -f "$CONFFILE" ]; then
  UUID="$(grep -m1 '^uuid=' "$CONFFILE" | cut -d= -f2-)"
fi
UUID="${UUID:-$(cat /proc/sys/kernel/random/uuid)}"

umask 077
cat > "$CONFFILE" <<CONNEOF
[connection]
id=TESLAUSB_HOTSPOT
uuid=$UUID
type=wifi
autoconnect=true
autoconnect-priority=-10

[wifi]
mode=infrastructure
ssid=$SSID

[wifi-security]
key-mgmt=wpa-psk
psk=$PASS

[ipv4]
method=auto

[ipv6]
method=auto
CONNEOF
chown root:root "$CONFFILE"
chmod 600 "$CONFFILE"
nmcli con reload
echo "hotspot-ensure: TESLAUSB_HOTSPOT ready (ssid=$SSID)"
