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
head_sha="$(printf 'a%.0s' {1..40})"
export FAKE_VERSION="$version" FAKE_CANDIDATE="$candidate_id" FAKE_HEAD_SHA="$head_sha"
export REAL_PYTHON3="$(command -v python3)"

cat > "$fake_bin/git" <<'SH'
#!/bin/sh
printf 'git %s\n' "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "ls-remote --heads")
    if [ -n "${FAKE_EXISTING_SHA:-}" ]; then
      printf '%s\trefs/heads/release/v%s\n' "$FAKE_EXISTING_SHA" "$FAKE_VERSION"
    fi
    ;;
  "ls-remote --exit-code")
    if [ -n "${FAKE_BRANCH_LOOKUP_ERROR:-}" ]; then
      exit 1
    fi
    if [ -n "${FAKE_BRANCH_PRESENT_AFTER_MERGE:-}" ]; then
      printf '%s\trefs/heads/release/v%s\n' "$FAKE_HEAD_SHA" "$FAKE_VERSION"
      exit 0
    fi
    exit 2
    ;;
  "push origin")
    if [ "${3:-}" = "--delete" ] && [ -n "${FAKE_DELETE_FAIL:-}" ]; then
      exit 1
    fi
    ;;
  "diff --cached") exit 1 ;;
  "rev-parse HEAD") printf '%s\n' "$FAKE_HEAD_SHA" ;;
  "rev-parse FETCH_HEAD") printf '%s\n' "${FAKE_REMOTE_SHA:-$FAKE_HEAD_SHA}" ;;
  "show FETCH_HEAD:VERSION") printf '%s\n' "${FAKE_REMOTE_VERSION:-$FAKE_VERSION}" ;;
  "show FETCH_HEAD:CANDIDATE") printf '%s\n' "${FAKE_REMOTE_CANDIDATE:-$FAKE_CANDIDATE}" ;;
esac
SH
cat > "$fake_bin/python3" <<'SH'
#!/bin/sh
printf 'python3 %s\n' "$*" >> "$FAKE_LOG"
case "$*" in
  *"--verify-revision"*) exit 0 ;;
  *) exec "$REAL_PYTHON3" "$@" ;;
esac
SH
cat > "$fake_bin/gh" <<'SH'
#!/bin/sh
printf 'gh %s\n' "$*" >> "$FAKE_LOG"
case "$*" in
  "pr list --state open --json headRefName"*)
    printf '%s' "${FAKE_OPEN_RELEASES:-}"
    ;;
  "pr list --state open --head "*)
    printf '%s' "${FAKE_PR:-}"
    ;;
  "pr view "*)
    if [ -n "${FAKE_VIEW:-}" ]; then
      printf '%s' "$FAKE_VIEW"
    else
      printf '{"number":7,"isDraft":true,"author":{"login":"github-actions[bot]"},"baseRefName":"main","headRefName":"release/v%s","headRefOid":"%s","headRepository":{"nameWithOwner":"faber9177/mcp-plugins"},"mergeable":"MERGEABLE"}' "$FAKE_VERSION" "$FAKE_HEAD_SHA"
    fi
    ;;
  "api --method PUT "*)
    printf '%s' "${FAKE_MERGE_RESPONSE:-{\"merged\":true}}"
    ;;
esac
SH
chmod +x "$fake_bin/git" "$fake_bin/python3" "$fake_bin/gh"

run_publisher() {
  FAKE_LOG="$1" GITHUB_OUTPUT="${2:-}" PATH="$fake_bin:$PATH" \
    "$root/scripts/publish_candidate_pr.sh" "$version" "$candidate_id"
}

log="$temporary/calls"
outputs="$temporary/outputs"
run_publisher "$log" "$outputs"
grep -Fq "git push --set-upstream origin $branch" "$log"
grep -Fq 'python3 scripts/pull_candidate.py --repo . --verify-revision HEAD' "$log"
grep -Fq 'python3 scripts/pull_candidate.py --repo . --verify-revision FETCH_HEAD' "$log"
grep -Fq "gh pr create --base main --head $branch" "$log"
grep -Fq 'gh pr ready 7' "$log"
grep -Fq "gh api --method PUT repos/faber9177/mcp-plugins/pulls/7/merge -f sha=$head_sha -f merge_method=squash" "$log"
grep -Fq "git push origin --delete $branch" "$log"
grep -Fq 'pr_number=7' "$outputs"
grep -Fq "release_branch=$branch" "$outputs"
grep -Fq "candidate_id=$candidate_id" "$outputs"
grep -Fq "head_sha=$head_sha" "$outputs"

: > "$log"
existing_sha="$(printf '1%.0s' {1..40})"
existing_pr="$(printf '{"number":7,"isDraft":false,"author":{"login":"github-actions[bot]"},"baseRefName":"main","headRefName":"%s","headRepository":{"nameWithOwner":"faber9177/mcp-plugins"}}' "$branch")"
existing_view="$(printf '{"number":7,"isDraft":false,"author":{"login":"github-actions[bot]"},"baseRefName":"main","headRefName":"%s","headRefOid":"%s","headRepository":{"nameWithOwner":"faber9177/mcp-plugins"},"mergeable":"MERGEABLE"}' "$branch" "$head_sha")"
FAKE_LOG="$log" FAKE_OPEN_RELEASES="$branch" FAKE_EXISTING_SHA="$existing_sha" \
FAKE_PR="$existing_pr" FAKE_VIEW="$existing_view" PATH="$fake_bin:$PATH" \
  "$root/scripts/publish_candidate_pr.sh" "$version" "$candidate_id"
