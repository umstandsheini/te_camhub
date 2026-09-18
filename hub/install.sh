#!/bin/bash -eu
# TeslaCam Hub installer.
#
# Run as root on a Pi where teslausb's own one-step setup has already
# completed (i.e. after first boot). Installs the Hub as the sole HTTPS
# service on 443 (with an 80->443 redirect) and disables teslausb's own
# nginx/cgi-bin web UI entirely -- the Hub replaces it, there's no fallback
# UI anymore. teslausb's core (gadget/snapshots/archive, and the cgi-bin
# *.sh scripts the Hub itself still shells out to for BLE/drive-toggle) is
# untouched -- this only turns off the old HTTP-facing layer.
#
#   sudo bash hub/install.sh
#
# Safe to re-run: copies the app fresh each time and restarts the service.

HUB_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HUB_DST=/opt/teslacam-hub
STATE_DIR=/backingfiles/decrypt-viewer-state
TLS_DIR=/mutable/tls

if [ "$(id -u)" -ne 0 ]; then
  echo "Must run as root (sudo bash hub/install.sh)" >&2
  exit 1
fi

echo "[hub-install] remounting / rw"
mount / -o remount,rw

apt-get update -y
# ffmpeg pulls in ~150 packages (X11/audio/video libs) via apt on this image
# -- on a Pi whose root partition is only ~1.8G (this one included), that
# alone can exhaust it. If a working ffmpeg binary is already on PATH (e.g.
# a manually-installed static build, the existing workaround on this box),
# skip asking apt for the real package instead of re-attempting (and
# re-failing) that huge pull on every install.sh re-run.
# wireless-tools/iw: diag.status() and synchold read the current SSID via
# iwgetid, and wifi-powersave-off.service needs iw -- Lite images don't
# promise either.
PKGS=(python3-pip openssl wireguard-tools libzbar0 python3-pil python3-pyzbar openresolv firmware-realtek wireless-tools iw)
if ! command -v ffmpeg > /dev/null; then
  PKGS+=(ffmpeg)
fi
echo "[hub-install] installing OS packages (${PKGS[*]})"
# libzbar0/python3-pil/python3-pyzbar (not zbar-tools) is the QR-code decode
# path used by diag.py's import_wg_qr -- zbar-tools' zbarimg CLI drags in the
# full ImageMagick/libmagickwand stack for image loading, which is the same
# kind of disk-busting pull as ffmpeg above; the pyzbar+Pillow path needs
# only these small libs.
apt-get install -y --no-install-recommends "${PKGS[@]}"

echo "[hub-install] installing python deps (pycryptodome, paho-mqtt, bleak, anthropic)"
# anthropic: Claude API SDK for the Assistent tab (1.x needs Python >= 3.10,
# Bookworm ships 3.11). assistant.py imports it lazily, so it costs no RAM
# until the tab is actually used.
pip3 install --break-system-packages --quiet pycryptodome paho-mqtt bleak anthropic 2>/dev/null \
  || pip3 install --quiet pycryptodome paho-mqtt bleak anthropic

echo "[hub-install] copying app to $HUB_DST"
mkdir -p "$HUB_DST"
# --checksum: a file changed in an update can keep its size and mtime
rsync -a --delete --checksum --exclude '__pycache__' "$HUB_SRC/app/" "$HUB_DST/app/"
mkdir -p "$STATE_DIR" /dev/shm/teslacam "$TLS_DIR"
# The version the update check compares against GitHub's latest release
# (hubupdate.py). Release packages carry the tag in VERSION; a checkout says "dev".
if [ -f "$HUB_SRC/../VERSION" ]; then
  tr -d ' \r\n' < "$HUB_SRC/../VERSION" > "$HUB_DST/VERSION"
else
  echo dev > "$HUB_DST/VERSION"
fi
install -m 755 "$HUB_SRC/hub-update.sh" "$HUB_DST/hub-update.sh"

