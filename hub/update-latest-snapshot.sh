#!/bin/bash -eu
# Keeps /run/teslacam-latest/mnt pointed at the newest teslausb snapshot, so
# the Hub always reads current data without needing to know snapshot numbers.
# teslausb rotates snap-000000, snap-000001, ... under /backingfiles/snapshots/
# (see run/make_snapshot.sh upstream).

latest_dir=$(ls -d /backingfiles/snapshots/snap-*/ 2>/dev/null | sort | tail -1)
if [ -z "$latest_dir" ]
then
  exit 0
fi
latest=$(basename "$latest_dir")

# accessing the path triggers the autofs mount defined by run/auto.teslausb
ls "/tmp/snapshots/$latest" > /dev/null 2>&1 || true

mkdir -p /run/teslacam-latest
ln -sfn "/tmp/snapshots/$latest" /run/teslacam-latest/mnt
