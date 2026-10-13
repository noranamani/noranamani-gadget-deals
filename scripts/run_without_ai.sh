#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# This VM is a dedicated automation worker. Start every run from the current
# GitHub main branch so a previous interrupted generation cannot block updates.
git fetch origin main
git reset --hard origin/main

python3 scripts/fetch_rakuten.py
python3 scripts/build_site.py
python3 scripts/validate.py
bash scripts/publish.sh