# teslausb's own first-boot setup fetches run/archiveloop and
# run/make_snapshot.sh from ${REPO}/teslausb/${BRANCH} -- a URL scheme
# that assumes the fork keeps the upstream repo name. This fork is named
# te_camhub, so REPO=bernd780 404s (see teslausb_setup_variables.conf.sample's
# REPO/BRANCH comment) and the device is stuck running unmodified
# marcone/main-dev core scripts. That silently breaks archiving on any
# car whose firmware writes dashcam clips under TeslaCam/EncryptedClips/
# (2026.20+) instead of directly under TeslaCam/, since the upstream
# make_snapshot.sh only looks in the old location and never links any
# files for archiveloop to pick up -- NAS sync then reports "0 event
# folders" forever, with no error anywhere. Deploy this fork's fixed
# versions directly so a fresh stick doesn't need to hit that failure
# once before getting patched by hand.
echo "[hub-install] deploying this fork's run/archiveloop + run/make_snapshot.sh (upstream REPO/BRANCH fetch can't reach a renamed fork -- see comment above)"
# install, not cp: it replaces the file instead of writing into it, and a
# running archiveloop (bash reads its script as it goes) keeps the old copy
# until its next start -- an update from the Hub runs while archiveloop does.
mkdir -p /root/bin
install -m 755 "$HUB_SRC/../run/archiveloop" /root/bin/archiveloop
install -m 755 "$HUB_SRC/../run/make_snapshot.sh" /root/bin/make_snapshot.sh
install -m 755 "$HUB_SRC/../run/filter_savedclips_window.py" /root/bin/filter_savedclips_window.py

echo "[hub-install] generating self-signed TLS cert (if missing)"
if [ ! -f "$TLS_DIR/cert.pem" ] || [ ! -f "$TLS_DIR/key.pem" ]; then
  HOST=$(hostname)
  openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
    -keyout "$TLS_DIR/key.pem" -out "$TLS_DIR/cert.pem" \
    -subj "/CN=$HOST" >/dev/null 2>&1
fi

echo "[hub-install] disabling teslausb's own nginx web UI (Hub replaces it; cgi-bin *.sh files stay on disk, the Hub still shells out to them directly)"
systemctl disable --now nginx 2>/dev/null || true

echo "[hub-install] installing snapshot-pointer helper + timer"
cp "$HUB_SRC/update-latest-snapshot.sh" "$HUB_DST/update-latest-snapshot.sh"
chmod +x "$HUB_DST/update-latest-snapshot.sh"
cp "$HUB_SRC/teslacam-latest-snapshot.service" /etc/systemd/system/teslacam-latest-snapshot.service
cp "$HUB_SRC/teslacam-latest-snapshot.timer" /etc/systemd/system/teslacam-latest-snapshot.timer
systemctl daemon-reload
systemctl enable teslacam-latest-snapshot.timer
systemctl start teslacam-latest-snapshot.timer
systemctl start teslacam-latest-snapshot.service

echo "[hub-install] installing AP + AP-on-USB helper scripts (the AP itself is managed by wifi-watch.sh)"
install -m 755 "$HUB_SRC/ap-ensure.sh" "$HUB_DST/ap-ensure.sh"
install -m 755 "$HUB_SRC/ap-usb-ensure.sh" "$HUB_DST/ap-usb-ensure.sh"

