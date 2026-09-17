#!/bin/bash -eu
# Builds the TeslaCam Hub installation image te_camhub-<version>.img.xz:
# Raspberry Pi OS Lite (Bookworm, 64-bit -- the base the Hub runs on since the
# 2026-09-14 rebuild) plus
#   - this repository in /root/te_camhub
#   - setup/pi/firstrun-image.sh as the first-boot script (systemd.run in
#     cmdline.txt, like Raspberry Pi Imager does it)
#   - image/teslausb_setup_variables.conf and image/LIESMICH.txt on the boot
#     partition
# and without Raspberry Pi OS's firstboot step, which would grow the root
# partition over the whole disk: setup-teslausb needs that space for
# /backingfiles and /mutable. Nothing is installed into the OS here; the first
# boot and setup-teslausb do that on the Pi.
#
#   sudo tools/build-image.sh [--src te_camhub-<tag>.tar.gz] [--out file.img.xz]
#                             [--base raspios.img.xz] [--country DE]
#
# Without --src the committed tree goes in (git archive HEAD), with VERSION
# "dev-<commit>". The base image is downloaded once into .image-cache/ and
# checked against its SHA-256. Needs Linux, root (loop mounts), curl, xz,
# sfdisk.

BASE_URL=https://downloads.raspberrypi.com/raspios_oldstable_lite_arm64/images/raspios_oldstable_lite_arm64-2026-06-19/2026-06-18-raspios-bookworm-arm64-lite.img.xz
BASE_SHA256=df4c6fb0b625204a09ce587f35f01b4d6375cb3ee0f05df71a0eca6c7aeb8f8f

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
SRC_TGZ=""
OUT=""
BASE=""
COUNTRY=DE
while [ $# -gt 0 ]
do
  case "$1" in
    --src) SRC_TGZ=$2; shift 2 ;;
    --out) OUT=$2; shift 2 ;;
    --base) BASE=$2; shift 2 ;;
    --country) COUNTRY=$2; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
if [ "$(id -u)" != 0 ]
then
  echo "needs root (loop mounts): sudo $0 $*" >&2
  exit 1
fi

WORK=$(mktemp -d)
cleanup() {
  for m in "$WORK/boot" "$WORK/root"
  do
    if mountpoint -q "$m"
    then
      umount "$m"
    fi
  done
  rm -rf "$WORK"
}
trap cleanup EXIT

echo "== source tree"
mkdir -p "$WORK/src"
if [ -n "$SRC_TGZ" ]
then
  tar -xzf "$SRC_TGZ" -C "$WORK/src"
else
  mkdir "$WORK/src/te_camhub"
  git -C "$REPO_DIR" archive HEAD | tar -x -C "$WORK/src/te_camhub"
  echo "dev-$(git -C "$REPO_DIR" rev-parse --short HEAD)" > "$WORK/src/te_camhub/VERSION"
fi
TREE=$WORK/src/te_camhub
if [ ! -f "$TREE/setup/pi/firstrun-image.sh" ] || [ ! -f "$TREE/VERSION" ]
then
  echo "not a te_camhub source tree" >&2
  exit 1
fi
VERSION=$(tr -d ' \r\n' < "$TREE/VERSION")
OUT=${OUT:-$REPO_DIR/dist/te_camhub-$VERSION.img.xz}
mkdir -p "$(dirname "$OUT")"

echo "== base image"
if [ -z "$BASE" ]
then
  CACHE=${IMAGE_CACHE:-$REPO_DIR/.image-cache}
  mkdir -p "$CACHE"
  BASE=$CACHE/$(basename "$BASE_URL")
  if [ ! -f "$BASE" ]
  then
    curl -fL --retry 3 -o "$BASE.part" "$BASE_URL"
    mv "$BASE.part" "$BASE"
  fi
fi
echo "$BASE_SHA256  $BASE" | sha256sum -c -

IMG=$WORK/te_camhub.img
xz -dc "$BASE" > "$IMG"
mapfile -t PARTS < <(sfdisk -d "$IMG" | sed -n 's/.*start= *\([0-9]*\), *size= *\([0-9]*\).*/\1 \2/p')
read -r BOOT_START BOOT_SECTORS <<< "${PARTS[0]}"
read -r ROOT_START ROOT_SECTORS <<< "${PARTS[1]}"
mkdir "$WORK/boot" "$WORK/root"
mount -o loop,offset=$((BOOT_START * 512)),sizelimit=$((BOOT_SECTORS * 512)) "$IMG" "$WORK/boot"
mount -o loop,offset=$((ROOT_START * 512)),sizelimit=$((ROOT_SECTORS * 512)) "$IMG" "$WORK/root"
B=$WORK/boot
R=$WORK/root

echo "== customizing ($VERSION)"
install -d -m 700 "$R/root"
rm -rf "$R/root/te_camhub"
cp -a "$TREE" "$R/root/te_camhub"
chown -R 0:0 "$R/root/te_camhub"

# vfat: plain cp, no permissions to set
cp "$TREE/setup/pi/firstrun-image.sh" "$B/firstrun.sh"
cp "$TREE/image/teslausb_setup_variables.conf" "$B/teslausb_setup_variables.conf"
cp "$TREE/image/LIESMICH.txt" "$B/LIESMICH.txt"

CMDLINE=$B/cmdline.txt
sed -i 's| init=/usr/lib/raspberrypi-sys-mods/firstboot||; s|[[:space:]]*$||' "$CMDLINE"
if ! grep -q "systemd.run=" "$CMDLINE"
then
  sed -i "s|\$| cfg80211.ieee80211_regdom=$COUNTRY systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target|" "$CMDLINE"
fi
if ! grep -q "^dtoverlay=dwc2" "$B/config.txt"
then
  printf '\n[all]\ndtoverlay=dwc2\n' >> "$B/config.txt"
fi
echo "cmdline.txt: $(cat "$CMDLINE")"

sync
umount "$B" "$R"

echo "== compressing to $OUT"
xz -T0 -6 -c "$IMG" > "$OUT.part"
mv "$OUT.part" "$OUT"
ls -l "$OUT"
sha256sum "$OUT"
