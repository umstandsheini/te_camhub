#!/bin/bash
# Installs an unpacked Hub release:
#
#   hub-update.sh <source dir> <backup .tar.gz> <tag>
#
# hubupdate.py starts this in its own transient systemd unit, since install.sh
# restarts the Hub -- inside the Hub's process the update would kill itself.
# Replaces /root/te_camhub with the new tree and runs its install.sh under
# archiveloop's archive lock (install.sh replaces archiveloop's scripts), then
# waits until the Hub answers again. If install.sh or that check fails, the
# code from the backup hubupdate.py made just before goes back:
# /opt/teslacam-hub, /root/te_camhub, /root/bin and the Hub's systemd units.
# Config and state stay as they are. Progress goes into the run state file the
# Diagnose page reads, output into the log.
#
# The HUB_UPDATE_* variables exist for the offline test, which runs this
# against a scratch directory instead of /.

set -u
SRC=$1
BACKUP=$2
TAG=$3
ROOT=${HUB_UPDATE_ROOT:-}
STATE=${HUB_UPDATE_STATE:-/backingfiles/decrypt-viewer-state/hub-update-run.json}
LOG=${HUB_UPDATE_LOG:-/mutable/hub-update.log}
MARKER=${HUB_UPDATE_MARKER:-/run/teslacam-hub-update}
LOCK=${HUB_UPDATE_LOCK:-/tmp/teslausb_archive.lock}
HEALTH_WAIT=${HUB_UPDATE_HEALTH_WAIT:-240}
# Single quotes on purpose: healthy() evals this on every try.
# shellcheck disable=SC2016
HEALTH_CMD=${HUB_UPDATE_HEALTH_CMD:-'[ "$(curl -sk -m 5 -o /dev/null -w "%{http_code}" https://127.0.0.1/api/vault/status)" = 200 ]'}
MOUNT=${HUB_UPDATE_MOUNT:-mount}
SYSTEMCTL=${HUB_UPDATE_SYSTEMCTL:-systemctl}

exec >> "$LOG" 2>&1
trap 'rm -f "$MARKER"' EXIT

report() {  # report <phase> <true|false|null> [error]
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') $1${3:+: $3}"
  python3 - "$STATE" "$1" "$2" "${3:-}" <<'PY'
import json, os, sys, time
path, phase, ok, err = sys.argv[1:5]
try:
    with open(path, encoding="utf-8") as f:
        run = json.load(f)
except (OSError, ValueError):
    run = {}
run.update(phase=phase, ok={"true": True, "false": False}.get(ok), error=err or None)
if ok != "null":
    run["finished"] = time.time()
with open(path + ".tmp", "w", encoding="utf-8") as f:
    json.dump(run, f)
os.replace(path + ".tmp", path)
PY
}

healthy() {
  local end=$(( $(date +%s) + HEALTH_WAIT ))
  while [ "$(date +%s)" -lt "$end" ]
  do
    eval "$HEALTH_CMD" && return 0
    sleep 3
  done
  return 1
}

rollback() {
  $MOUNT / -o remount,rw
  rm -rf "$ROOT/opt/teslacam-hub" "$ROOT/root/te_camhub"
  # Members missing from the backup (no /root/te_camhub on a hand-deployed
  # Pi) only make tar complain; everything else is restored.
  tar -xzf "$BACKUP" -C "$ROOT/" --wildcards \
    opt/teslacam-hub root/te_camhub root/bin 'etc/systemd/system/teslacam-*'
  $SYSTEMCTL daemon-reload
  $SYSTEMCTL restart teslacam-hub
  sync
  $MOUNT / -o remount,ro
}

echo "##### Installation $TAG, $(date '+%Y-%m-%d %H:%M:%S')"
if ! $MOUNT / -o remount,rw
then
  report "fehlgeschlagen" false "Systempartition ließ sich nicht beschreibbar machen"
  exit 1
fi

failed=""
report "Programm ersetzen" null
# --checksum: size + mtime alone can call a changed file unchanged
if ! rsync -a --delete --checksum "$SRC/" "$ROOT/root/te_camhub/"
then
  failed="Kopieren nach /root/te_camhub fehlgeschlagen"
else
  report "install.sh" null
  if ! flock -w 900 "$LOCK" bash -eu "$ROOT/root/te_camhub/hub/install.sh"
  then
    failed="install.sh fehlgeschlagen (siehe Protokoll)"
  else
    report "Hub-Start prüfen" null
    healthy || failed="Hub antwortet nach dem Update nicht"
  fi
fi

finish_ro() {
  # install.sh already tries this; a last attempt here catches whatever was
  # still holding a replaced file open while it ran.
  sync
  for _ in 1 2 3
  do
    $MOUNT / -o remount,ro 2> /dev/null && return 0
    sleep 5
  done
  echo "=== / bleibt beschreibbar bis zum naechsten Neustart"
  return 0
}

if [ -z "$failed" ]
then
  rm -rf "$SRC"
  finish_ro
  report "fertig" true
  echo "##### Ergebnis: OK"
  exit 0
fi

report "Wiederherstellen" null "$failed"
rollback
if healthy
then
  report "zurückgesetzt" false "$failed – die vorherige Version läuft wieder"
else
  report "zurückgesetzt" false "$failed – vorherige Version wiederhergestellt, der Hub antwortet aber nicht: Pi neu starten"
fi
finish_ro
echo "##### Ergebnis: $failed"
exit 1
