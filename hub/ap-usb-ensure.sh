#!/bin/bash -eu
# hub/ap-usb-ensure.sh <ssid> <pass> [<ip>]
#
# Moves the TESLAUSB_AP hotspot from the onboard chip's ap0 virtual
# interface (a second role time-shared on the SAME radio as the wlan0
# home-WiFi connection -- the "gleichzeitiger AP+WLAN-Betrieb kann die
# WLAN-Verbindung kurz stören" limitation the AP-Fallback UI already
# warns about) onto a plugged-in USB WiFi adapter instead, as its own
# dedicated real device. No virtual interface, no chip contention: the
# USB radio does nothing but AP the whole time, wlan0 does nothing but
# home-WiFi the whole time.
#
# Detects the USB adapter by sysfs device path (contains "/usb/" or
# "/usbN/" for any USB-attached wifi interface, not just one specific
# chipset) rather than hardcoding an interface name -- wlan1 is not
# guaranteed to stay wlan1 across reboots/replugs, but "the wifi
# interface whose device path goes through USB" is stable as long as
# only one USB WiFi adapter is ever attached.
#
# Idempotent: safe to call repeatedly, e.g. every time the setting is
# saved. Same keyfile-write-then-`nmcli con reload` approach as
# ap-ensure.sh/hotspot-ensure.sh (see ap-ensure.sh's header for why
# `nmcli con add`/`modify` don't reliably persist on this Pi).
#
# wifi-security pins proto=rsn/pairwise=ccmp/group=ccmp (pure WPA2-PSK/AES)
# rather than leaving NetworkManager to pick defaults for a bare
# key-mgmt=wpa-psk: without those, NM advertises a WPA2/WPA3-transition AP
# (RSN offering SAE alongside PSK, TKIP alongside CCMP) -- confirmed via
# `iw scan` against this exact profile. Several real clients (a Tesla's
# onboard WiFi among them) fail to associate against that mixed mode and
# report it back to the user as a plain wrong-password error, even though
# the password matches byte for byte. proto/pairwise/group alone still
# left SAE in the advertised AKM suites (RSN capability, independent of
# the cipher settings) -- pmf=1 (disable Protected Management Frames,
# which SAE requires) is what actually drops SAE from the list, also
# confirmed via `iw scan`.

SSID="${1:?ssid required}"
PASS="${2:?password required}"
IP="${3:-192.168.66.1}"

USB_WLAN=""
for dev in /sys/class/net/wlan*; do
  [ -e "$dev" ] || continue
  ifname="$(basename "$dev")"
  devpath="$(readlink -f "$dev/device" 2>/dev/null || true)"
  case "$devpath" in
    */usb*/*) USB_WLAN="$ifname"; break ;;
  esac
done

if [ -z "$USB_WLAN" ]; then
  echo "ap-usb-ensure: no USB WiFi adapter found" >&2
  exit 1
fi

# Tear down the onboard chip's ap0 role and stop the core teslausb
# if-up.d hook from recreating it on the next wlan0 up-event -- both
# would otherwise keep fighting for the AP role alongside this one.
if iw dev ap0 info &> /dev/null; then
  nmcli con down TESLAUSB_AP &> /dev/null || true
  iw dev ap0 del &> /dev/null || true
fi
if [ -f /etc/network/if-up.d/teslausb-ap ]; then
  rm -f /etc/network/if-up.d/teslausb-ap
fi

iw "$USB_WLAN" set power_save off || true

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
interface-name=$USB_WLAN
autoconnect=true

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
nmcli con up TESLAUSB_AP || true
echo "ap-usb-ensure: TESLAUSB_AP moved to USB adapter $USB_WLAN (ssid=$SSID ip=$IP)"
