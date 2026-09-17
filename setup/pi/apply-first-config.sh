#!/bin/bash
# Applies the parts of teslausb_setup_variables.conf that must be in place
# before setup-teslausb can run on a fresh image: host name, the home WiFi as
# a NetworkManager profile, WiFi country (Raspberry Pi OS keeps WiFi blocked
# without one), time zone, an SSH key for pi, and SOURCE_DIR pointing at the
# source tree the image ships in /root/te_camhub (so nothing is fetched from
# GitHub, whose ${REPO}/teslausb URL scheme can't reach this fork anyway).
#
# Called by the image's firstrun.sh (config file filled in before the first
# boot) and by setup-portal.py (form sent from the setup hotspot).
#
#   apply-first-config.sh /root/teslausb_setup_variables.conf
#
# Writes the WiFi keyfile directly: nmcli can't add connections on this OS
# (see hub/ap-ensure.sh), and during firstrun.sh NetworkManager isn't even
# running yet.

CONF=${1:?config file required}
BOOT=/boot/firmware
SRC=/root/te_camhub
CUSTOM=/usr/lib/raspberrypi-sys-mods/imager_custom
WIFI_MARK=/root/.teslacam-first-wifi   # profile written last time, replaced on a second run

sed -i 's/\r$//' "$CONF"
# shellcheck disable=SC1090
. "$CONF"

HOST=${TESLAUSB_HOSTNAME:-teslausb}
if [ -x "$CUSTOM" ]
then
  "$CUSTOM" set_hostname "$HOST"
else
  CURRENT=$(tr -d " \t\n\r" < /etc/hostname)
  echo "$HOST" > /etc/hostname
  sed -i "s/127.0.1.1.*$CURRENT/127.0.1.1\t$HOST/g" /etc/hosts
fi

if [ -n "${SSID:-}" ]
then
  if [ -f "$WIFI_MARK" ]
  then
    rm -f "$(cat "$WIFI_MARK")"
  fi
  NMFILE="/etc/NetworkManager/system-connections/${SSID//\//_}.nmconnection"
  (
    umask 077
    printf '[connection]\nid=%s\nuuid=%s\ntype=wifi\ninterface-name=wlan0\nautoconnect=true\n\n[wifi]\nmode=infrastructure\nssid=%s\nhidden=true\n\n' \
      "$SSID" "$(cat /proc/sys/kernel/random/uuid)" "$SSID"
    if [ -n "${WIFIPASS:-}" ]
    then
      printf '[wifi-security]\nkey-mgmt=wpa-psk\npsk=%s\n\n' "$WIFIPASS"
    fi
    printf '[ipv4]\nmethod=auto\n\n[ipv6]\nmethod=auto\n'
  ) > "$NMFILE"
  chown root:root "$NMFILE"
  chmod 600 "$NMFILE"
  echo "$NMFILE" > "$WIFI_MARK"
fi

command -v raspi-config > /dev/null && raspi-config nonint do_wifi_country "${WIFI_COUNTRY:-DE}"
rfkill unblock wifi 2> /dev/null
for f in /var/lib/systemd/rfkill/*:wlan
do
  [ -e "$f" ] && echo 0 > "$f"
done

if [ -x "$CUSTOM" ]
then
  "$CUSTOM" set_timezone "${TIME_ZONE:-Europe/Berlin}"
else
  ln -sf "/usr/share/zoneinfo/${TIME_ZONE:-Europe/Berlin}" /etc/localtime
fi

if [ -n "${SSH_PUBKEY:-}" ]
then
  install -o pi -g pi -m 700 -d /home/pi/.ssh
  echo "$SSH_PUBKEY" > /home/pi/.ssh/authorized_keys
  chown pi:pi /home/pi/.ssh/authorized_keys
  chmod 600 /home/pi/.ssh/authorized_keys
  systemctl enable ssh
fi

grep -q '^export SOURCE_DIR=' "$CONF" || printf "\nexport SOURCE_DIR='%s'\n" "$SRC" >> "$CONF"
chmod 600 "$CONF"

install -m 755 "$SRC/pi-gen-sources/00-teslausb-tweaks/files/rc.local" /etc/rc.local
install -d /root/bin
install -m 755 "$SRC/setup/pi/setup-teslausb" /root/bin/setup-teslausb
ln -sfn "$BOOT" /teslausb
# WiFi is configured above; skip rc.local's own WiFi step and its extra reboot.
touch "$BOOT/WIFI_ENABLED"
exit 0
