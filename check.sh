#! /bin/bash

shopt -s globstar nullglob extglob

# print shellcheck version so we know what Github uses
shellcheck -V

# SC1091 - Don't complain about not being able to find files that don't exist.
shellcheck --exclude=SC1091 \
           ./setup/pi/setup-teslausb \
           ./pi-gen-sources/00-teslausb-tweaks/files/rc.local \
           ./run/archiveloop \
           ./hub/hub-update.sh \
           ./tools/build-image.sh \
           ./setup/pi/apply-first-config.sh \
           ./setup/pi/firstrun-image.sh \
           ./run/auto.teslausb \
           ./run/awake_start \
           ./run/awake_stop \
           ./run/mountimage \
           ./run/mountoptsforimage \
           ./run/remountfs_rw \
           ./run/send-push-message \
           ./run/temperature_monitor \
           ./run/waitforidle
