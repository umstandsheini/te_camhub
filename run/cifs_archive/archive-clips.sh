#!/bin/bash -eu

function connectionmonitor {
  while true
  do
    for _ in {1..5}
    do
      if timeout 6 /root/bin/archive-is-reachable.sh "$ARCHIVE_SERVER"
      then
        # sleep and then continue outer loop
        sleep 5
        continue 2
      fi
      sleep 1
    done
    log "connection dead, killing archive-clips"
    # Since there can be a substantial delay before rsync deletes archived
    # source files, give it an opportunity to delete them before killing it
    # hard.
    killall rsync || true
    sleep 2
    killall -9 rsync || true
    kill -9 "$1" || true
    return
  done
}

connectionmonitor $$ &

# rsync's temp files may be left behind if the connection is lost,
# but rsync doesn't clean these up on subsequent runs. Putting
# them in a temp dir allows them to be easily cleaned up.
rsynctmp=".teslausbtmp"
rm -rf "$ARCHIVE_MOUNT/${rsynctmp:?}" || true
mkdir -p "$ARCHIVE_MOUNT/$rsynctmp"

rm -f /tmp/archive-rsync-cmd.log /tmp/archive-error.log

# Optional safety net alongside archiveloop's fq_codel qdisc change (see
# marcone/teslausb#728): if a saturating transfer still starves
# connectionmonitor's liveness check despite fq_codel, capping rsync's own
# throughput leaves it headroom. Off by default (empty/0 = no limit, same
# as omitting --bwlimit) -- set ARCHIVE_BWLIMIT_KBPS if fq_codel alone
# doesn't fix repeated "connection dead, killing archive-clips" kills.
declare -a bwlimitopt=()
if [ -n "${ARCHIVE_BWLIMIT_KBPS:-}" ] && [ "${ARCHIVE_BWLIMIT_KBPS}" != "0" ]
then
  bwlimitopt=("--bwlimit=${ARCHIVE_BWLIMIT_KBPS}")
fi

while [ -n "${1+x}" ]
do
  if ! (rsync -avhRL --remove-source-files --temp-dir="$rsynctmp" --no-perms --omit-dir-times --stats \
        "${bwlimitopt[@]}" \
        --log-file=/tmp/archive-rsync-cmd.log --ignore-missing-args \
        --files-from="$2" "$1/" "$ARCHIVE_MOUNT" &> /tmp/rsynclog || [[ "$?" = "24" ]] )
  then
    cat /tmp/archive-rsync-cmd.log /tmp/rsynclog > /tmp/archive-error.log
    exit 1
  fi

  shift 2
done

rm -rf "$ARCHIVE_MOUNT/${rsynctmp:?}" || true

kill %1 || true
