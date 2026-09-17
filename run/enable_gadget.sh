#!/bin/bash -eu

source /root/bin/envsetup.sh

if ! configfs_root=$(findmnt -o TARGET -n configfs)
then
  echo "error: configfs not found"
  exit 1
fi
readonly gadget_root="$configfs_root/usb_gadget/teslausb"

# USB supports many languages. 0x409 is US English
readonly lang=0x409

# configuration name can be anything, the convention
# appears to be to use "c"
readonly cfg=c

function bind_gadget () {
  local udc
  udc=$(find /sys/class/udc -type l -printf '%P\n' | head -1)
  if [ -z "$udc" ]
  then
    echo "error: no USB device controller found (dtoverlay=dwc2 missing?)"
    return 1
  fi
  # g_ether is loaded from cmdline.txt so the host sees a well-behaved
  # device instead of a mute one until this runs (see setup-teslausb's
  # fix_cmdline_txt_modules_load) -- and it holds the UDC until unloaded.
  if grep -q '^g_ether ' /proc/modules
  then
    modprobe -r g_ether
  fi
  echo "$udc" > "$gadget_root/UDC"
  if [ "$(cat "$gadget_root/UDC")" != "$udc" ]
  then
    echo "error: gadget did not bind to $udc"
    return 1
  fi
  echo "bound to $udc at $(cut -d' ' -f1 /proc/uptime)s uptime"
}

# The car needs a *bound* gadget, not merely a configured one. This used to
# exit whenever the configfs directory existed -- which it also does after a
# failed bind (g_ether still holding the UDC), so every later caller logged
# "already prepared" while the car had no drive at all (2026-09-11). An
# existing directory now only skips re-creating it; the bind is always
# checked, and a failure is an error, not silence.
if [ -d "$gadget_root" ]
then
  if [ -n "$(cat "$gadget_root/UDC" 2> /dev/null)" ]
  then
    echo "already bound to $(cat "$gadget_root/UDC")"
    exit 0
  fi
  echo "configured but not bound, binding now"
  bind_gadget
  exit 0
fi

modprobe libcomposite

mkdir -p "$gadget_root/configs/$cfg.1"

# common setup
echo 0x1d6b > "$gadget_root/idVendor"  # Linux Foundation
echo 0x0104 > "$gadget_root/idProduct" # Composite Gadget
echo 0x0100 > "$gadget_root/bcdDevice" # v1.0.0
echo 0x0200 > "$gadget_root/bcdUSB"    # USB 2.0
mkdir -p "$gadget_root/strings/$lang"
mkdir -p "$gadget_root/configs/$cfg.1/strings/$lang"
echo "TeslaUSB-$(sha256sum < /etc/machine-id | awk '{print $1}')" > "$gadget_root/strings/$lang/serialnumber"
echo TeslaUSB > "$gadget_root/strings/$lang/manufacturer"
echo "TeslaUSB Composite Gadget" > "$gadget_root/strings/$lang/product"
echo "TeslaUSB Config" > "$gadget_root/configs/$cfg.1/strings/$lang/configuration"

# A bare Raspberry Pi 4 or 5 can peak at at over 1 A during boot, but idles around 500 mA.
# A Raspberry Pi Zero 2 W can peak at over 300 mA during boot, but idles around 100 mA.
# A Raspberry Pi Zero W can peak up to 220 mA during boot, but idles around 80 mA.
if isPi5
then
  echo 600 > "$gadget_root/configs/$cfg.1/MaxPower"
elif isPi4
then
  echo 500 > "$gadget_root/configs/$cfg.1/MaxPower"
elif isPi2
then
  echo 200 > "$gadget_root/configs/$cfg.1/MaxPower"
else
  echo 100 > "$gadget_root/configs/$cfg.1/MaxPower"
fi

# mass storage setup
mkdir -p "$gadget_root/functions/mass_storage.0"

lun=0

if [ -e "/backingfiles/cam_disk.bin" ]
then
  echo "/backingfiles/cam_disk.bin" > "$gadget_root/functions/mass_storage.0/lun.${lun}/file"
  echo "TeslaUSB CAM $(du -h /backingfiles/cam_disk.bin | awk '{print $1}')" > "$gadget_root/functions/mass_storage.0/lun.${lun}/inquiry_string"
  ((++lun))
fi

# Music alone lives on "media_disk.bin" (kept from the earlier Music/
# LightShow/Boombox merge -- Music has no known Tesla partition-sharing
# restriction). LightShow and Boombox were split back onto their own
# dedicated backing images after merging them broke both features:
# Tesla's firmware only recognizes one of LightShow/Boombox when they
# share a partition (confirmed via marcone/teslausb#832 and
# teslamotors/light-show#111), and LightShow specifically wants a small,
# dedicated FAT32 volume of its own.
if [ -e "/backingfiles/media_disk.bin" ]
then
  mkdir -p "$gadget_root/functions/mass_storage.0/lun.${lun}"
  echo "/backingfiles/media_disk.bin" > "$gadget_root/functions/mass_storage.0/lun.${lun}/file"
  echo "TeslaUSB MEDIA $(du -h /backingfiles/media_disk.bin | awk '{print $1}')" > "$gadget_root/functions/mass_storage.0/lun.${lun}/inquiry_string"
  ((++lun))
fi

if [ -e "/backingfiles/lightshow_disk.bin" ]
then
  mkdir -p "$gadget_root/functions/mass_storage.0/lun.${lun}"
  echo "/backingfiles/lightshow_disk.bin" > "$gadget_root/functions/mass_storage.0/lun.${lun}/file"
  echo "TeslaUSB LIGHTSHOW $(du -h /backingfiles/lightshow_disk.bin | awk '{print $1}')" > "$gadget_root/functions/mass_storage.0/lun.${lun}/inquiry_string"
  ((++lun))
fi

if [ -e "/backingfiles/boombox_disk.bin" ]
then
  mkdir -p "$gadget_root/functions/mass_storage.0/lun.${lun}"
  echo "/backingfiles/boombox_disk.bin" > "$gadget_root/functions/mass_storage.0/lun.${lun}/file"
  echo "TeslaUSB BOOMBOX $(du -h /backingfiles/boombox_disk.bin | awk '{print $1}')" > "$gadget_root/functions/mass_storage.0/lun.${lun}/inquiry_string"
  ((++lun))
fi

ln -sf "$gadget_root/functions/mass_storage.0" "$gadget_root/configs/$cfg.1"

# activate
bind_gadget
