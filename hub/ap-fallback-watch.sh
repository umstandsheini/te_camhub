#!/bin/bash
# hub/ap-fallback-watch.sh -- run periodically by teslacam-ap-fallback.timer.
#
# No-op unless AP_FALLBACK_ONLY=true and an AP_SSID is configured. When
# active: brings TESLAUSB_AP up only while the WLAN client has no active
# connection (home WiFi out of range/down), and takes it back down as soon
# as the client reconnects -- so the AP is only ever on when actually
# needed, instead of teslausb's default always-on secondary AP.

CONF=/root/teslausb_setup_variables.conf
getval() {
  grep "^export $1=" "$CONF" 2>/dev/null | tail -1 | sed -E "s/^export $1=//; s/^'(.*)'\$/\1/"
}

[ "$(getval AP_FALLBACK_ONLY)" = "true" ] || exit 0
[ -n "$(getval AP_SSID)" ] || exit 0
nmcli -t -f NAME c show 2>/dev/null | grep -qx TESLAUSB_AP || exit 0

WLAN_ACTIVE="$(nmcli -t -f TYPE,DEVICE c show --active 2>/dev/null | grep 802-11-wireless | grep -v ':ap0$' || true)"
if [ -n "$WLAN_ACTIVE" ]; then
  nmcli con down TESLAUSB_AP >/dev/null 2>&1 || true
else
  nmcli con up TESLAUSB_AP >/dev/null 2>&1 || true
fi
