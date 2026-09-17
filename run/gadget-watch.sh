#!/bin/bash
# Logs (to the journal, via teslausb-gadget-watch.service) the moment the
# host -- the car -- has actually configured the USB drives. Power-on to this
# line is the number that decides whether a woken car ever sees its dashcam
# drive; every boot-time change is measured against it.
state=""
for _ in $(seq 1 240)
do
  state=$(cat /sys/class/udc/*/state 2> /dev/null || true)
  if [ "$state" = "configured" ]
  then
    echo "host configured the drives at $(cut -d' ' -f1 /proc/uptime)s uptime"
    exit 0
  fi
  sleep 0.5
done
echo "host did not configure the drives within 120 s (state: ${state:-unknown})"
