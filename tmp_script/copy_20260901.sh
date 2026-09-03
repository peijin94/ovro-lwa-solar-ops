#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

exec python3 "${script_dir}/copy_event_ms.py" \
    2026-09-01T21:00:00Z \
    2026-09-01T21:50:00Z \
    --name Type3pol5 \
    "$@"