echo "[hub-install] installing the WiFi watcher + timer (best known WiFi, AP only as a fallback)"
install -m 755 "$HUB_SRC/wifi-watch.sh" "$HUB_DST/wifi-watch.sh"
cp "$HUB_SRC/teslacam-wifi-watch.service" /etc/systemd/system/teslacam-wifi-watch.service
cp "$HUB_SRC/teslacam-wifi-watch.timer" /etc/systemd/system/teslacam-wifi-watch.timer
# Superseded: ap-fallback-watch.sh only ever switched the AP, home-wifi-watch.sh
# only the way back home, and they fought over the radio. wifi-watch.sh does both.
systemctl disable --now teslacam-ap-fallback.timer teslacam-home-wifi.timer 2>/dev/null || true
rm -f /etc/systemd/system/teslacam-ap-fallback.{service,timer} \
      /etc/systemd/system/teslacam-home-wifi.{service,timer} \
      "$HUB_DST/ap-fallback-watch.sh" "$HUB_DST/home-wifi-watch.sh"
systemctl daemon-reload
systemctl enable --now teslacam-wifi-watch.timer

echo "[hub-install] installing the WireGuard watchdog + timer (a dead full tunnel swallows all internet)"
install -m 755 "$HUB_SRC/wg-watch.sh" "$HUB_DST/wg-watch.sh"
cp "$HUB_SRC/teslacam-wg-watch.service" /etc/systemd/system/teslacam-wg-watch.service
cp "$HUB_SRC/teslacam-wg-watch.timer" /etc/systemd/system/teslacam-wg-watch.timer
systemctl daemon-reload
systemctl enable --now teslacam-wg-watch.timer

echo "[hub-install] installing hotspot + WireGuard helper scripts (off until enabled in Einstellungen)"
cp "$HUB_SRC/hotspot-ensure.sh" "$HUB_DST/hotspot-ensure.sh"
cp "$HUB_SRC/wg-ensure.sh" "$HUB_DST/wg-ensure.sh"
chmod +x "$HUB_DST/hotspot-ensure.sh" "$HUB_DST/wg-ensure.sh"

echo "[hub-install] installing systemd unit"
cp "$HUB_SRC/teslacam-hub.service" /etc/systemd/system/teslacam-hub.service
systemctl daemon-reload
systemctl enable teslacam-hub
systemctl restart teslacam-hub

echo "[hub-install] applying secure defaults on first run only (never overrides an existing conf value)"
CONF=/root/teslausb_setup_variables.conf
getconf_val() { grep "^export $1=" "$CONF" 2>/dev/null | tail -1 | sed -E "s/^export $1=//; s/^'(.*)'\$/\1/"; }
if [ -f "$CONF" ] && [ -z "$(getconf_val SSH_DISABLE_PASSWORD)" ]; then
  echo "[hub-install] WARNING: disabling SSH password login by default." \
       "Make sure an SSH key is authorized for this Pi BEFORE relying on remote SSH again" \
       "-- otherwise only physical/console access can get you back in." \
       "Revert any time via Einstellungen -> Sicherheit."
  echo "export SSH_DISABLE_PASSWORD='true'" >> "$CONF"
  mkdir -p /etc/ssh/sshd_config.d
  echo "PasswordAuthentication no" > /etc/ssh/sshd_config.d/99-teslausb.conf
  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
fi
# Without a USB WiFi adapter the Pi's own access point shares the single radio
# with the client and pins it to the AP's channel -- a phone hotspot on another
# channel is then hard to join. So the AP defaults to fallback-only; set
# AP_FALLBACK_ONLY=false (Einstellungen) to keep it up permanently.
if [ -f "$CONF" ] && [ -z "$(getconf_val AP_FALLBACK_ONLY)" ]; then
  echo "export AP_FALLBACK_ONLY='true'" >> "$CONF"
fi
if [ -f "$CONF" ] && [ -z "$(getconf_val VAULT_AUTOLOCK_MIN)" ]; then
  echo "export VAULT_AUTOLOCK_MIN='180'" >> "$CONF"
fi
if [ -f "$CONF" ] && [ -z "$(getconf_val SAMBA_ENABLED)" ]; then
  echo "export SAMBA_ENABLED='true'" >> "$CONF"
fi

