#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d "${TMPDIR:-/tmp}/faber-public-pr-test.XXXXXX")"
trap 'rm -rf "$temporary"' EXIT
fake_bin="$temporary/bin"
mkdir "$fake_bin"
version="$(tr -d '[:space:]' < "$root/VERSION")"
candidate_id="$(tr -d '[:space:]' < "$root/CANDIDATE")"
branch="release/v$version"
export FAKE_VERSION="$version"

cat > "$fake_bin/git" <<'SH'
#!/bin/sh
printf 'git %s\n' "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "ls-remote --heads")
    if [ -n "${FAKE_EXISTING_SHA:-}" ]; then
      branch_ref='refs/'
      branch_ref="${branch_ref}heads/release/v${FAKE_VERSION}"
      printf '%s\t%s\n' "$FAKE_EXISTING_SHA" "$branch_ref"
    fi
    ;;
  "diff --cached") exit 1 ;;
esac
SH
cat > "$fake_bin/gh" <<'SH'
#!/bin/sh
printf 'gh %s\n' "$*" >> "$FAKE_LOG"
case "$*" in
  *"--json headRefName"*) printf '%s' "${FAKE_OPEN_RELEASES:-}" ;;
  *"--json number,isDraft"*) printf '%s' "${FAKE_PR:-}" ;;
esac
SH
chmod +x "$fake_bin/git" "$fake_bin/gh"

log="$temporary/calls"
FAKE_LOG="$log" PATH="$fake_bin:$PATH" "$root/scripts/publish_candidate_pr.sh" "$version" "$candidate_id"
grep -Fq "git push --set-upstream origin $branch" "$log"
grep -Fq "gh pr create --base main --head $branch" "$log"

: > "$log"
if FAKE_LOG="$log" PATH="$fake_bin:$PATH" \
  "$root/scripts/publish_candidate_pr.sh" 9.9.9 "$candidate_id" >"$temporary/version.out" 2>&1; then
  echo "publisher accepted a mismatched tree version" >&2
  exit 1
fi
grep -Fq 'release version does not match the validated tree' "$temporary/version.out"
if grep -Eq '^(git|gh) ' "$log"; then
  echo "publisher used GitHub before rejecting a mismatched version" >&2
  exit 1
fi

: > "$log"
if FAKE_LOG="$log" PATH="$fake_bin:$PATH" \
  "$root/scripts/publish_candidate_pr.sh" "$version" "$(printf 'b%.0s' {1..64})" >"$temporary/candidate.out" 2>&1; then
  echo "publisher accepted a mismatched candidate ID" >&2
  exit 1
fi
grep -Fq 'candidate ID does not match the validated tree' "$temporary/candidate.out"
if grep -Eq '^(git|gh) ' "$log"; then
  echo "publisher used GitHub before rejecting a mismatched candidate" >&2
  exit 1
fi

: > "$log"
if FAKE_LOG="$log" FAKE_OPEN_RELEASES=release/v0.1.9 PATH="$fake_bin:$PATH" \
  "$root/scripts/publish_candidate_pr.sh" "$version" "$candidate_id" >"$temporary/conflict.out" 2>&1; then
  echo "publisher accepted a conflicting release PR" >&2
  exit 1
fi
grep -Fq 'Another plugin release PR is already open' "$temporary/conflict.out"
if grep -Fq 'git push' "$log"; then
  echo "publisher pushed before rejecting the conflict" >&2
  exit 1
fi

: > "$log"
existing_sha="$(printf '1%.0s' {1..40})"
FAKE_LOG="$log" \
FAKE_OPEN_RELEASES="$branch" \
FAKE_EXISTING_SHA="$existing_sha" \
FAKE_PR='{"number":7,"isDraft":true}' \
PATH="$fake_bin:$PATH" \
  "$root/scripts/publish_candidate_pr.sh" "$version" "$candidate_id"
expected_ref='refs/'
expected_ref+="heads/$branch"
grep -Fq "git push --force-with-lease=$expected_ref:$existing_sha origin $branch" "$log"
grep -Fq "gh pr edit 7 --title Release Faber MCP plugins v$version" "$log"

: > "$log"
if FAKE_LOG="$log" \
  FAKE_OPEN_RELEASES="$branch" \
  FAKE_PR='{"number":7,"isDraft":false}' \
  PATH="$fake_bin:$PATH" \
  "$root/scripts/publish_candidate_pr.sh" "$version" "$candidate_id" >"$temporary/ready.out" 2>&1; then
  echo "publisher updated a non-draft release PR" >&2
  exit 1
fi
grep -Fq 'Existing release PR is no longer a draft' "$temporary/ready.out"
if grep -Fq 'git push' "$log"; then
  echo "publisher pushed before checking draft state" >&2
  exit 1
fi

echo "public release PR tests passed"
