#!/usr/bin/env bash
set -euo pipefail
python scripts/fetch_rakuten.py
python scripts/build_site.py
python scripts/validate.py