grep -Fq "git push --force-with-lease=refs/heads/$branch:$existing_sha origin $branch" "$log"
grep -Fq 'gh pr edit 7' "$log"
if grep -Fq 'gh pr ready' "$log"; then
  echo "publisher marked an already-ready PR ready again" >&2
  exit 1
fi

assert_rejected() {
  name="$1"
  expected="$2"
  shift 2
  : > "$log"
  if env FAKE_LOG="$log" PATH="$fake_bin:$PATH" "$@" \
    "$root/scripts/publish_candidate_pr.sh" "$version" "$candidate_id" \
    >"$temporary/$name.out" 2>&1; then
    echo "publisher accepted $name" >&2
    exit 1
  fi
  grep -Fq "$expected" "$temporary/$name.out"
}

assert_rejected conflict 'Another plugin release PR is already open' \
  FAKE_OPEN_RELEASES=release/v9.9.9

human_pr="$(printf '{"number":7,"isDraft":true,"author":{"login":"human"},"baseRefName":"main","headRefName":"%s","headRepository":{"nameWithOwner":"faber9177/mcp-plugins"}}' "$branch")"
assert_rejected human-pr 'was not created by github-actions[bot]' \
  FAKE_OPEN_RELEASES="$branch" FAKE_PR="$human_pr"
if grep -Fq 'git push' "$log"; then
  echo "publisher pushed a human-owned release PR" >&2
  exit 1
fi

wrong_base_pr="$(printf '{"number":7,"isDraft":true,"author":{"login":"github-actions[bot]"},"baseRefName":"other","headRefName":"%s","headRepository":{"nameWithOwner":"faber9177/mcp-plugins"}}' "$branch")"
assert_rejected wrong-base 'does not target main' \
  FAKE_OPEN_RELEASES="$branch" FAKE_PR="$wrong_base_pr"

wrong_repo_pr="$(printf '{"number":7,"isDraft":true,"author":{"login":"github-actions[bot]"},"baseRefName":"main","headRefName":"%s","headRepository":{"nameWithOwner":"someone/mcp-plugins"}}' "$branch")"
assert_rejected wrong-repo 'comes from another repository' \
  FAKE_OPEN_RELEASES="$branch" FAKE_PR="$wrong_repo_pr"

wrong_view="$(printf '{"number":7,"isDraft":true,"author":{"login":"github-actions[bot]"},"baseRefName":"main","headRefName":"%s","headRefOid":"%s","headRepository":{"nameWithOwner":"faber9177/mcp-plugins"},"mergeable":"MERGEABLE"}' "$branch" "$(printf 'b%.0s' {1..40})")"
assert_rejected changed-head 'head changed after validation' FAKE_VIEW="$wrong_view"
if grep -Fq 'gh api --method PUT' "$log"; then
  echo "publisher merged a changed PR head" >&2
  exit 1
fi

assert_rejected orphan-branch 'without a pipeline-created PR' \
  FAKE_EXISTING_SHA="$existing_sha"
if grep -Fq 'git push' "$log"; then
  echo "publisher modified an orphaned release branch" >&2
  exit 1
fi

unmergeable_view="$(printf '{"number":7,"isDraft":false,"author":{"login":"github-actions[bot]"},"baseRefName":"main","headRefName":"%s","headRefOid":"%s","headRepository":{"nameWithOwner":"faber9177/mcp-plugins"},"mergeable":"CONFLICTING"}' "$branch" "$head_sha")"
assert_rejected unmergeable 'is not mergeable: CONFLICTING' \
  FAKE_VIEW="$unmergeable_view"
if grep -Fq 'gh api --method PUT' "$log"; then
  echo "publisher tried to merge a conflicting PR" >&2
  exit 1
fi

assert_rejected remote-candidate 'Remote candidate ID disagrees' \
  FAKE_REMOTE_CANDIDATE="$(printf 'b%.0s' {1..64})"
assert_rejected remote-version 'Remote release version disagrees' \
  FAKE_REMOTE_VERSION=9.9.9
assert_rejected merge-failure 'GitHub did not merge' \
  FAKE_MERGE_RESPONSE='{"merged":false}'
if grep -Fq "git push origin --delete $branch" "$log"; then
  echo "publisher deleted the branch after a failed merge" >&2
  exit 1
fi

: > "$log"
FAKE_LOG="$log" FAKE_DELETE_FAIL=1 PATH="$fake_bin:$PATH" \
  "$root/scripts/publish_candidate_pr.sh" "$version" "$candidate_id"
grep -Fq "git ls-remote --exit-code --heads origin refs/heads/$branch" "$log"

assert_rejected branch-cleanup 'Merged release branch could not be deleted' \
  FAKE_DELETE_FAIL=1 FAKE_BRANCH_PRESENT_AFTER_MERGE=1
assert_rejected branch-cleanup-lookup 'Could not verify merged release branch cleanup' \
  FAKE_DELETE_FAIL=1 FAKE_BRANCH_LOOKUP_ERROR=1

: > "$log"
if FAKE_LOG="$log" PATH="$fake_bin:$PATH" \
  "$root/scripts/publish_candidate_pr.sh" 9.9.9 "$candidate_id" \
  >"$temporary/version.out" 2>&1; then
  echo "publisher accepted a mismatched tree version" >&2
  exit 1
fi
grep -Fq 'release version does not match the validated tree' "$temporary/version.out"

: > "$log"
if FAKE_LOG="$log" PATH="$fake_bin:$PATH" \
  "$root/scripts/publish_candidate_pr.sh" "$version" "$(printf 'b%.0s' {1..64})" \
  >"$temporary/candidate.out" 2>&1; then
  echo "publisher accepted a mismatched candidate" >&2
  exit 1
fi
grep -Fq 'candidate ID does not match the validated tree' "$temporary/candidate.out"

echo "public release PR and auto-merge tests passed"
