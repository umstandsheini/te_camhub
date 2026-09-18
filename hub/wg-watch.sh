#!/bin/bash
# hub/wg-watch.sh -- run every 2 minutes by teslacam-wg-watch.timer.
#
# Takes a dead WireGuard tunnel out of the way. With WG_ALLOWED_IPS =
# 0.0.0.0/0,::/0 (the default for "VPN nach Hause") wg-quick routes *all*
# internet traffic into the tunnel and sets the peer's DNS server. If the
# peer never answers, the Pi is left with:
#   - no name resolution (the tunnel's DNS is unreachable through it)
#   - no internet at all, while the local network still works, so nothing
#     looks broken from the outside
# Seen on 2026-09-18: no handshake ever, 177 KB sent, 0 received -- the Hub
# could not reach GitHub for update checks or Tesla for keys, and the stale
# resolver entry survived the tunnel itself.
#
# So: no handshake within HANDSHAKE_MAX seconds on a full-tunnel setup ->
# stop wg-quick and clean up its resolver record, then retry every
# RETRY_SEC. A split tunnel (no default route through wg0) is left alone: a
# dead one costs nothing there. WG_ENABLED=false means this does nothing;
# the tunnel is then not supposed to run at all.

CONF=${WG_WATCH_CONF:-/root/teslausb_setup_variables.conf}
STATE=${WG_WATCH_STATE:-/run/teslacam-wg-retry}
LOG=${WG_WATCH_LOG:-/mutable/wg-watch.log}
IFACE=${WG_WATCH_IFACE:-wg0}
UNIT=${WG_WATCH_UNIT:-wg-quick@wg0}
HANDSHAKE_MAX=${WG_WATCH_HANDSHAKE_MAX:-180}
RETRY_SEC=${WG_WATCH_RETRY_SEC:-1800}
GRACE_SEC=${WG_WATCH_GRACE_SEC:-60}
SYSTEMCTL=${WG_WATCH_SYSTEMCTL:-systemctl}
WG=${WG_WATCH_WG:-wg}
RESOLVCONF=${WG_WATCH_RESOLVCONF:-resolvconf}

getval() {
  grep "^export $1=" "$CONF" 2>/dev/null | tail -1 | sed -E "s/^export $1=//; s/^'(.*)'\$/\1/"
}

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG" 2>/dev/null
  echo "$1"
}

newest_handshake() {   # seconds since the newest handshake, or "never"
  local now newest ts
  now=$(date +%s)
  newest=0
  while read -r _peer ts
  do
    [ -n "${ts:-}" ] || continue
    [ "$ts" -gt "$newest" ] && newest=$ts
  done < <($WG show "$IFACE" latest-handshakes 2>/dev/null)
  [ "$newest" = 0 ] && { echo never; return; }
  echo $((now - newest))
}

full_tunnel() {
  $WG show "$IFACE" allowed-ips 2>/dev/null | grep -qE '(^|[[:space:],])0\.0\.0\.0/0|(^|[[:space:],])::/0'
}

tunnel_down() {   # reason
  log "Tunnel $IFACE wird gestoppt: $1"
  $SYSTEMCTL stop "$UNIT" > /dev/null 2>&1
  # wg-quick's own teardown misses this when the interface is already gone;
  # a leftover record keeps pointing DNS at the unreachable peer.
  $RESOLVCONF -d "$IFACE" > /dev/null 2>&1
  $RESOLVCONF -u > /dev/null 2>&1
  date +%s > "$STATE"
}

[ "$(getval WG_ENABLED)" = "true" ] || exit 0

if ! $SYSTEMCTL is-active --quiet "$UNIT"
then
  # Down because this script stopped it: try again once the retry window passed.
  now=$(date +%s)
  last=$(cat "$STATE" 2>/dev/null || echo 0)
  [ "$last" = 0 ] && exit 0
  [ $((now - last)) -lt "$RETRY_SEC" ] && exit 0
  log "neuer Verbindungsversuch fuer $IFACE"
  $SYSTEMCTL start "$UNIT" > /dev/null 2>&1
  sleep "$GRACE_SEC"
  age=$(newest_handshake)
  if [ "$age" = never ]
  then
    tunnel_down "auch im neuen Versuch keine Antwort vom Gegenstelle"
  else
    log "Tunnel $IFACE ist wieder da (Handshake vor ${age}s)"
    rm -f "$STATE"
  fi
  exit 0
fi

age=$(newest_handshake)
if [ "$age" != never ] && [ "$age" -le "$HANDSHAKE_MAX" ]
then
  rm -f "$STATE"
  exit 0
fi

if ! full_tunnel
then
  # Split tunnel: a dead one doesn't take the internet down with it.
  exit 0
fi

if [ "$age" = never ]
then
  tunnel_down "keine Antwort von der Gegenstelle (nie ein Handshake) -- er wuerde den gesamten Internetverkehr verschlucken"
else
  tunnel_down "seit ${age}s kein Handshake -- er wuerde den gesamten Internetverkehr verschlucken"
fi
exit 0
