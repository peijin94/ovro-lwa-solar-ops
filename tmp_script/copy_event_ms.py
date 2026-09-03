#!/usr/bin/env python3
"""Copy time-selected OVRO-LWA measurement sets from calimsolar with rsync.

The requested time range is half-open: START_UT is included and END_UT is
excluded.  Naive input times are interpreted as UTC; timezone-aware inputs are
converted to UTC.  Actual timestamps are discovered from the remote 55MHz data
rather than synthesized from the requested times.
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_BANDS = [
    "23MHz",
    "32MHz",
    "41MHz",
    "50MHz",
    "59MHz",
    "69MHz",
    "78MHz",
    "18MHz",
    "27MHz",
    "36MHz",
    "46MHz",
    "55MHz",
    "64MHz",
    "73MHz",
    "82MHz",
]

VALID_BANDS = {
    "13MHz",
    "18MHz",
    "23MHz",
    "27MHz",
    "32MHz",
    "36MHz",
    "41MHz",
    "46MHz",
    "50MHz",
    "55MHz",
    "59MHz",
    "64MHz",
    "69MHz",
    "73MHz",
    "78MHz",
    "82MHz",
}

REFERENCE_BAND = "55MHz"


def utc_time(value: str) -> datetime:
    """Parse an ISO or YYYYMMDD_HHMMSS timestamp and return UTC."""
    candidate = value.strip()
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        try:
            parsed = datetime.strptime(candidate, "%Y%m%d_%H%M%S")
        except ValueError as error:
            raise argparse.ArgumentTypeError(
                f"invalid UT time {value!r}; use YYYY-MM-DDTHH:MM:SS "
                "or YYYYMMDD_HHMMSS"
            ) from error

    if parsed.microsecond:
        raise argparse.ArgumentTypeError("times must use whole seconds")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed


def cadence_seconds(value: str) -> int:
    try:
        cadence = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("cadence must be an integer number of seconds") from error
    if cadence <= 0 or cadence % 10:
        raise argparse.ArgumentTypeError("cadence must be a positive multiple of 10 seconds")
    return cadence


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "List actual 55MHz timestamps on calimsolar, select them at the requested "
            "cadence, and copy the matching bands. The range is [START_UT, END_UT)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("start_ut", type=utc_time, help="inclusive starting UTC time")
    parser.add_argument("end_ut", type=utc_time, help="exclusive ending UTC time")
    parser.add_argument(
        "-b",
        "--bands",
        "--band-lst",
        "--band_lst",
        nargs="+",
        default=DEFAULT_BANDS,
        metavar="BAND",
        help="bands to copy",
    )
    parser.add_argument(
        "-c",
        "--cadence",
        type=cadence_seconds,
        default=10,
        metavar="SECONDS",
        help="sampling cadence; must be a positive multiple of 10",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        "--dryrun",
        action="store_true",
        help="perform the SSH listing and print planned rsync commands without copying",
    )
    parser.add_argument("--source-host", default="calimsolar", help="SSH source host")
    parser.add_argument(
        "--source-root",
        default="/lustre/pipeline",
        help="remote pipeline root",
    )
    parser.add_argument(
        "--destination",
        default="/nas8/lwa/event-MS/",
        help="local root destination directory",
    )
    parser.add_argument(
        "--name",
        default="Event",
        help=(
            "descriptive event name appended to the UTC start date, "
            "for example 20260901Type4Burst"
        ),
    )
    parser.add_argument(
        "--file-ext",
        "--file_ext",
        choices=(".ms.tar", ".ms"),
        default=".ms.tar",
        help="source filename extension",
    )
    parser.add_argument(
        "--file-list",
        "--file_list",
        default="flist_transfer.txt",
        help="local rsync --files-from list generated before transfer",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.end_ut <= args.start_ut:
        raise ValueError("END_UT must be later than START_UT")

    unknown = sorted(set(args.bands) - VALID_BANDS)
    if unknown:
        raise ValueError(
            f"unknown band(s): {', '.join(unknown)}; valid bands are "
            f"{', '.join(sorted(VALID_BANDS))}"
        )
    if len(set(args.bands)) != len(args.bands):
        raise ValueError("the band list contains duplicates")
    if (
        not args.name.strip()
        or args.name in {".", ".."}
        or "/" in args.name
        or "\\" in args.name
    ):
        raise ValueError("NAME must be a non-empty directory name without slashes")


def remote_directory(args: argparse.Namespace, timestamp: datetime, band: str) -> str:
    date = timestamp.strftime("%Y-%m-%d")
    hour = timestamp.strftime("%H")
    root = args.source_root.rstrip("/")
    return f"{root}/slow/{band}/{date}/{hour}"


def transfer_relative_path(args: argparse.Namespace, timestamp: datetime, band: str) -> str:
    """Return a path relative to the remote /slow source directory."""
    filename = f"{timestamp.strftime('%Y%m%d_%H%M%S')}_{band}{args.file_ext}"
    date = timestamp.strftime("%Y-%m-%d")
    hour = timestamp.strftime("%H")
    return f"{band}/{date}/{hour}/{filename}"


def reference_hour_directories(args: argparse.Namespace) -> list[str]:
    """Return each remote 55MHz hour directory intersecting the time range."""
    current = args.start_ut.replace(minute=0, second=0, microsecond=0)
    directories = []
    while current < args.end_ut:
        directories.append(remote_directory(args, current, REFERENCE_BAND))
        current += timedelta(hours=1)
    return directories


def list_reference_timestamps(args: argparse.Namespace) -> list[datetime]:
    """List and parse actual timestamps from the remote 55MHz data tree."""
    name_pattern = f"*_{REFERENCE_BAND}{args.file_ext}"
    find_options = [
        "-maxdepth",
        "1",
        "-mindepth",
        "1",
        "-name",
        name_pattern,
        "-printf",
        "%f\\n",
    ]
    find_options_string = shlex.join(find_options)
    remote_commands = []
    for directory in reference_hour_directories(args):
        quoted_directory = shlex.quote(directory)
        remote_commands.append(
            f"if [ -d {quoted_directory} ]; then "
            f"find {quoted_directory} {find_options_string}; fi"
        )
    remote_command = "; ".join(remote_commands)
    listing_command = ["ssh", args.source_host, remote_command]
    print(f"Listing timestamps: {shlex.join(listing_command)}", flush=True)
    result = subprocess.run(listing_command, capture_output=True, text=True, check=False)
    if result.returncode:
        detail = result.stderr.strip() or "no error message"
        raise RuntimeError(f"remote timestamp listing failed: {detail}")

    filename_re = re.compile(
        rf"^(\d{{8}}_\d{{6}})_{re.escape(REFERENCE_BAND + args.file_ext)}$"
    )
    timestamps = set()
    for line in result.stdout.splitlines():
        match = filename_re.fullmatch(line.strip())
        if not match:
            continue
        timestamp = datetime.strptime(match.group(1), "%Y%m%d_%H%M%S").replace(
            tzinfo=timezone.utc
        )
        if args.start_ut <= timestamp < args.end_ut:
            timestamps.add(timestamp)
    return sorted(timestamps)


def main() -> int:
    args = parse_args()
    try:
        validate_args(args)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    try:
        available_timestamps = list_reference_timestamps(args)
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    # Files are recorded at a nominal 10-second cadence, although their seconds
    # are not grid-aligned.  Subsample the real, sorted file sequence rather than
    # manufacturing timestamps that may not exist.
    stride = args.cadence // 10
    timestamps = available_timestamps[::stride]
    print(
        f"Found {len(available_timestamps)} actual {REFERENCE_BAND} timestamps; "
        f"selected {len(timestamps)} at {args.cadence}-second cadence."
    )

    file_list = Path(args.file_list).expanduser()
    if not file_list.parent.is_dir():
        print(
            f"error: file-list parent directory does not exist: {file_list.parent}",
            file=sys.stderr,
        )
        return 1

    transfer_paths = sorted(
        transfer_relative_path(args, timestamp, band)
        for timestamp in timestamps
        for band in args.bands
    )
    contents = "\n".join(transfer_paths)
    if contents:
        contents += "\n"
    try:
        file_list.write_text(contents, encoding="utf-8")
    except OSError as error:
        print(f"error: could not write {file_list}: {error}", file=sys.stderr)
        return 1
    print(f"Wrote {len(transfer_paths)} paths to {file_list}")

    if not timestamps:
        print("No matching timestamps found; nothing to copy.")
        return 0

    event_directory = f"{args.start_ut.strftime('%Y%m%d')}{args.name}"
    destination = Path(args.destination).expanduser() / event_directory

    total = len(timestamps) * len(args.bands)
    mode = "Would copy" if args.dry_run else "Copying"
    print(
        f"{mode} {total} band/time selections "
        f"({len(timestamps)} timestamps x {len(args.bands)} bands) to {destination}"
    )

    if not args.dry_run:
        destination.mkdir(parents=True, exist_ok=True)

    remote_root = f"{args.source_host}:{args.source_root.rstrip('/')}/slow/"
    command = [
        "rsync",
        "-ar",
        "--no-relative",
        "--partial",
        "--append-verify",
        "--info=progress2",
        f"--files-from={file_list}",
        remote_root,
        f"{destination}/",
    ]
    print(shlex.join(command), flush=True)
    if args.dry_run:
        return 0

    result = subprocess.run(command, check=False)
    if result.returncode:
        print(f"rsync failed (exit {result.returncode}).", file=sys.stderr)
        return result.returncode
    print(f"Completed one rsync transfer containing {total} selected paths.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
