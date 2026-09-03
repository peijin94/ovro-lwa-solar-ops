#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

exec python3 "${script_dir}/copy_event_ms.py" \
    2026-09-02T19:05:00Z \
    2026-09-02T23:00:00Z \
    --name MflareType325large \
    --cadence 10 \
    --file-ext .ms.tar \
    "$@"
