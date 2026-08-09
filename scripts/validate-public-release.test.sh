#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d "${TMPDIR:-/tmp}/faber-public-validation-test.XXXXXX")"
trap 'rm -rf "$temporary"' EXIT

test_rejection() {
  name="$1"
  expected="$2"
  fixture="$temporary/$name"
  mkdir "$fixture"
  cp -R "$root/." "$fixture/"
  rm -rf "$fixture/.git" "$fixture/.codex"
  case "$name" in
    candidate-binding)
      printf '\n' >> "$fixture/plugins/faber-cowork/hooks/hooks.json"
      ;;
    unsafe-state)
      python3 - "$fixture/CANDIDATE_FILES" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
value = json.loads(path.read_text())
value["files"][0]["path"] = "scripts/overwrite.sh"
path.write_text(json.dumps(value))
PY
      ;;
    symlink)
      path="$fixture/plugins/faber-cowork/hooks/hooks.json"
      rm "$path"
      ln -s "$fixture/README.md" "$path"
      ;;
    mode)
      chmod 755 "$fixture/plugins/faber-cowork/hooks/hooks.json"
      ;;
    version)
      printf '9.9.9\n' > "$fixture/VERSION"
      ;;
    package-version)
      printf '9.9.9\n' > "$fixture/plugins/faber-cowork/VERSION"
      ;;
    package-checksums)
      printf '%064d  bin/faber-companion_linux_amd64\n' 0 > \
        "$fixture/plugins/faber-claude-code/SHA256SUMS"
      ;;
    retired-metadata)
      mkdir -p "$fixture/plugins/faber-retired"
      printf '0.1.4\n' > "$fixture/plugins/faber-retired/VERSION"
      ;;
    *)
      echo "unknown fixture: $name" >&2
      exit 1
      ;;
  esac
  if "$fixture/scripts/validate-public-release.sh" "$fixture" >"$temporary/$name.out" 2>&1; then
    echo "validator accepted $name" >&2
    exit 1
  fi
  grep -Fq "$expected" "$temporary/$name.out"
}

"$root/scripts/validate-public-release.sh" "$root"
test_rejection candidate-binding 'public plugin contents do not match CANDIDATE'
test_rejection unsafe-state 'unsafe or duplicate path'
test_rejection symlink 'public repository contains a symbolic link'
test_rejection mode 'file mode disagrees'
test_rejection version 'does not contain the public version'
test_rejection package-version 'public package VERSION disagrees'
test_rejection package-checksums 'public package SHA256SUMS disagrees'
test_rejection retired-metadata 'public package metadata does not match current candidate packages'

echo "public release validation tests passed"
