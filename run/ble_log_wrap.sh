#!/bin/bash -eu
# Wraps a tesla-control invocation with the existing cross-process BLE lock
# (see hub/app/diag.py's BLE_LOCKFILE comment -- the Pi has exactly one
# Bluetooth adapter) plus a persistent log line to /mutable/ble.log:
# timestamp, caller, exit code, and how many HCI-level connections this Pi
# itself has open before/after (via hcitool con), so a "vehicle is already
# connected to the maximum number of BLE devices" failure can be told apart
# from this Pi leaking its own stale connections vs. some other device
# (phone key, another BLE accessory) occupying the car's connection slots.
# Never lets a logging problem change the tesla-control result: hci checks
# are best-effort ("?" on failure), and the original stdout/exit code
# always pass straight through.
#
# Usage: ble_log_wrap.sh <source-label> -- <tesla-control args...>

LOG=/mutable/ble.log
source_label="$1"; shift
if [ "${1:-}" = "--" ]; then shift; fi

hci_count() {
  hcitool con 2>/dev/null | grep -c 'handle' || echo "?"
}

hci_before=$(hci_count)
ts_start=$(date -Is)
set +e
out=$(flock /tmp/ble.lock /root/bin/tesla-control "$@" 2>&1)
rc=$?
set -e
hci_after=$(hci_count)

{
  echo "${ts_start} [${source_label}] rc=${rc} hci_before=${hci_before} hci_after=${hci_after} cmd=\"tesla-control $*\""
  out_oneline=$(echo "${out}" | tr '\n' ' ')
  echo "  out: ${out_oneline:0:300}"
} >> "${LOG}" 2>/dev/null || true

# trim the log to the last ~5000 lines so it can't grow unbounded
if [ -f "${LOG}" ]
then
  tail -n 5000 "${LOG}" > "${LOG}.tmp" 2>/dev/null && mv "${LOG}.tmp" "${LOG}" || true
fi

echo "${out}"
exit "${rc}"
