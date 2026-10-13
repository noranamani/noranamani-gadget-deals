#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${GITHUB_WRITE_TOKEN:-}" ]]; then
  echo "GITHUB_WRITE_TOKEN is not set" >&2
  exit 2
fi

if git diff --quiet && git diff --cached --quiet; then
  echo "No changes to publish."
  exit 0
fi

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$branch" != "main" ]]; then
  echo "Expected main branch, got: $branch" >&2
  exit 3
fi

remote_url="$(git remote get-url origin)"
if [[ ! "$remote_url" =~ github\.com[:/]([^/]+)/([^/.]+)(\.git)?$ ]]; then
  echo "origin is not a recognized GitHub URL: $remote_url" >&2
  exit 4
fi
owner="${BASH_REMATCH[1]}"
repo="${BASH_REMATCH[2]}"
repo="${repo%.git}"

git config user.name "noranamani-codex-bot"
git config user.email "noranamani-codex-bot@users.noreply.github.com"
git add data docs
git diff --cached --quiet && { echo "No generated changes to publish."; exit 0; }
git commit -m "chore: daily gadget deals update"

askpass="$(mktemp)"
trap 'rm -f "$askpass"' EXIT
cat > "$askpass" <<'ASK'
#!/usr/bin/env bash
case "$1" in
  *Username*) printf '%s\n' "x-access-token" ;;
  *Password*) printf '%s\n' "$GITHUB_WRITE_TOKEN" ;;
  *) printf '\n' ;;
esac
ASK
chmod 700 "$askpass"
GIT_ASKPASS="$askpass" GIT_TERMINAL_PROMPT=0 git push "https://github.com/${owner}/${repo}.git" HEAD:main

echo "Published to main."
