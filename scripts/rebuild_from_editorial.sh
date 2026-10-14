#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

git fetch origin main
git reset --hard origin/main

python3 scripts/build_site.py
python3 scripts/validate.py

if ! python3 scripts/build_note_article.py; then
  echo "WARNING: weekly note bundle generation failed; continuing site publish." >&2
fi

bash scripts/publish.sh
