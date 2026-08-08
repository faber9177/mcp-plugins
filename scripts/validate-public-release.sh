#!/usr/bin/env bash
set -euo pipefail

root="$(cd "${1:-.}" && pwd)"
validator_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() {
  echo "Faber public plugin validation failed: $*" >&2
  exit 1
}

for command in file go grep python3 strings; do
  command -v "$command" >/dev/null 2>&1 || fail "missing required command: $command"
done

candidate="$(tr -d '[:space:]' < "$root/CANDIDATE")"
[[ "$candidate" =~ ^[0-9a-f]{64}$ ]] || fail "CANDIDATE is not an opaque SHA-256 digest"
legacy_candidate='97de49b7154c3ea9819853e79ffb8170ba8fbec0017d29ca082e68efb254b959'

expected_files="$({
  printf '%s\n' \
    .claude-plugin/marketplace.json \
    .github/workflows/pull-candidate.yml \
    CANDIDATE \
    LICENSE \
    README.md \
    VERSION \
    plugins/faber-claude-code/.claude-plugin/plugin.json \
    plugins/faber-claude-code/README.md \
    plugins/faber-claude-code/SETUP.md \
    plugins/faber-claude-code/SHA256SUMS \
    plugins/faber-claude-code/VERSION \
    plugins/faber-claude-code/bin/faber-companion_darwin_amd64 \
    plugins/faber-claude-code/bin/faber-companion_darwin_arm64 \
    plugins/faber-claude-code/bin/faber-companion_linux_amd64 \
    plugins/faber-claude-code/bin/faber-companion_linux_arm64 \
    plugins/faber-claude-code/hooks/hooks.json \
    plugins/faber-claude-code/scripts/launch-companion.sh \
    plugins/faber-claude-code/skills/faber/SKILL.md \
    plugins/faber-cowork/.claude-plugin/plugin.json \
    plugins/faber-cowork/.mcp.json \
    plugins/faber-cowork/README.md \
    plugins/faber-cowork/VERSION \
    plugins/faber-cowork/hooks/hooks.json \
    plugins/faber-cowork/skills/faber/SKILL.md \
    scripts/publish_candidate_pr.sh \
    scripts/publish_candidate_pr_test.sh \
    scripts/pull_candidate.py \
    scripts/pull_candidate_test.py \
    scripts/validate-public-release.test.sh \
    scripts/validate-public-release.sh
  if [[ "$candidate" != "$legacy_candidate" ]]; then
    printf '%s\n' plugins/faber-claude-code/tools/catalog.json
  fi
} | LC_ALL=C sort)"

actual_files="$(
  cd "$root"
  find . -path './.git' -prune -o -type f -print | sed 's#^./##' | LC_ALL=C sort
)"
[[ "$actual_files" == "$expected_files" ]] || {
  diff -u <(printf '%s\n' "$expected_files") <(printf '%s\n' "$actual_files") >&2 || true
  fail "repository contains missing or unexpected files"
}
if find "$root" -path "$root/.git" -prune -o -type l -print | grep -q .; then
  fail "repository contains a symbolic link"
fi

