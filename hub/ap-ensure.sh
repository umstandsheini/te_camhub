#!/bin/bash -eu
# hub/ap-ensure.sh <ssid> <pass> [<ip>]
#
# Ensures the ap0 virtual interface + TESLAUSB_AP NetworkManager profile
# exist, mirroring teslausb's own setup/pi/configure-ap.sh (NetworkManager
# path -- this Pi doesn't use the legacy hostapd/wpa_supplicant path).
# Idempotent: safe to call repeatedly, e.g. every time the setting is saved
# -- always rewrites the whole keyfile rather than diffing/patching it.
#
# The profile is written directly as a keyfile under
# /etc/NetworkManager/system-connections/ and picked up via `nmcli con
# reload`, never via `nmcli con add`/`nmcli con modify`. Two independent
# reasons, both confirmed empirically against this Pi:
#   1. Raspberry Pi OS's NetworkManager.conf ships `plugins=ifupdown,keyfile`;
#      under that combination `nmcli con add`'s AddConnection D-Bus call
#      fails with "settings plugin does not support adding connections".
#   2. NetworkManager.service runs with ProtectSystem=true, so *any* write
#      NetworkManager's own daemon process makes under /etc -- including
#      what `nmcli con modify` asks it to do -- hits a read-only mount from
#      that process's point of view, regardless of the real filesystem's
#      rw/ro state. Only a write from a process outside that sandbox (this
#      script, running as root via the Hub) can actually land on disk;
#      `nmcli con reload` afterwards is a read, which NM's sandbox permits.
# autoconnect=false is baked into the template itself so the Hub never needs
# a separate `nmcli con modify ... autoconnect` call to (un)set it.
#
# wifi-security pins proto=rsn/pairwise=ccmp/group=ccmp (pure WPA2-PSK/AES)
# -- a bare key-mgmt=wpa-psk lets NetworkManager default to a WPA2/WPA3
# transition AP (RSN offering SAE alongside PSK, TKIP alongside CCMP),
# which several real clients -- a Tesla's onboard WiFi among them -- fail
# to associate against, surfacing as a plain wrong-password error to the
# user even with a byte-for-byte correct password. pmf=1 (disable
# Protected Management Frames, which SAE requires) is what actually drops
# SAE from the advertised AKM suites -- proto/pairwise/group alone don't.
# See ap-usb-ensure.sh, where this was diagnosed via `iw scan`.

SSID="${1:?ssid required}"
PASS="${2:?password required}"
IP="${3:-192.168.66.1}"

WLAN="$(nmcli -t -f TYPE,DEVICE c show --active | grep 802-11-wireless | grep -v ':ap0$' | cut -d: -f2 | head -1)"
if [ -z "$WLAN" ]; then
  WLAN="$(nmcli -t -f TYPE,DEVICE d | grep '^wifi:' | grep -v ':ap0$' | cut -d: -f2 | head -1)"
fi
if [ -z "$WLAN" ]; then
  echo "ap-ensure: no wifi client device found" >&2
  exit 1
fi

if ! iw dev ap0 info &> /dev/null; then
  iw dev "$WLAN" interface add ap0 type __ap || true
fi
iw "$WLAN" set power_save off || true
iw ap0 set power_save off || true

CONFFILE=/etc/NetworkManager/system-connections/TESLAUSB_AP.nmconnection
if [ -f "$CONFFILE" ]; then
  UUID="$(grep -m1 '^uuid=' "$CONFFILE" | cut -d= -f2-)"
fi
UUID="${UUID:-$(cat /proc/sys/kernel/random/uuid)}"

umask 077
cat > "$CONFFILE" <<CONNEOF
[connection]
id=TESLAUSB_AP
uuid=$UUID
type=wifi
interface-name=ap0
autoconnect=false

[wifi]
mode=ap
ssid=$SSID

[wifi-security]
key-mgmt=wpa-psk
proto=rsn
pairwise=ccmp
group=ccmp
pmf=1
psk=$PASS

[ipv4]
address1=$IP/24
method=shared

[ipv6]
method=disabled
CONNEOF
chown root:root "$CONFFILE"
chmod 600 "$CONFFILE"
nmcli con reload
echo "ap-ensure: TESLAUSB_AP ready (ssid=$SSID ip=$IP if=$WLAN)"
