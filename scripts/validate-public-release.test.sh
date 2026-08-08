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
  rm -rf "$fixture/.git"
  python3 - "$fixture" "$name" <<'PY'
import json
from pathlib import Path
import sys

root, name = Path(sys.argv[1]), sys.argv[2]
if name == "cowork-command":
    path = root / "plugins/faber-cowork/.mcp.json"
    value = json.loads(path.read_text())
    value["mcpServers"]["faber"]["command"] = "sh"
elif name == "cowork-hook":
    path = root / "plugins/faber-cowork/hooks/hooks.json"
    value = json.loads(path.read_text())
    value["hooks"]["Stop"][0]["hooks"][0]["type"] = "command"
    value["hooks"]["Stop"][0]["hooks"][0]["command"] = "sh"
elif name == "candidate-binding":
    for relative in (
        "plugins/faber-claude-code/skills/faber/SKILL.md",
        "plugins/faber-cowork/skills/faber/SKILL.md",
    ):
        path = root / relative
        path.write_text(path.read_text() + "\n")
    raise SystemExit(0)
elif name == "email-identity":
    path = root / "README.md"
    path.write_text(path.read_text() + "\n" + "release-owner" + chr(64) + "example.org\n")
    raise SystemExit(0)
elif name == "binary-email-identity":
    path = root / "plugins/faber-claude-code/bin/faber-companion_linux_amd64"
    path.write_bytes(path.read_bytes() + b"\0release-owner" + bytes((64,)) + b"example.org\0")
    raise SystemExit(0)
elif name == "binary-vcs-identity":
    path = root / "plugins/faber-claude-code/bin/faber-companion_linux_amd64"
    path.write_bytes(
        path.read_bytes() + b"\0github" + b".com/example-owner/example-repository\0"
    )
    raise SystemExit(0)
elif name == "binary-allowlist-prefix":
    path = root / "plugins/faber-claude-code/bin/faber-companion_linux_amd64"
    path.write_bytes(
        path.read_bytes() + b"\0github" + b".com/godbus/dbus-private/private-repository\0"
    )
    raise SystemExit(0)
elif name == "binary-allowlist-subpath":
    path = root / "plugins/faber-claude-code/bin/faber-companion_linux_amd64"
    path.write_bytes(
        path.read_bytes() + b"\0github" + b".com/godbus/dbus/private-repository\0"
    )
    raise SystemExit(0)
elif name == "binary-allowlist-dot-extension":
    path = root / "plugins/faber-claude-code/bin/faber-companion_linux_amd64"
    path.write_bytes(
        path.read_bytes() + b"\0github" + b".com/godbus/dbus/v5.private\0"
    )
    raise SystemExit(0)
elif name == "generic-git-url":
    path = root / "README.md"
    path.write_text(
        path.read_text() + "\nhttps://git.corp.example/team/" + "private-repository.git\n"
    )
    raise SystemExit(0)
elif name == "backslash-vcs-identity":
    path = root / "README.md"
    path.write_text(
        path.read_text() + "\nhttps://github" + ".com\\example-owner\\example-repository\n"
    )
    raise SystemExit(0)
elif name == "encoded-email-identity":
    path = root / "README.md"
    path.write_text(path.read_text() + "\nprivate.owner&#" + "64;example.org\n")
    raise SystemExit(0)
elif name == "content-host-identity":
    path = root / "README.md"
    path.write_text(
        path.read_text()
        + "\nhttps://raw"
        + ".githubuser"
        + "content.com/example-owner/example-repository/main/file\n"
    )
    raise SystemExit(0)
elif name == "json-escaped-identity":
    path = root / "plugins/faber-claude-code/.claude-plugin/plugin.json"
    value = json.loads(path.read_text())
    value["contact"] = "__ENCODED_IDENTITY__"
    content = json.dumps(value).replace("__ENCODED_IDENTITY__", r"private.owner\u0040example.org")
    path.write_text(content)
    raise SystemExit(0)
elif name == "manifest-author-identity":
    path = root / "plugins/faber-claude-code/.claude-plugin/plugin.json"
    value = json.loads(path.read_text())
    value["author"] = {"name": "Release Owner"}
else:
    raise SystemExit(f"unknown fixture: {name}")
path.write_text(json.dumps(value, indent=2) + "\n")
PY
  if "$fixture/scripts/validate-public-release.sh" "$fixture" >"$temporary/$name.out" 2>&1; then
    echo "validator accepted $name" >&2
    exit 1
  fi
  grep -Fq "$expected" "$temporary/$name.out"
}

test_rejection cowork-command 'Cowork starts a local command'
test_rejection cowork-hook 'Cowork hooks must be prompt-only'
test_rejection candidate-binding 'public plugin contents do not match CANDIDATE'
test_rejection email-identity 'contains a non-service email identity'
test_rejection binary-email-identity 'contains a non-service email identity'
test_rejection binary-vcs-identity 'contains prohibited identity or build metadata'
test_rejection binary-allowlist-prefix 'contains prohibited identity or build metadata'
test_rejection binary-allowlist-subpath 'contains prohibited identity or build metadata'
test_rejection binary-allowlist-dot-extension 'contains prohibited identity or build metadata'
test_rejection generic-git-url 'contains prohibited identity or build metadata'
test_rejection backslash-vcs-identity 'contains prohibited identity or build metadata'
test_rejection encoded-email-identity 'contains a non-service email identity'
test_rejection content-host-identity 'contains prohibited identity or build metadata'
test_rejection json-escaped-identity 'contains a non-service email identity'
test_rejection manifest-author-identity 'author identity is not canonical'

echo "public release validation tests passed"
