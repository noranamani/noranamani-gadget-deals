#!/usr/bin/env bash
set -euo pipefail

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$branch" != "main" ]]; then
  echo "Expected main branch, got: $branch" >&2
  exit 3
fi

git config user.name "noranamani-rakuten-bot"
git config user.email "noranamani-rakuten-bot@users.noreply.github.com"

git add data docs note
git diff --cached --quiet && { echo "No generated changes to publish."; exit 0; }

git commit -m "chore: daily gadget deals update"
git push origin main

echo "Published to main."
