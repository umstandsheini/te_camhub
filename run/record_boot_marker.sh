#!/bin/bash
# Runs as early as systemd allows (boot-marker.service, ordered after
# systemd-remount-fs.service specifically so root is in its final
# fstab-specified state, but deliberately NOT after mutable.mount/
# backingfiles.mount -- see that unit's comment for why). Writes ONE line
# to the root filesystem, briefly remounting rw for it, so a boot that
# never gets far enough to mount /mutable (or never gets far enough for
# teslausb.service/archiveloop to run at all) still leaves evidence it was
# at least attempted. Absence of a marker for a given stretch means the
# kernel itself never started -- genuinely no power. Presence of one, with
# nothing from teslausb.service/archiveloop afterwards, points at a
# later-stage failure (a specific mount, storage, etc.) instead.
#
# This runs before NTP has a chance to sync, so the timestamp itself can
# read as wildly wrong (the same clock-jump artifact archiveloop.log's own
# timestamps show) -- kernel uptime is included specifically so a marker
# can still be placed relative to other boots even when the wall-clock
# value can't be trusted.
#
# Errors are deliberately NOT swallowed here (past version did, and that
# hid a real remount failure) -- a failure to write means the very thing
# this exists to detect (an early-boot failure) is happening, so it must
# show up in `journalctl -u boot-marker.service`, not vanish silently.
set -u
LOG=/var/log/boot_markers.log
FALLBACK_LOG=/mutable/boot_markers_fallback.log

line="$(date -Is) boot attempt, kernel uptime=$(awk '{print $1}' /proc/uptime 2>/dev/null || echo '?')s"

if mount / -o remount,rw && echo "$line" >> "$LOG"
then
  tail -n 500 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
  mount / -o remount,ro
  exit 0
fi
mount / -o remount,ro || true
echo "record_boot_marker: failed to write $LOG, trying $FALLBACK_LOG" >&2

# Root wasn't writable yet at this point in boot -- try /mutable as a
# fallback in case that mount (unlike root) happened to already be ready.
# Not the common case this exists to catch (that's root failing while
# /mutable is fine later on), but cheap insurance either way.
if echo "$line" >> "$FALLBACK_LOG" 2>/dev/null
then
  tail -n 500 "$FALLBACK_LOG" > "${FALLBACK_LOG}.tmp" 2>/dev/null && mv "${FALLBACK_LOG}.tmp" "$FALLBACK_LOG"
  exit 1
fi
echo "record_boot_marker: failed to write $FALLBACK_LOG too -- no marker recorded this boot" >&2
exit 1
