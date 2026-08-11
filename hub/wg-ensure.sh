#!/bin/bash -eu
# hub/wg-ensure.sh
#
# Ensures a WireGuard client tunnel (wg0) home. Reads interface/peer
# settings as KEY=VALUE lines on stdin (one per line) rather than
# positional args -- values can include a private key/preshared key
# imported from a QR code, and stdin keeps those off argv (visible to any
# local user via `ps`). Recognised keys:
#   PEER_PUBKEY  (required)              ENDPOINT   (required)
#   ALLOWED_IPS  (default 0.0.0.0/0)     ADDRESS    (required)
#   KEEPALIVE    (default 25)            PSK        (optional)
#   PRIVKEY      (optional -- e.g. imported from a QR code; if omitted,
#                 keeps this Pi's own previously-generated key, or
#                 generates one on first run)
#   DNS          (optional)
#
# Idempotent: safe to call repeatedly, e.g. every time settings are saved
# or a QR code is imported -- always rewrites the whole wg0.conf. Does not
# start/restart the wg-quick@wg0 service itself -- the caller (diag.py's
# apply_wireguard) does that after this script returns.

PEER_PUBKEY="" ENDPOINT="" ALLOWED_IPS="0.0.0.0/0" ADDRESS="" KEEPALIVE="25" PSK="" PRIVKEY="" DNS=""
while IFS= read -r line || [ -n "$line" ]; do
  # Split on the FIRST '=' only -- IFS='=' read with multiple '=' in the
  # value (e.g. base64 padding on a WireGuard key: "...fX8=") drops the
  # trailing delimiter+empty-field instead of keeping it as part of the
  # last variable, silently truncating the key by its padding character.
  key="${line%%=*}"
  val="${line#*=}"
  case "$key" in
    PEER_PUBKEY) PEER_PUBKEY="$val" ;;
    ENDPOINT) ENDPOINT="$val" ;;
    ALLOWED_IPS) [ -n "$val" ] && ALLOWED_IPS="$val" ;;
    ADDRESS) ADDRESS="$val" ;;
    KEEPALIVE) [ -n "$val" ] && KEEPALIVE="$val" ;;
    PSK) PSK="$val" ;;
    PRIVKEY) PRIVKEY="$val" ;;
    DNS) DNS="$val" ;;
  esac
done

[ -n "$PEER_PUBKEY" ] || { echo "wg-ensure: PEER_PUBKEY required" >&2; exit 1; }
[ -n "$ENDPOINT" ] || { echo "wg-ensure: ENDPOINT required" >&2; exit 1; }
[ -n "$ADDRESS" ] || { echo "wg-ensure: ADDRESS required" >&2; exit 1; }

WG_DIR=/etc/wireguard
mkdir -p "$WG_DIR"
chmod 700 "$WG_DIR"

umask 077
if [ -n "$PRIVKEY" ]; then
  echo "$PRIVKEY" > "$WG_DIR/privatekey"
  echo "$PRIVKEY" | wg pubkey > "$WG_DIR/publickey"
elif [ ! -f "$WG_DIR/privatekey" ]; then
  wg genkey | tee "$WG_DIR/privatekey" | wg pubkey > "$WG_DIR/publickey"
fi
chmod 600 "$WG_DIR/privatekey"
PRIVOUT="$(cat "$WG_DIR/privatekey")"

DNS_LINE=""
[ -n "$DNS" ] && DNS_LINE="DNS = $DNS"
PSK_LINE=""
[ -n "$PSK" ] && PSK_LINE="PresharedKey = $PSK"

cat > "$WG_DIR/wg0.conf" <<CONFEOF
[Interface]
PrivateKey = $PRIVOUT
Address = $ADDRESS
$DNS_LINE

[Peer]
PublicKey = $PEER_PUBKEY
Endpoint = $ENDPOINT
AllowedIPs = $ALLOWED_IPS
PersistentKeepalive = $KEEPALIVE
$PSK_LINE
CONFEOF
chown root:root "$WG_DIR/wg0.conf"
chmod 600 "$WG_DIR/wg0.conf"

echo "wg-ensure: wg0.conf ready (endpoint=$ENDPOINT allowed_ips=$ALLOWED_IPS)"
echo "wg-ensure: own public key: $(cat "$WG_DIR/publickey")"