version="$(tr -d '[:space:]' < "$root/VERSION")"
[[ "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || fail "VERSION is not stable SemVer"

code_root="$root/plugins/faber-claude-code"
cowork_root="$root/plugins/faber-cowork"
python3 - "$version" "$code_root" "$cowork_root" <<'PY'
import json
from pathlib import Path
import sys

version, code_root, cowork_root = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
marketplace = json.loads(code_root.parents[1].joinpath(".claude-plugin/marketplace.json").read_text())
if marketplace.get("owner") != {"name": "Faber"}:
    raise SystemExit("Marketplace owner identity is not canonical")
if code_root.joinpath("VERSION").read_text().strip() != version:
    raise SystemExit("Claude Code VERSION disagrees")
if cowork_root.joinpath("VERSION").read_text().strip() != version:
    raise SystemExit("Cowork VERSION disagrees")
code_manifest = json.loads(code_root.joinpath(".claude-plugin/plugin.json").read_text())
cowork_manifest = json.loads(cowork_root.joinpath(".claude-plugin/plugin.json").read_text())
if code_manifest["version"] != version:
    raise SystemExit("Claude Code manifest version disagrees")
if cowork_manifest["version"] != version:
    raise SystemExit("Cowork manifest version disagrees")
for product, manifest in (("Claude Code", code_manifest), ("Cowork", cowork_manifest)):
    if manifest.get("author") != {"name": "Faber"}:
        raise SystemExit(f"{product} author identity is not canonical")
    if manifest.get("repository") != "https://github.com/faber9177/mcp-plugins":
        raise SystemExit(f"{product} repository identity is not canonical")
    if manifest.get("homepage") != "https://www.getfaber.app":
        raise SystemExit(f"{product} homepage identity is not canonical")
config = json.loads(cowork_root.joinpath(".mcp.json").read_text())
if set(config.get("mcpServers", {})) != {"faber"}:
    raise SystemExit("Cowork must configure only the hosted Faber MCP server")
mcp = config["mcpServers"]["faber"]
if mcp["url"] != "https://www.getfaber.app/mcp":
    raise SystemExit("Cowork MCP URL is not hosted Faber")
if mcp["headers"]["X-Faber-Product"] != "claude-cowork":
    raise SystemExit("Cowork product header is missing")
if mcp["headers"]["X-Faber-Product-Version"] != version:
    raise SystemExit("Cowork product version disagrees")

def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)

if any("command" in value for value in objects(config)):
    raise SystemExit("Cowork starts a local command")
hooks = json.loads(cowork_root.joinpath("hooks/hooks.json").read_text())
hook_types = [value["type"] for value in objects(hooks) if "type" in value]
if not hook_types or any(value != "prompt" for value in hook_types):
    raise SystemExit("Cowork hooks must be prompt-only")
PY

cmp -s "$code_root/skills/faber/SKILL.md" "$cowork_root/skills/faber/SKILL.md" || fail "packaged Faber skills disagree"
[[ -x "$code_root/scripts/launch-companion.sh" ]] || fail "Claude Code launcher is not executable"
if [[ "$candidate" != "$legacy_candidate" ]]; then
  grep -Fq 'FABER_MCP_TOOL_CATALOG="$plugin_root/tools/catalog.json"' "$code_root/scripts/launch-companion.sh" ||
    fail "Claude Code launcher does not load the packaged tool catalog"
  python3 - "$code_root/tools/catalog.json" <<'PY'
import json
from pathlib import Path
import sys

catalog = json.loads(Path(sys.argv[1]).read_text())
if set(catalog) != {"instructions", "tools"} or not catalog["instructions"]:
    raise SystemExit("Claude Code MCP tool catalog is invalid")
names = [tool.get("name") for tool in catalog["tools"] if isinstance(tool, dict)]
if not names or any(not name for name in names) or len(names) != len(set(names)):
    raise SystemExit("Claude Code MCP tool catalog is invalid")
PY
fi
if find "$cowork_root" -type f -perm -111 | grep -q .; then
  fail "Cowork package contains an executable"
fi

python3 - "$root" <<'PY'
from pathlib import Path
import html
import json
import re
import sys
from urllib.parse import unquote

root = Path(sys.argv[1])
public_distribution_url = b"https://github.com/faber9177/mcp-plugins"
public_distribution_reference = re.compile(
    rb"(?<![A-Za-z0-9:/.%-])"
    + re.escape(public_distribution_url)
    + rb"(?=$|[\x00\s\"'<>}\],)`*_~]|[.,;:!?](?:[\x00\s\"'<>}\],)`*_~]|$))",
    re.IGNORECASE,
)
vcs_reference_pattern = re.compile(
    rb"(?:(?:https?|ssh)://|git@)?(?:[A-Za-z0-9._~%+-]{1,128}@)?"
    rb"(?:(?:[A-Za-z0-9-]{1,63}\.)*github\.com\.?|"
    rb"(?:[A-Za-z0-9-]{1,63}\.)*githubusercontent\.com\.?|"
    rb"(?:[A-Za-z0-9-]{1,63}\.)*gitlab\.com\.?|"
    rb"(?:[A-Za-z0-9-]{1,63}\.)*bitbucket\.org\.?)[/:\\]",
    re.IGNORECASE,
)
generic_git_url_pattern = re.compile(
    rb"(?:https?|ssh|git)://[A-Za-z0-9._~%:/?#\[\]@!$&'()*+,;=\\-]{1,2048}\.git"
    rb"(?=$|[\x00\s\"'<>}\],)`*_~.,;:!?])",
    re.IGNORECASE,
)
dbus_direct_symbols = (
    rb"AuthCookieSha1|AuthExternal|Conn|Connect|ConnectSessionBus|DecodeMessage|"
    rb"DecodeMessageWithFDs|Dial|Error|EscapeBusAddressValue|FormatError|"
    rb"InvalidMessageError|InvalidTypeError|MakeFailedError|MakeNoObjectError|MatchOption|"
    rb"MakeUnknownInterfaceError|MakeUnknownMethodError|MakeVariant|NewConn|"
    rb"NewDefaultHandler|NewDefaultSignalHandler|NewError|Object|ObjectPath|ParseSignature|"
    rb"SessionBus|Signature|SignatureError|SignatureOf|Store|Type|"
    rb"UnescapeBusAddressValue|Variant|alignment|authCookieSha1|authExternal|"
    rb"authReadLine|authWriteLine|computeMethodName|depthCounter|detectEndianness|"
    rb"exportedMethod|fileExists|findMatching|formatMatchOptions|genericTransport|"
    rb"getAllMethods|getHomeDir|getKey|getMethods|getRuntimeDirectory|"
    rb"getSessionBusAddress|getSessionBusPlatformAddress|getSignature|getTransport|"
    rb"getVariantValue|header|hexchar|init|isClosed|isConvertibleTo|isKeyType|isMemberChar|"
    rb"isValidInterface|isValidMember|isVariant|kindsAreCompatible|lck|map|needsEscape|"
    rb"newCallTracker|newConn|newDecoder|newEncoder|newEncoderAtOffset|"
    rb"newExportedIntf|newExportedObject|newIntrospectIntf|newNameTracker|"
    rb"newNonceTcpTransport|newSequenceGenerator|newSerialGenerator|newTcpTransport|"
    rb"newUnixTransport|nullwriter|outputHandler|setDest|sigByteSize|signalChannelData|"
    rb"standardMethodArgumentDecode|"
    rb"store|storeBase|storeInterfaces|storeMap|storeMapIntoInterface|storeMapIntoMap|"
    rb"storeMapIntoVariant|storeSlice|storeSliceIntoInterface|storeSliceIntoSlice|"
    rb"storeSliceIntoVariant|storeStruct|strNeedsEscape|tcpFamily|"
    rb"tryDiscoverDbusSessionBusAddress|typeFor|unixTransport|validSingle"
)
dbus_receivers = (
    rb"Call|Conn|Error|FormatError|InvalidMessageError|InvalidTypeError|Message|"
    rb"Object|ObjectPath|Signature|SignatureError|Type|Variant|authCookieSha1|"
    rb"authExternal|callTracker|decoder|defaultHandler|defaultSignalHandler|"
    rb"depthCounter|encoder|exportedIntf|exportedMethod|exportedObj|genericTransport|"
    rb"header|nameTracker|nullwriter|oobReader|outputHandler|sequenceGenerator|"
    rb"serialGenerator|signalChannelData|unixTransport"
)
dbus_value_receivers = (
    rb"Conn|Error|FormatError|InvalidMessageError|InvalidTypeError|ObjectPath|"
    rb"Signature|SignatureError|Type|Variant|authCookieSha1|authExternal|depthCounter|"
    rb"exportedMethod|genericTransport|header|nullwriter|unixTransport"
)
allowed_binary_vcs_references = (
    re.compile(
        re.escape(b"github" + b".com/godbus/dbus")
        + rb"/v5(?:\.(?:"
        + dbus_direct_symbols
        + rb")(?:\.(?:func\d+|gowrap\d+|deferwrap\d+|init|\d+))*|"
        + rb"\.(?:"
        + dbus_value_receivers
        + rb")\.(?:[A-Z][A-Za-z0-9_]*|format|generateChallenge|getCookie)"
        + rb"(?:\.(?:[A-Z][A-Za-z0-9_]*|func\d+|gowrap\d+|deferwrap\d+))*|"
        + rb"\.\(\*(?:"
        + dbus_receivers
        + rb")\)\.[A-Za-z0-9_]+"
        + rb"(?:\.(?:[A-Z][A-Za-z0-9_]*|func\d+|gowrap\d+|deferwrap\d+))*)?"
        + rb"(?=$|[^A-Za-z0-9._~%+/-])",
        re.IGNORECASE,
    ),
    re.compile(
        re.escape(b"github" + b".com/zalando/go-keyring")
        + rb"(?:\.(?:Delete|Get|Set|init)(?:\.[A-Za-z0-9_]+)*|"
        rb"\.(?:fallbackServiceProvider|macOSXKeychain|secretServiceProvider)"
        rb"\.(?:Delete|DeleteAll|Get|Set|findItem|findServiceItems)(?:\.deferwrap1)?|"
        rb"\.\(\*(?:fallbackServiceProvider|macOSXKeychain|secretServiceProvider)\)"
        rb"\.(?:Delete|DeleteAll|Get|Set|findItem|findServiceItems)"
        rb"(?:\.deferwrap1)?|/secret_service(?:\.(?:NewSecret|NewSecretService|SecretService)|"
        rb"\.\(\*SecretService\)\.[A-Za-z0-9_]+"
        rb"(?:\.(?:func\d+|gowrap\d+|deferwrap\d+))*)?)?"
        + rb"(?=$|[^A-Za-z0-9._~%+/-])",
        re.IGNORECASE,
    ),
)
patterns = (
    vcs_reference_pattern,
    generic_git_url_pattern,
    re.compile(b"/" + b"Users" + rb"/[^/\x00\s]+/"),
    re.compile(b"/" + b"home" + rb"/[^/\x00\s]+/"),
    re.compile(b"/private/" + b"var/folders/"),
    re.compile(rb"[A-Za-z]:\\" + b"Users" + rb"\\", re.IGNORECASE),
    re.compile(b"vcs" + rb"\.(?:revision|time|modified)", re.IGNORECASE),
    re.compile(
        b"SOURCE" + rb"\.json|CHANGE" + rb"LOG\.md|\." + rb"git/",
        re.IGNORECASE,
    ),
)
text_patterns = (
    re.compile(
        b"vcs"
        + rb"\.branch|GITHUB_(?:HEAD_)?REF|CI_COMMIT"
        + rb"_REF|refs/(?:heads|remotes)/",
        re.IGNORECASE,
    ),
    re.compile(rb"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", re.IGNORECASE),
)
email_pattern = re.compile(
    rb'''(?:"[^"\r\n]{1,64}"|[A-Za-z0-9.!#$%&'*+/=?^_`{|}~\[\]-]{1,64})@'''
    rb"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\.[A-Za-z]{2,63}",
    re.IGNORECASE,
)
allowed_emails = {b"41898282+github-actions[bot]@users.noreply.github.com"}
for path in root.rglob("*"):
    if not path.is_file() or ".git" in path.parts or path.name == "CANDIDATE":
        continue
    content = path.read_bytes()
    if "bin" not in path.parts:
        try:
            text_content = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise SystemExit(f"{path.relative_to(root)} is not valid UTF-8: {error}")
        for _ in range(4):
            decoded = html.unescape(unquote(text_content))
            if decoded == text_content:
                break
            text_content = decoded
        else:
            raise SystemExit(f"{path.relative_to(root)} contains excessively nested text encoding")
        if path.suffix == ".json":
            try:
                json_content = json.loads(content)
            except json.JSONDecodeError as error:
                raise SystemExit(f"{path.relative_to(root)} contains invalid JSON: {error}")
            json_strings = []
            pending = [json_content]
            while pending:
                value = pending.pop()
                if isinstance(value, str):
                    json_strings.append(value)
                elif isinstance(value, list):
                    pending.extend(value)
                elif isinstance(value, dict):
                    pending.extend(value.keys())
                    pending.extend(value.values())
            text_content += "\n" + "\n".join(json_strings)
        content = text_content.encode()
    scanned_content = public_distribution_reference.sub(b"", content)
    unexpected_emails = set(email_pattern.findall(scanned_content)) - allowed_emails
    if unexpected_emails:
        raise SystemExit(f"{path.relative_to(root)} contains a non-service email identity")
    if "bin" in path.parts:
        for allowed_reference in allowed_binary_vcs_references:
            scanned_content = allowed_reference.sub(b"", scanned_content)
    if any(pattern.search(scanned_content) for pattern in patterns):
        raise SystemExit(f"{path.relative_to(root)} contains prohibited identity or build metadata")
    if "bin" not in path.parts:
        if any(pattern.search(scanned_content) for pattern in text_patterns):
            raise SystemExit(f"{path.relative_to(root)} contains prohibited identity or build metadata")
PY

python3 "$validator_root/scripts/pull_candidate.py" --verify-public "$root"

for target in darwin_amd64 darwin_arm64 linux_amd64 linux_arm64; do
  binary="$code_root/bin/faber-companion_$target"
  target_os="${target%_*}"
  target_arch="${target#*_}"
  [[ -x "$binary" ]] || fail "missing executable $target companion"
  metadata="$(cd "$code_root" && go version -m "bin/faber-companion_$target" 2>&1)" || fail "cannot inspect $target companion"
  grep -Fq $'path\tgetfaber.app/companion/cmd/faber-companion' <<< "$metadata" || fail "$target companion path is not sanitized"
  grep -Fq $'mod\tgetfaber.app/companion' <<< "$metadata" || fail "$target companion module is not sanitized"
  grep -Fq $'build\tGOOS='"$target_os" <<< "$metadata" || fail "$target companion GOOS is incorrect"
  grep -Fq $'build\tGOARCH='"$target_arch" <<< "$metadata" || fail "$target companion GOARCH is incorrect"
  description="$(file -b "$binary")" || fail "cannot identify $target companion"
  strings "$binary" >/dev/null || fail "cannot inspect strings in $target companion"
  if [[ "$target_os" == darwin ]]; then
    grep -Fq 'Mach-O' <<< "$description" || fail "$target companion is not a Mach-O binary"
  else
    grep -Fq 'ELF' <<< "$description" || fail "$target companion is not an ELF binary"
  fi
done

(
  cd "$code_root"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c SHA256SUMS
  else
    shasum -a 256 -c SHA256SUMS
  fi
) >/dev/null || fail "companion checksums are stale"
if [[ "$candidate" != "$legacy_candidate" ]]; then
  grep -Fq '  tools/catalog.json' "$code_root/SHA256SUMS" || fail "tool catalog checksum is missing"
fi

case "$(uname -s)/$(uname -m)" in
  Darwin/x86_64) host_target=darwin_amd64 ;;
  Darwin/arm64) host_target=darwin_arm64 ;;
  Linux/x86_64) host_target=linux_amd64 ;;
  Linux/aarch64|Linux/arm64) host_target=linux_arm64 ;;
  *) host_target="" ;;
esac
if [[ -n "$host_target" ]]; then
  version_output="$("$code_root/bin/faber-companion_$host_target" version)"
  if [[ "$candidate" == "$legacy_candidate" ]]; then
    grep -Fq '"version":"'"$version"'"' <<< "$version_output" || fail "legacy companion version disagrees"
  else
    grep -Fq '"version":"0.0.0+candidate"' <<< "$version_output" || fail "companion build identity is not sanitized"
  fi
fi

package_kib="$(du -sk "$code_root" | awk '{print $1}')"
(( package_kib < 50 * 1024 )) || fail "Claude Code package exceeds Anthropic's 50 MB limit"

if command -v claude >/dev/null 2>&1; then
  claude plugin validate "$root"
else
  echo "warning: claude CLI not installed; skipped official plugin validation" >&2
fi

echo "Validated public Faber MCP plugins v$version ($candidate)"
