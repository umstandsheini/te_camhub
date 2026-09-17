#!/usr/bin/env python3
"""Prune SavedClips entries older than N minutes from each event folder.

Input list format is one relative path per line (same as sentry_files):
  SavedClips/<event-folder>/<filename>

For each SavedClips event folder, this script finds clip timestamps from filenames
matching Tesla's naming pattern: YYYY-MM-DD_HH-MM-SS-*.{mp4,MP4}.
It keeps only files whose clip timestamp is within N minutes of the latest
clip timestamp in that same event folder.

Non-SavedClips entries are preserved.
SavedClips entries with no parseable timestamp are preserved.

Ported from marcone/teslausb PR #1033 (andyylin:feature/sync-last-minutes),
unchanged apart from this note -- see hub app settings (sync_savedclips_last_minutes)
for the Hub-side UI wiring, and run/archiveloop for where this gets called.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import PurePosixPath

TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-")


def parse_clip_ts(filename: str) -> dt.datetime | None:
    match = TIMESTAMP_RE.match(filename)
    if not match:
        return None
    try:
        return dt.datetime.strptime(match.group(1), "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", required=True, help="Path to sentry_files list")
    parser.add_argument(
        "--minutes",
        type=int,
        required=True,
        help="Minutes of SavedClips footage to keep per event folder",
    )
    parser.add_argument(
        "--removed-list",
        help="Optional path to write filtered-out SavedClips entries",
    )
    args = parser.parse_args()

    if args.minutes < 0:
        raise SystemExit("--minutes must be >= 0")

    list_path = args.list
    with open(list_path, "r", encoding="utf-8") as f:
        lines = [line.rstrip("\n") for line in f]

    removed_lines: list[str] = []
    if args.minutes == 0:
        if args.removed_list:
            with open(args.removed_list, "w", encoding="utf-8") as f:
                for line in removed_lines:
                    f.write(f"{line}\n")
        return 0

    max_ts_by_event: dict[str, dt.datetime] = {}
    parsed_ts_by_path: dict[str, dt.datetime] = {}

    for relpath in lines:
        path = PurePosixPath(relpath)
        if len(path.parts) < 3 or path.parts[0] != "SavedClips":
            continue

        ts = parse_clip_ts(path.name)
        if ts is None:
            continue

        event_key = "/".join(path.parts[:2])
        parsed_ts_by_path[relpath] = ts
        cur_max = max_ts_by_event.get(event_key)
        if cur_max is None or ts > cur_max:
            max_ts_by_event[event_key] = ts

    out_lines: list[str] = []
    for relpath in lines:
        path = PurePosixPath(relpath)

        if len(path.parts) < 3 or path.parts[0] != "SavedClips":
            out_lines.append(relpath)
            continue

        ts = parsed_ts_by_path.get(relpath)
        if ts is None:
            # Keep non-clip/non-standard files to avoid data loss.
            out_lines.append(relpath)
            continue

        event_key = "/".join(path.parts[:2])
        max_ts = max_ts_by_event.get(event_key)
        if max_ts is None:
            out_lines.append(relpath)
            continue

        cutoff = max_ts - dt.timedelta(minutes=args.minutes)
        if ts >= cutoff:
            out_lines.append(relpath)
        else:
            removed_lines.append(relpath)

    with open(list_path, "w", encoding="utf-8") as f:
        for line in out_lines:
            f.write(f"{line}\n")

    if args.removed_list:
        with open(args.removed_list, "w", encoding="utf-8") as f:
            for line in removed_lines:
                f.write(f"{line}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