echo "[hub-install] ensuring SMB/Samba share of TeslaCam (Einstellungen -> SMB-Freigabe; on by default)"
if [ -f "$CONF" ] && [ "$(getconf_val SAMBA_ENABLED)" != "false" ]; then
  # configure-samba.sh's first-install branch creates a 'pi' Samba account
  # with the insecure default password "raspberry". That branch can run here
  # or already in setup-teslausb (SAMBA_ENABLED=true in the config) -- and in
  # the second case the old "was smbd installed before this script?" check
  # skipped the password, which is exactly how the 2026-09-14 rebuild ended
  # up sharing every recording as pi/raspberry. A marker on /mutable instead
  # sets a generated password exactly once per install, never on re-runs
  # (which would silently invalidate the password the user already has).
  SMB_PW_MARKER=/mutable/.hub-smb-password-set
  SAMBA_GUEST=false bash "$HUB_SRC/../setup/pi/configure-samba.sh"
  # The stock ExecCondition (is-configured, ~13 s even idle) timed out under
  # boot I/O load and took the share down with it; smbd's own start then hit
  # the 90 s default. "server role = standalone server" makes the condition a
  # constant anyway (SESSION_FINDINGS_2026-09-13.md §3).
  mkdir -p /etc/systemd/system/smbd.service.d
  printf '[Service]\nExecCondition=\nTimeoutStartSec=600\n' > /etc/systemd/system/smbd.service.d/override.conf
  systemctl daemon-reload
  systemctl enable --now smbd nmbd 2>/dev/null || true
  if [ ! -e "$SMB_PW_MARKER" ]; then
    GENPW="$(tr -dc 'A-Za-z0-9' < /dev/urandom | head -c16)"
    printf '%s\n%s\n' "$GENPW" "$GENPW" | smbpasswd -s -a pi >/dev/null 2>&1
    touch "$SMB_PW_MARKER"
    echo "[hub-install] generated SMB password for user 'pi' (overriding the script's insecure 'raspberry' default): $GENPW"
    echo "[hub-install]   change it any time in Einstellungen -> SMB-Freigabe"
  fi
else
  systemctl disable --now smbd nmbd 2>/dev/null || true
fi

# `install` above replaces the run scripts instead of writing into them, so a
# running archiveloop keeps reading the copy it started with -- but that
# deleted inode also keeps / from going read-only again ("mount point is
# busy", seen on the 2026-09-17 update). Restarting teslausb releases it and
# picks up the new scripts; the car loses the USB drives for a few seconds.
if systemctl is-active --quiet teslausb; then
  echo "[hub-install] restarting teslausb so it runs the new archiveloop"
  systemctl restart teslausb
  sleep 5
fi

echo "[hub-install] remounting / ro"
sync
# Every restart above (the Hub itself included) leaves the replaced files
# open in the process that is going away, and a deleted-but-open file keeps
# the filesystem busy. On the 2026-09-18 update three tries over 9 s were not
# enough and / stayed writable; six over 30 s cover it, and the last attempt
# names whoever is still holding on.
for try in 1 2 3 4 5 6; do
  if mount / -o remount,ro; then
    break
  fi
  if [ "$try" = 6 ]; then
    echo "[hub-install] WARNING: / stays writable until the next boot. Still holding deleted files:"
    for p in /proc/[0-9]*; do
      for fd in "$p"/fd/*; do
        target=$(readlink "$fd" 2>/dev/null) || continue
        case "$target" in
          *"(deleted)")
            case "$target" in
              /root/*|/opt/*|/usr/*|/etc/*)
                echo "[hub-install]   $(cat "$p/comm" 2>/dev/null) (${p#/proc/}) -> $target" ;;
            esac ;;
        esac
      done
    done 2>/dev/null | sort -u | head -5
  else
    sleep 5
  fi
done

echo "[hub-install] done."
echo "  Primary UI: https://$(hostname).local/  (or https://<pi-ip>/)"
echo "  First visit sets up the vault (encryption passphrase = login password)."
