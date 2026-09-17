#!/bin/bash
# First boot of the TeslaCam Hub installation image. tools/build-image.sh
# puts this on the boot partition as firstrun.sh and adds systemd.run=... to
# cmdline.txt; both go away at the end. Runs before the network is up.
#
#   - user pi with the password from PI_PASSWORD, or a random one nobody
#     knows (then only an SSH key from SSH_PUBKEY gets in); SSH host keys
#   - setup portal service enabled: it opens the WiFi "TeslaCam-Setup" on the
#     next boots whenever there's no working WiFi (setup-portal.py)
#   - teslausb_setup_variables.conf on the boot partition filled in: moved to
#     /root and applied (apply-first-config.sh); rc.local's setup-teslausb
#     takes over after the reboot
#   - not filled in: the template goes to /root as a starting point for the
#     portal's form, which writes the real config
#
# Replaces the hand-filled firstrun-local.sh.template from the 2026-09-14
# rebuild: no placeholders, nothing secret baked into the image.

set +e
BOOT=/boot/firmware
SRC=/root/te_camhub
CONF_SRC=$BOOT/teslausb_setup_variables.conf
CONF=/root/teslausb_setup_variables.conf
LOG=$BOOT/teslausb-headless-setup.log

log() {
  echo "$(date) : firstrun: $1" >> "$LOG"
}

conf_value() {  # conf_value <file> <variable>
  ( sed 's/\r$//' "$1" > /tmp/firstrun.conf && . /tmp/firstrun.conf && eval "printf %s \"\${$2:-}\"" )
  rm -f /tmp/firstrun.conf
}

log "Erster Start des TeslaCam-Hub-Images"

PW=""
[ -f "$CONF_SRC" ] && PW=$(conf_value "$CONF_SRC" PI_PASSWORD)
[ -n "$PW" ] || PW=$(tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 24)
/usr/lib/userconf-pi/userconf pi "$(printf '%s' "$PW" | openssl passwd -6 -stdin)"
unset PW
echo 'pi ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/010_pi-nopasswd
chmod 440 /etc/sudoers.d/010_pi-nopasswd
ls /etc/ssh/ssh_host_*_key > /dev/null 2>&1 || ssh-keygen -A

install -m 644 "$SRC/setup/pi/teslacam-setup-portal.service" /etc/systemd/system/teslacam-setup-portal.service
systemctl enable teslacam-setup-portal.service

SSID=""
[ -f "$CONF_SRC" ] && SSID=$(conf_value "$CONF_SRC" SSID)
if [ -n "$SSID" ] && [ "$SSID" != "DEIN_WLAN" ]
then
  sed -i 's/\r$//' "$CONF_SRC"
  mv "$CONF_SRC" "$CONF"
  sed -i '/^export PI_PASSWORD=/d' "$CONF"
  bash "$SRC/setup/pi/apply-first-config.sh" "$CONF"
  log "Konfiguration übernommen (WLAN $SSID) – die Einrichtung startet nach dem Neustart"
else
  if [ -f "$CONF_SRC" ]
  then
    sed -i 's/\r$//; /^export PI_PASSWORD=/d' "$CONF_SRC"
    mv "$CONF_SRC" "$CONF.template"
  fi
  install -m 755 "$SRC/pi-gen-sources/00-teslausb-tweaks/files/rc.local" /etc/rc.local
  install -d /root/bin
  install -m 755 "$SRC/setup/pi/setup-teslausb" /root/bin/setup-teslausb
  ln -sfn "$BOOT" /teslausb
  touch "$BOOT/WIFI_ENABLED"
  command -v raspi-config > /dev/null && raspi-config nonint do_wifi_country DE
  rfkill unblock wifi 2> /dev/null
  for f in /var/lib/systemd/rfkill/*:wlan
  do
    [ -e "$f" ] && echo 0 > "$f"
  done
  log "Keine ausgefüllte teslausb_setup_variables.conf – nach dem Neustart mit dem WLAN \"TeslaCam-Setup\" (Passwort teslacam-setup) verbinden und die Seite ausfüllen"
fi

rm -f "$BOOT/firstrun.sh"
sed -i 's| systemd.run.*||g' "$BOOT/cmdline.txt"
exit 0
