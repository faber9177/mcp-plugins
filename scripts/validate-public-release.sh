#!/usr/bin/env bash
set -euo pipefail

root="${1:-.}"
root="$(cd "$root" && pwd)"

for path in VERSION CANDIDATE CANDIDATE_FILES scripts/pull_candidate.py; do
  [[ -f "$root/$path" ]] || {
    echo "public release is missing $path" >&2
    exit 1
  }
done

if find "$root" \
  -path "$root/.git" -prune -o \
  -path "$root/.codex" -prune -o \
  -type l -print | grep -q .; then
  echo "public repository contains a symbolic link" >&2
  exit 1
fi

PYTHONDONTWRITEBYTECODE=1 \
  python3 "$root/scripts/pull_candidate.py" --verify-public "$root"
