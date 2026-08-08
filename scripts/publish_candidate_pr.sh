#!/usr/bin/env bash
set -euo pipefail

version="${1:-}"
candidate_id="${2:-}"
[[ "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || {
  echo "release version must be stable SemVer" >&2
  exit 2
}
[[ "$candidate_id" =~ ^[0-9a-f]{64}$ ]] || {
  echo "candidate ID must be an opaque SHA-256 digest" >&2
  exit 2
}
tree_version="$(tr -d '[:space:]' < VERSION)"
tree_candidate="$(tr -d '[:space:]' < CANDIDATE)"
[[ "$version" == "$tree_version" ]] || {
  echo "release version does not match the validated tree" >&2
  exit 1
}
[[ "$candidate_id" == "$tree_candidate" ]] || {
  echo "candidate ID does not match the validated tree" >&2
  exit 1
}
for command in gh git jq; do
  command -v "$command" >/dev/null 2>&1 || { echo "missing required command: $command" >&2; exit 1; }
done
scripts/validate-public-release.sh .

branch="release/v$version"
branch_ref="refs/"
branch_ref+="heads/$branch"
open_releases="$(gh pr list --state open --json headRefName --jq '[.[].headRefName | select(startswith("release/v"))] | .[]')"
while IFS= read -r open_branch; do
  [[ -z "$open_branch" || "$open_branch" == "$branch" ]] || {
    echo "Another plugin release PR is already open: $open_branch" >&2
    exit 1
  }
done <<< "$open_releases"

pr="$(gh pr list --state open --head "$branch" --json number,isDraft --jq '.[0] // empty')"
if [[ -n "$pr" && "$(printf '%s' "$pr" | jq -r .isDraft)" != true ]]; then
  echo "Existing release PR is no longer a draft" >&2
  exit 1
fi

existing="$(git ls-remote --heads origin "$branch_ref" | awk '{print $1}')"
git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com
git switch -C "$branch"
git add --all
git diff --cached --quiet && { echo "candidate produced no repository changes" >&2; exit 1; }
git commit -m "Release Faber MCP plugins v$version"
if [[ -n "$existing" ]]; then
  git push --force-with-lease="$branch_ref:$existing" origin "$branch"
else
  git push --set-upstream origin "$branch"
fi

body="$(mktemp)"
trap 'rm -f "$body"' EXIT
cat > "$body" <<EOF
## What

Release Faber MCP plugins v$version from candidate \`$candidate_id\`.

## Why

A new sanitized plugin candidate is available from the deployed Faber service.

## How

Pulled the allowlisted candidate over HTTPS, verified its checksums and content digest, stamped the next patch version, and regenerated the public packages.

## Test

- Candidate archive safety and exact-content validation passed.
- Binary metadata, checksums, package versions, and identity-leak scans passed.
EOF

if [[ -z "$pr" ]]; then
  gh pr create --base main --head "$branch" --title "Release Faber MCP plugins v$version" --body-file "$body" --draft
else
  number="$(printf '%s' "$pr" | jq -r .number)"
  gh pr edit "$number" --title "Release Faber MCP plugins v$version" --body-file "$body"
fi
