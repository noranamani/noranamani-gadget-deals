#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# This VM is the fixed-IP data collector. Always start from the current main
# branch, fetch fresh Rakuten data, validate the data files, and publish them.
# Public HTML is rebuilt later by rebuild_from_editorial.sh after editorial
# content has been refreshed.
git fetch origin main
git reset --hard origin/main

python3 scripts/fetch_rakuten.py
python3 scripts/validate_data.py
bash scripts/publish.sh
