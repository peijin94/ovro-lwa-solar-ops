# Event MS transfer notes

Last updated: 2026-09-03 UTC

## Purpose

The scripts in `tmp_script/` copy selected OVRO-LWA slow-visibility data from
the Lustre pipeline to the event archive on `/nas8`.

Main script:

```text
tmp_script/copy_event_ms.py
```

Remote source:

```text
calimsolar:/lustre/pipeline/slow/
```

Local destination convention:

```text
/nas8/lwa/event-MS/yyyymmdd[name]/
```

The date is taken from the UTC start time. For example, `--name Type4Burst`
with a start time on 2026-09-01 writes to:

```text
/nas8/lwa/event-MS/20260901Type4Burst/
```

## Verified remote layout

Slow data are organized as:

```text
/lustre/pipeline/slow/<band>/<YYYY-MM-DD>/<HH>/
    YYYYMMDD_HHMMSS_<band>.ms.tar
```

Example:

```text
/lustre/pipeline/slow/55MHz/2026-09-01/20/
    20260901_200004_55MHz.ms.tar
```

The same timestamp was checked and found in 23MHz, 55MHz, and 82MHz.

## Important timestamp behavior

Do not synthesize filenames by assuming timestamps fall exactly on seconds
00, 10, 20, etc. The actual nominal 10-second sequence drifts. A verified
sequence began:

```text
20:00:04, 20:00:15, 20:00:25, 20:00:35, ...
```

Later timestamps included `20:05:36`, `20:05:46`, and similar offsets.

`copy_event_ms.py` therefore performs one SSH listing of the applicable
55MHz hour directories, parses the real filenames, sorts and filters their
timestamps, and uses those exact timestamps for every requested band.

The requested interval is half-open:

```text
[start_ut, end_ut)
```

The start is included; the end is excluded. Input times do not need to be
aligned to a 10-second boundary.

## Cadence selection

Data have a nominal base cadence of 10 seconds. The `--cadence` value must be
a positive multiple of 10. After time-range filtering, the script selects
every `cadence / 10`-th actual 55MHz timestamp:

```text
10 seconds  -> every timestamp
30 seconds  -> every third timestamp
60 seconds  -> every sixth timestamp
120 seconds -> every twelfth timestamp
```

This indexing is intentional: it works with the real drifting timestamps
instead of testing timestamp seconds modulo the requested cadence.

## Default bands and file type

Default bands, in configured order:

```text
23MHz 32MHz 41MHz 50MHz 59MHz 69MHz 78MHz
18MHz 27MHz 36MHz 46MHz 55MHz 64MHz 73MHz 82MHz
```

The default extension is `.ms.tar`. Use `--file-ext .ms` only when the remote
source contains unpacked measurement-set directories. No wildcard matching
is used.

## Efficient transfer procedure

The script first generates a sorted local file list. Its default name is:

```text
flist_transfer.txt
```

Entries are relative to `/lustre/pipeline/slow/`, for example:

```text
23MHz/2026-09-01/21/20260901_210006_23MHz.ms.tar
55MHz/2026-09-01/21/20260901_210006_55MHz.ms.tar
```

It then runs one `rsync` process for the complete transfer:

```bash
rsync -ar --no-relative \
  --partial --append-verify \
  --info=progress2 \
  --files-from=flist_transfer.txt \
  calimsolar:/lustre/pipeline/slow/ \
  /nas8/lwa/event-MS/yyyymmdd[name]/
```

Notes on these options:

- There is no `-z`; compression is intentionally disabled.
- `--partial --append-verify` supports resuming an interrupted transfer and
  verifies the appended data.
- `--no-relative` removes the remote band/date/hour hierarchy. All selected
  files are placed directly in the dated event directory.
- `-r` is explicitly present because `--files-from` changes how `-a` implies
  recursion. This matters when transferring unpacked `.ms` directories.

Rerun the same command or wrapper after an interruption to continue.

## Dry runs

Use `--dry-run` or `--dryrun` to inspect a transfer. A dry run still:

1. Connects to `calimsolar` over SSH to list real 55MHz timestamps.
2. Writes or replaces `flist_transfer.txt`.
3. Prints the single planned `rsync` command.

It does not create the destination directory or execute `rsync`.

Example:

```bash
tmp_script/copy_event_ms.py \
  2026-09-01T20:00:00Z \
  2026-09-01T21:00:00Z \
  --name Type4Burst \
  --cadence 30 \
  --dry-run
```

## Event wrappers

`tmp_script/copy_20260901.sh`

- Time: 2026-09-01 21:00 through 21:50 UTC
- Name: `Type3pol5`
- Defaults to all configured bands, 10-second cadence, and `.ms.tar`
- Destination: `/nas8/lwa/event-MS/20260901Type3pol5/`

`tmp_script/copy_20260902MflareType325large.sh`

- Time: 2026-09-02 19:05 through 23:00 UTC
- Name: `MflareType325large`
- All configured bands
- Full 10-second cadence
- Raw `.ms.tar` data
- Destination: `/nas8/lwa/event-MS/20260902MflareType325large/`

Both wrappers pass additional arguments to `copy_event_ms.py`. For example:

```bash
tmp_script/copy_20260902MflareType325large.sh --dry-run
```

## Operational notes

- The event name must be a single non-empty directory name without `/` or
  backslash characters.
- `flist_transfer.txt` is replaced on each invocation. For concurrent runs,
  give each process a distinct file with `--file-list`.
- Missing remote hour directories are skipped during timestamp discovery.
- If no matching 55MHz timestamps are found, the script writes an empty file
  list and does not run `rsync`.
- Exact filenames are derived from 55MHz. If a selected timestamp is missing
  in another requested band, the single `rsync` command reports that missing
  source path and exits nonzero.
- The remote host can be overridden with `--source-host`, but the operational
  default is `calimsolar`.
