#!/usr/bin/env bash
set -euo pipefail

version="${1:-}"
candidate_id="${2:-}"
repository="${GITHUB_REPOSITORY:-faber9177/mcp-plugins}"
expected_author="github-actions[bot]"
expected_author_id="41898282"

verify_actions_author() {
  local number="$1"
  local description="$2"
  local identity
  identity="$(gh api "repos/$repository/pulls/$number")"
  [[ "$(jq -r .user.login <<< "$identity")" == "$expected_author" \
    && "$(jq -r .user.type <<< "$identity")" == Bot \
    && "$(jq -r .user.id <<< "$identity")" == "$expected_author_id" ]] || {
    echo "$description was not created by $expected_author" >&2
    return 1
  }
}

[[ "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || {
  echo "release version must be stable SemVer" >&2
  exit 2
}
[[ "$candidate_id" =~ ^[0-9a-f]{64}$ ]] || {
  echo "candidate ID must be an opaque SHA-256 digest" >&2
  exit 2
}
[[ "$(tr -d '[:space:]' < VERSION)" == "$version" ]] || {
  echo "release version does not match the validated tree" >&2
  exit 1
}
[[ "$(tr -d '[:space:]' < CANDIDATE)" == "$candidate_id" ]] || {
  echo "candidate ID does not match the validated tree" >&2
  exit 1
}
for command in gh git jq; do
  command -v "$command" >/dev/null 2>&1 || { echo "missing required command: $command" >&2; exit 1; }
done
scripts/validate-public-release.sh .

branch="release/v$version"
branch_ref="refs/heads/$branch"
open_releases="$(gh pr list --state open --json headRefName --jq '[.[].headRefName | select(startswith("release/v"))] | .[]')"
while IFS= read -r open_branch; do
  [[ -z "$open_branch" || "$open_branch" == "$branch" ]] || {
    echo "Another plugin release PR is already open: $open_branch" >&2
    exit 1
  }
done <<< "$open_releases"

pr="$(gh pr list --state open --head "$branch" \
  --json number,isDraft,baseRefName,headRefName,headRepository \
  --jq '.[0] // empty')"
if [[ -n "$pr" ]]; then
  verify_actions_author "$(jq -r .number <<< "$pr")" "Existing release PR"
  [[ "$(jq -r .baseRefName <<< "$pr")" == main ]] || {
    echo "Existing release PR does not target main" >&2
    exit 1
  }
  [[ "$(jq -r .headRefName <<< "$pr")" == "$branch" ]] || {
    echo "Existing release PR has an unexpected branch" >&2
    exit 1
  }
  [[ "$(jq -r .headRepository.nameWithOwner <<< "$pr")" == "$repository" ]] || {
    echo "Existing release PR comes from another repository" >&2
    exit 1
  }
fi

existing="$(git ls-remote --heads origin "$branch_ref" | awk '{print $1}')"
if [[ -n "$existing" && -z "$pr" ]]; then
  echo "Release branch already exists without a pipeline-created PR: $branch" >&2
  exit 1
fi
git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com
git switch -C "$branch"
git add --all
git diff --cached --quiet && { echo "candidate produced no repository changes" >&2; exit 1; }
git commit -m "Release Faber MCP plugins v$version"
head_sha="$(git rev-parse HEAD)"
PYTHONDONTWRITEBYTECODE=1 python3 scripts/pull_candidate.py --repo . --verify-revision HEAD
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

A new trusted plugin candidate is available from the deployed Faber service.

## How

Pulled the candidate over HTTPS, verified its checksums, archive safety, and content digest, stamped the next patch version, and mirrored its files.

## Test

- Candidate checksums, archive safety, and exact content digest passed.
- The release commit and candidate metadata were verified before merge.
EOF

if [[ -z "$pr" ]]; then
  gh pr create --base main --head "$branch" --title "Release Faber MCP plugins v$version" --body-file "$body" --draft
else
  number="$(jq -r .number <<< "$pr")"
  gh pr edit "$number" --title "Release Faber MCP plugins v$version" --body-file "$body"
fi

pr="$(gh pr view "$branch" \
  --json number,isDraft,baseRefName,headRefName,headRefOid,headRepository)"
number="$(jq -r .number <<< "$pr")"
verify_actions_author "$number" "Generated release PR"
[[ "$(jq -r .baseRefName <<< "$pr")" == main ]] || {
  echo "Generated release PR does not target main" >&2
  exit 1
}
[[ "$(jq -r .headRefName <<< "$pr")" == "$branch" ]] || {
  echo "Generated release PR has an unexpected branch" >&2
  exit 1
}
[[ "$(jq -r .headRepository.nameWithOwner <<< "$pr")" == "$repository" ]] || {
  echo "Generated release PR comes from another repository" >&2
  exit 1
}
[[ "$(jq -r .headRefOid <<< "$pr")" == "$head_sha" ]] || {
  echo "Generated release PR head changed after validation" >&2
  exit 1
}

git fetch --quiet origin "$branch_ref"
[[ "$(git rev-parse FETCH_HEAD)" == "$head_sha" ]] || {
  echo "Remote release branch changed after validation" >&2
  exit 1
}
[[ "$(git show FETCH_HEAD:VERSION | tr -d '[:space:]')" == "$version" ]] || {
  echo "Remote release version disagrees with the validated tree" >&2
  exit 1
}
[[ "$(git show FETCH_HEAD:CANDIDATE | tr -d '[:space:]')" == "$candidate_id" ]] || {
  echo "Remote candidate ID disagrees with the validated tree" >&2
  exit 1
}
PYTHONDONTWRITEBYTECODE=1 python3 scripts/pull_candidate.py --repo . --verify-revision FETCH_HEAD

if [[ "$(jq -r .isDraft <<< "$pr")" == true ]]; then
  gh pr ready "$number"
fi

mergeable="UNKNOWN"
for _ in $(seq 1 15); do
  merge_check="$(gh pr view "$number" \
    --json baseRefName,headRefName,headRefOid,headRepository,mergeable)"
  [[ "$(jq -r .baseRefName <<< "$merge_check")" == main \
    && "$(jq -r .headRefName <<< "$merge_check")" == "$branch" \
    && "$(jq -r .headRefOid <<< "$merge_check")" == "$head_sha" \
    && "$(jq -r .headRepository.nameWithOwner <<< "$merge_check")" == "$repository" ]] || {
    echo "Generated release PR provenance changed before merge" >&2
    exit 1
  }
  mergeable="$(jq -r .mergeable <<< "$merge_check")"
  [[ "$mergeable" == UNKNOWN ]] || break
  sleep 2
done
[[ "$mergeable" == MERGEABLE ]] || {
  echo "Generated release PR is not mergeable: $mergeable" >&2
  exit 1
}
verify_actions_author "$number" "Generated release PR"

response="$(gh api --method PUT "repos/$repository/pulls/$number/merge" \
  -f sha="$head_sha" \
  -f merge_method=squash \
  -f commit_title="Release Faber MCP plugins v$version")"
[[ "$(jq -r .merged <<< "$response")" == true ]] || {
  echo "GitHub did not merge the generated release PR" >&2
  exit 1
}
if ! git push origin --delete "$branch"; then
  if git ls-remote --exit-code --heads origin "$branch_ref" >/dev/null; then
    echo "Merged release branch could not be deleted" >&2
    exit 1
  else
    lookup_status=$?
    [[ "$lookup_status" -eq 2 ]] || {
      echo "Could not verify merged release branch cleanup" >&2
      exit 1
    }
  fi
fi

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    printf 'pr_number=%s\n' "$number"
    printf 'release_branch=%s\n' "$branch"
    printf 'candidate_id=%s\n' "$candidate_id"
    printf 'head_sha=%s\n' "$head_sha"
  } >> "$GITHUB_OUTPUT"
fi

printf 'Merged Faber MCP plugin release v%s from candidate %s\n' "$version" "$candidate_id"
