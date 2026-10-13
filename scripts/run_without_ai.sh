#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

git pull --ff-only origin main
python3 scripts/fetch_rakuten.py
python3 scripts/build_site.py
python3 scripts/validate.py
bash scripts/publish.sh
