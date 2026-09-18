#!/bin/bash
# hub/wifi-watch.sh -- run every minute by teslacam-wifi-watch.timer.
#
# One watcher for the Pi's WiFi, replacing home-wifi-watch.sh and
# ap-fallback-watch.sh, which solved half the problem each and got in each
# other's way. Two failures it exists for, both seen in the car:
#
#   - NetworkManager picks a network by priority only while it is choosing
#     one. Once associated with a phone hotspot it stays there even when the
#     home WiFi is back in range (2026-09-18: the Pi was online through a
#     phone but invisible at home -- no SSH, no MQTT, no NAS sync).
#   - Without a USB WiFi adapter the Pi's own access point (TESLAUSB_AP on
#     ap0) shares the one radio with the client, which pins the client to the
#     AP's channel. A phone hotspot on another channel is then hard or
#     impossible to join -- so the AP has to go down before scanning and
#     connecting.
#
# Every run:
#   1. known networks, best first: the home WiFi (SSID), then the list from
#      Einstellungen (TESLAUSB_WIFI_1, _2, ... in order)
#   2. connected to the best one that is in range -> nothing to do, AP down
#   3. connected to a worse one -> switch (this is the "back home" case, and
#      equally "better hotspot in range")
#   4. connected to nothing -> AP down (frees the radio), scan, connect to the
#      best known network in range
#   5. nothing found AP_GRACE runs in a row -> bring the AP up as the way in
#      when there is no known WiFi at all
#
# The home WiFi also counts as "in range" when the home zone says the car is
# parked at home (HOME_LAT/HOME_LON/HOME_RADIUS_M, learned by the Hub) or,
# with no zone known yet, when its profile is a hidden network -- a hidden
# SSID can never show up in a scan.
#
# AP_FALLBACK_ONLY=false keeps the AP up permanently (only sensible with a
# USB WiFi adapter, i.e. a second radio). HOME_WIFI_PREFER=false switches the
# whole watcher off.

CONF=${WIFI_WATCH_CONF:-/root/teslausb_setup_variables.conf}
STATE_DIR=${WIFI_WATCH_STATE_DIR:-/run}
LOG=${WIFI_WATCH_LOG:-/mutable/home-wifi.log}
LOCATION=${WIFI_WATCH_LOCATION:-/backingfiles/decrypt-viewer-state/last_location.json}
RETRY_STATE=$STATE_DIR/teslacam-wifi-try
MISS_STATE=$STATE_DIR/teslacam-wifi-misses
RETRY_SEC=${WIFI_WATCH_RETRY_SEC:-600}
MIN_SIGNAL=${WIFI_WATCH_MIN_SIGNAL:-20}
LOCATION_MAX_AGE=${WIFI_WATCH_LOCATION_MAX_AGE:-172800}   # 48 h
SCAN_WAIT=${WIFI_WATCH_SCAN_WAIT:-5}
AP_GRACE=${WIFI_WATCH_AP_GRACE:-3}                        # runs without any known WiFi before the AP goes up
AP_PROFILE=${WIFI_WATCH_AP_PROFILE:-TESLAUSB_AP}

getval() {
  grep "^export $1=" "$CONF" 2>/dev/null | tail -1 | sed -E "s/^export $1=//; s/^'(.*)'\$/\1/"
}

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG" 2>/dev/null
  echo "$1"
}

ap_up() {
  nmcli -t -f NAME c show --active 2>/dev/null | grep -qx "$AP_PROFILE"
}

ap_down() {
  ap_up || return 0
  log "Access Point aus (das Funkgerät wird zum Suchen/Verbinden gebraucht)"
  nmcli con down "$AP_PROFILE" > /dev/null 2>&1
  sleep 2
}

HOME_SSID=$(getval SSID)
[ "$(getval HOME_WIFI_PREFER)" = "false" ] && exit 0
AP_CONFIGURED=no
nmcli -t -f NAME c show 2>/dev/null | grep -qx "$AP_PROFILE" && AP_CONFIGURED=yes
AP_ALWAYS=no
[ "$AP_CONFIGURED" = yes ] && [ "$(getval AP_FALLBACK_ONLY)" = "false" ] && AP_ALWAYS=yes

# ---- known networks, best first ------------------------------------------
# profile<TAB>ssid; the home WiFi wins, then the list order from Einstellungen.
KNOWN=""
add_known() {   # add_known <ssid>
  [ -n "$1" ] || return 0
  local name ssid
  while IFS= read -r name
  do
    ssid=$(nmcli -g 802-11-wireless.ssid c show "$name" 2>/dev/null)
    if [ "$ssid" = "$1" ]
    then
      KNOWN="$KNOWN$name	$ssid
"
      return 0
    fi
  done < <(nmcli -t -f NAME,TYPE c show 2>/dev/null | awk -F: '$2 ~ /wireless/ && $1 != "'"$AP_PROFILE"'" { print $1 }')
  return 0
}

add_known "$HOME_SSID"
for i in $(seq 1 10)
do
  ssid=$(nmcli -g 802-11-wireless.ssid c show "TESLAUSB_WIFI_$i" 2>/dev/null)
  [ -n "$ssid" ] && KNOWN="$KNOWN"$'TESLAUSB_WIFI_'"$i	$ssid
"
done
[ -n "$KNOWN" ] || exit 0

rank_of() {   # rank_of <ssid> -> position in KNOWN, empty if unknown
  echo "$KNOWN" | awk -F'\t' -v s="$1" 'NF && $2 == s { print NR; exit }'
}

# ---- what are we on right now? -------------------------------------------
CURRENT=$(nmcli -t -f TYPE,DEVICE,NAME c show --active 2>/dev/null \
          | awk -F: '$1 ~ /wireless/ && $2 != "ap0" { print $3 }' | head -1)
CURRENT_SSID=$(iwgetid -r 2>/dev/null)
if [ -z "$CURRENT_SSID" ] && [ -n "$CURRENT" ]
then
  # iwgetid comes up empty while the card is (re)associating -- the active
  # profile still says which network this is, so don't treat it as "unknown"
  # and start connecting somewhere.
  CURRENT_SSID=$(nmcli -g 802-11-wireless.ssid c show "$CURRENT" 2>/dev/null)
fi
CURRENT_RANK=$(rank_of "$CURRENT_SSID")

if [ -n "$CURRENT_SSID" ] && [ "$CURRENT_RANK" = 1 ]
then
  rm -f "$RETRY_STATE" "$MISS_STATE"
  [ "$AP_ALWAYS" = yes ] || ap_down
  exit 0
fi

now=$(date +%s)
last=$(cat "$RETRY_STATE" 2>/dev/null || echo 0)
[ $((now - last)) -lt "$RETRY_SEC" ] && [ -n "$CURRENT" ] && exit 0

# ---- which known networks are in range? ----------------------------------
[ "$AP_ALWAYS" = yes ] || ap_down       # the client can't scan other channels while the AP holds the radio
nmcli device wifi rescan > /dev/null 2>&1
sleep "$SCAN_WAIT"
SCAN=$(nmcli -t -f SSID,SIGNAL device wifi list 2>/dev/null)

in_range() {   # in_range <ssid>
  echo "$SCAN" | awk -F: -v s="$1" -v m="$MIN_SIGNAL" '$1 == s && $2 + 0 >= m { found = 1 } END { exit !found }'
}

home_zone_says_home() {
  local lat lon radius dist
  lat=$(getval HOME_LAT); lon=$(getval HOME_LON)
  radius=$(getval HOME_RADIUS_M); radius=${radius:-150}
  [ -n "$lat" ] && [ -n "$lon" ] && [ -r "$LOCATION" ] || return 1
  dist=$(python3 - "$LOCATION" "$lat" "$lon" "$LOCATION_MAX_AGE" <<'PY' 2>/dev/null
import json, math, sys, time
path, lat, lon, max_age = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
try:
    with open(path, encoding="utf-8") as f:
        p = json.load(f)
    if time.time() - float(p["ts"]) > max_age:
        raise ValueError("too old")
    r, a, b = 6371000.0, math.radians(lat), math.radians(float(p["lat"]))
    d_lat, d_lon = math.radians(float(p["lat"]) - lat), math.radians(float(p["lon"]) - lon)
    h = math.sin(d_lat / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(d_lon / 2) ** 2
    print(int(2 * r * math.asin(math.sqrt(h))))
except Exception:
    pass
PY
)
  [ -n "$dist" ] && [ "$dist" -le "$radius" ]
}

target_profile=""
target_ssid=""
target_why=""
rank=0
while IFS=$'\t' read -r name ssid
do
  [ -n "$name" ] || continue
  rank=$((rank + 1))
  [ -n "$CURRENT_RANK" ] && [ "$rank" -ge "$CURRENT_RANK" ] && break   # nothing better than what we have
  why=""
  if in_range "$ssid"
  then
    why="in Reichweite"
  elif [ "$ssid" = "$HOME_SSID" ]
  then
    if home_zone_says_home
    then
      why="Auto steht zu Hause"
    elif [ "$(nmcli -g 802-11-wireless.hidden c show "$name" 2>/dev/null)" = "yes" ] \
         && [ -z "$(getval HOME_LAT)$(getval HOME_LON)" ]
    then
      why="verstecktes Heim-WLAN"
    fi
  fi
  if [ -n "$why" ]
  then
    target_profile=$name
    target_ssid=$ssid
    target_why=$why
    break
  fi
done <<< "$KNOWN"

if [ -z "$target_profile" ]
then
  # Nothing known in range. Keep the current connection if there is one;
  # otherwise put the AP up after a few empty runs so there is a way in.
  if [ -n "$CURRENT" ]
  then
    rm -f "$MISS_STATE"
    exit 0
  fi
  misses=$(( $(cat "$MISS_STATE" 2>/dev/null || echo 0) + 1 ))
  echo "$misses" > "$MISS_STATE"
  if [ "$AP_CONFIGURED" = yes ] && [ "$misses" -ge "$AP_GRACE" ] && ! ap_up
  then
    log "kein bekanntes WLAN seit $misses Versuchen -- Access Point an"
    nmcli con up "$AP_PROFILE" > /dev/null 2>&1
  fi
  exit 0
fi

rm -f "$MISS_STATE"
echo "$now" > "$RETRY_STATE"
log "Wechsel von '${CURRENT_SSID:-nichts}' zu '$target_ssid': $target_why"
if nmcli con up "$target_profile" > /dev/null 2>&1
then
  log "verbunden mit $target_ssid"
  rm -f "$RETRY_STATE"
  exit 0
fi
if [ "$target_ssid" = "$HOME_SSID" ]
then
  pass=$(getval WIFIPASS)
  if [ -n "$pass" ] && nmcli device wifi connect "$target_ssid" password "$pass" hidden yes > /dev/null 2>&1
  then
    log "verbunden mit $target_ssid"
    rm -f "$RETRY_STATE"
    exit 0
  fi
fi
log "Verbinden mit '$target_ssid' fehlgeschlagen, naechster Versuch in $((RETRY_SEC / 60)) Minuten"
exit 0
