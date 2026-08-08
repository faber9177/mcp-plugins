#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
from urllib.parse import unquote, urlparse
from urllib.request import urlopen


PAYLOAD_FILES = {
    ".claude-plugin/marketplace.json",
    "plugins/faber-claude-code/.claude-plugin/plugin.json",
    "plugins/faber-claude-code/bin/faber-companion_darwin_amd64",
    "plugins/faber-claude-code/bin/faber-companion_darwin_arm64",
    "plugins/faber-claude-code/bin/faber-companion_linux_amd64",
    "plugins/faber-claude-code/bin/faber-companion_linux_arm64",
    "plugins/faber-claude-code/hooks/hooks.json",
    "plugins/faber-claude-code/scripts/launch-companion.sh",
    "plugins/faber-claude-code/skills/faber/SKILL.md",
    "plugins/faber-claude-code/tools/catalog.json",
    "plugins/faber-cowork/.claude-plugin/plugin.json",
    "plugins/faber-cowork/.mcp.json",
    "plugins/faber-cowork/hooks/hooks.json",
    "plugins/faber-cowork/skills/faber/SKILL.md",
}
STAMP_FILES = {
    "plugins/faber-claude-code/.claude-plugin/plugin.json",
    "plugins/faber-cowork/.claude-plugin/plugin.json",
    "plugins/faber-cowork/.mcp.json",
}
EXECUTABLE_PAYLOAD_FILES = {
    "plugins/faber-claude-code/bin/faber-companion_darwin_amd64",
    "plugins/faber-claude-code/bin/faber-companion_darwin_arm64",
    "plugins/faber-claude-code/bin/faber-companion_linux_amd64",
    "plugins/faber-claude-code/bin/faber-companion_linux_arm64",
    "plugins/faber-claude-code/scripts/launch-companion.sh",
}
BINARY_PAYLOAD_FILES = {
    relative for relative in EXECUTABLE_PAYLOAD_FILES if "/bin/" in relative
}
PUBLIC_DISTRIBUTION_URL = b"https://github.com/faber9177/mcp-plugins"
PUBLIC_DISTRIBUTION_REFERENCE = re.compile(
    rb"(?<![A-Za-z0-9:/.%-])"
    + re.escape(PUBLIC_DISTRIBUTION_URL)
    + rb"(?=$|[\x00\s\"'<>}\],)`*_~]|[.,;:!?](?:[\x00\s\"'<>}\],)`*_~]|$))",
    re.IGNORECASE,
)
EMAIL_PATTERN = re.compile(
    rb'''(?:"[^"\r\n]{1,64}"|[A-Za-z0-9.!#$%&'*+/=?^_`{|}~\[\]-]{1,64})@'''
    rb"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\.[A-Za-z]{2,63}",
    re.IGNORECASE,
)
VCS_REFERENCE_PATTERN = re.compile(
    rb"(?:(?:https?|ssh)://|git@)?(?:[A-Za-z0-9._~%+-]{1,128}@)?"
    rb"(?:(?:[A-Za-z0-9-]{1,63}\.)*github\.com\.?|"
    rb"(?:[A-Za-z0-9-]{1,63}\.)*githubusercontent\.com\.?|"
    rb"(?:[A-Za-z0-9-]{1,63}\.)*gitlab\.com\.?|"
    rb"(?:[A-Za-z0-9-]{1,63}\.)*bitbucket\.org\.?)[/:\\]",
    re.IGNORECASE,
)
GENERIC_GIT_URL_PATTERN = re.compile(
    rb"(?:https?|ssh|git)://[A-Za-z0-9._~%:/?#\[\]@!$&'()*+,;=\\-]{1,2048}\.git"
    rb"(?=$|[\x00\s\"'<>}\],)`*_~.,;:!?])",
    re.IGNORECASE,
)
DBUS_DIRECT_SYMBOLS = (
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
DBUS_RECEIVERS = (
    rb"Call|Conn|Error|FormatError|InvalidMessageError|InvalidTypeError|Message|"
    rb"Object|ObjectPath|Signature|SignatureError|Type|Variant|authCookieSha1|"
    rb"authExternal|callTracker|decoder|defaultHandler|defaultSignalHandler|"
    rb"depthCounter|encoder|exportedIntf|exportedMethod|exportedObj|genericTransport|"
    rb"header|nameTracker|nullwriter|oobReader|outputHandler|sequenceGenerator|"
    rb"serialGenerator|signalChannelData|unixTransport"
)
DBUS_VALUE_RECEIVERS = (
    rb"Conn|Error|FormatError|InvalidMessageError|InvalidTypeError|ObjectPath|"
    rb"Signature|SignatureError|Type|Variant|authCookieSha1|authExternal|depthCounter|"
    rb"exportedMethod|genericTransport|header|nullwriter|unixTransport"
)
ALLOWED_BINARY_VCS_REFERENCES = (
    re.compile(
        re.escape(b"github" + b".com/godbus/dbus")
        + rb"/v5(?:\.(?:"
        + DBUS_DIRECT_SYMBOLS
        + rb")(?:\.(?:func\d+|gowrap\d+|deferwrap\d+|init|\d+))*|"
        + rb"\.(?:"
        + DBUS_VALUE_RECEIVERS
        + rb")\.(?:[A-Z][A-Za-z0-9_]*|format|generateChallenge|getCookie)"
        + rb"(?:\.(?:[A-Z][A-Za-z0-9_]*|func\d+|gowrap\d+|deferwrap\d+))*|"
        + rb"\.\(\*(?:"
        + DBUS_RECEIVERS
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
BUILD_METADATA_PATTERNS = (
    EMAIL_PATTERN,
    VCS_REFERENCE_PATTERN,
    GENERIC_GIT_URL_PATTERN,
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
TEXT_IDENTITY_OR_BUILD_PATTERNS = (
    re.compile(
        b"vcs"
        + rb"\.branch|GITHUB_(?:HEAD_)?REF|CI_COMMIT"
        + rb"_REF|refs/(?:heads|remotes)/",
        re.IGNORECASE,
    ),
    re.compile(rb"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", re.IGNORECASE),
)
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = len(PAYLOAD_FILES)
LEGACY_CANDIDATE = "97de49b7154c3ea9819853e79ffb8170ba8fbec0017d29ca082e68efb254b959"
LEGACY_PAYLOAD_FILES = PAYLOAD_FILES - {"plugins/faber-claude-code/tools/catalog.json"}


@dataclass(frozen=True)
class OpenRelease:
    number: int
    branch: str
    version: str
    candidate_id: str


def fail(message: str) -> None:
    raise SystemExit(f"Faber candidate pull failed: {message}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def allowed_origin(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" or (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    )


def download(origin: str, destination: Path) -> None:
    if not allowed_origin(origin):
        fail("candidate origin must use HTTPS")
    for name in ("candidate.json", "candidate.tar.gz", "SHA256SUMS"):
        with urlopen(f"{origin.rstrip('/')}/{name}", timeout=30) as response:
            if not allowed_origin(response.geturl()):
                fail("candidate download redirected away from HTTPS")
            content = response.read(MAX_DOWNLOAD_BYTES + 1)
            if len(content) > MAX_DOWNLOAD_BYTES:
                fail(f"candidate download exceeds the size limit: {name}")
            destination.joinpath(name).write_bytes(content)


def copy_candidate(candidate_dir: Path, destination: Path) -> None:
    for name in ("candidate.json", "candidate.tar.gz", "SHA256SUMS"):
        source = candidate_dir / name
        if not source.is_file():
            fail(f"local candidate is missing {name}")
        if source.stat().st_size > MAX_DOWNLOAD_BYTES:
            fail(f"local candidate exceeds the size limit: {name}")
        shutil.copyfile(source, destination / name)


def verify_downloads(downloads: Path) -> dict[str, object]:
    checksum_lines = downloads.joinpath("SHA256SUMS").read_text().splitlines()
    checksums: dict[str, str] = {}
    for line in checksum_lines:
        parts = line.split()
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            fail("SHA256SUMS has an invalid entry")
        checksums[parts[1].lstrip("*")] = parts[0]
    if set(checksums) != {"candidate.json", "candidate.tar.gz"}:
        fail("SHA256SUMS must cover exactly candidate.json and candidate.tar.gz")
    for name, expected in checksums.items():
        if sha256(downloads / name) != expected:
            fail(f"checksum mismatch for {name}")

    try:
        manifest = json.loads(downloads.joinpath("candidate.json").read_text())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"candidate.json is invalid: {error}")
    if set(manifest) != {"candidate_id", "format_version"}:
        fail("candidate.json contains unexpected metadata")
    if manifest["format_version"] != 1:
        fail("unsupported candidate format")
    if not isinstance(manifest["candidate_id"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", manifest["candidate_id"]
    ):
        fail("candidate_id is not an opaque SHA-256 digest")
    return manifest


def extract_safely(archive: Path, destination: Path) -> None:
    found: set[str] = set()
    extracted_bytes = 0
    member_count = 0
    with tarfile.open(archive, "r|gz") as bundle:
        for member in bundle:
            member_count += 1
            if member_count > MAX_ARCHIVE_MEMBERS:
                fail("candidate archive contains too many entries")
            raw_name = member.name
            while raw_name.startswith("./"):
                raw_name = raw_name[2:]
            if not raw_name:
                continue
            relative = PurePosixPath(raw_name)
            if relative.is_absolute() or ".." in relative.parts:
                fail(f"unsafe archive path: {member.name}")
            normalized = relative.as_posix()
            if not member.isfile():
                fail(f"archive entry is not a regular file: {member.name}")
            if (
                member.uid != 0
                or member.gid != 0
                or member.uname
                or member.gname
                or member.mtime != 0
                or member.pax_headers
            ):
                fail(f"archive entry exposes build metadata: {member.name}")
            if normalized not in PAYLOAD_FILES:
                fail(f"unexpected candidate file: {normalized}")
            if normalized in found:
                fail(f"duplicate candidate file: {normalized}")
            expected_mode = 0o755 if normalized in EXECUTABLE_PAYLOAD_FILES else 0o644
            if member.mode & 0o777 != expected_mode:
                fail(f"candidate file has an unexpected mode: {normalized}")
            extracted_bytes += member.size
            if extracted_bytes > MAX_EXTRACTED_BYTES:
                fail("candidate expands beyond the size limit")
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                fail(f"cannot read archive entry: {member.name}")
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o777)
            found.add(normalized)
    if found != PAYLOAD_FILES:
        missing = ", ".join(sorted(PAYLOAD_FILES - found))
        fail(f"candidate is missing required files: {missing}")


def verify_payload(payload: Path, candidate_id: str) -> None:
    calculated = candidate_digest(
        {relative: payload.joinpath(relative).read_bytes() for relative in PAYLOAD_FILES}
    )
    if calculated != candidate_id:
        fail("candidate digest does not match the extracted payload")

    for relative in sorted(PAYLOAD_FILES):
        content = payload.joinpath(relative).read_bytes()
        if relative in BINARY_PAYLOAD_FILES:
            scanned_content = content
        else:
            try:
                text_content = content.decode("utf-8")
            except UnicodeDecodeError:
                fail(f"{relative} is not valid UTF-8")
            for _ in range(4):
                decoded = html.unescape(unquote(text_content))
                if decoded == text_content:
                    break
                text_content = decoded
            else:
                fail(f"{relative} contains excessively nested text encoding")
            if relative.endswith(".json"):
                try:
                    json_content = json.loads(content)
                except json.JSONDecodeError as error:
                    fail(f"{relative} contains invalid JSON: {error}")
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
            scanned_content = text_content.encode()
        scanned_content = PUBLIC_DISTRIBUTION_REFERENCE.sub(b"", scanned_content)
        if EMAIL_PATTERN.search(scanned_content):
            fail(f"{relative} contains prohibited identity or build metadata")
        if relative in BINARY_PAYLOAD_FILES:
            for allowed_reference in ALLOWED_BINARY_VCS_REFERENCES:
                scanned_content = allowed_reference.sub(b"", scanned_content)
        for pattern in BUILD_METADATA_PATTERNS[1:]:
            if pattern.search(scanned_content):
                fail(f"{relative} contains prohibited identity or build metadata")
        if relative not in BINARY_PAYLOAD_FILES:
            for pattern in TEXT_IDENTITY_OR_BUILD_PATTERNS:
                if pattern.search(scanned_content):
                    fail(f"{relative} contains prohibited identity or build metadata")

    try:
        marketplace = json.loads(payload.joinpath(".claude-plugin/marketplace.json").read_text())
        code_manifest = json.loads(
            payload.joinpath(
                "plugins/faber-claude-code/.claude-plugin/plugin.json"
            ).read_text()
        )
        cowork_manifest = json.loads(
            payload.joinpath("plugins/faber-cowork/.claude-plugin/plugin.json").read_text()
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"plugin identity metadata is invalid: {error}")
    if marketplace.get("owner") != {"name": "Faber"}:
        fail("marketplace owner identity is not canonical")
    for product, manifest in (
        ("Claude Code", code_manifest),
        ("Cowork", cowork_manifest),
    ):
        if manifest.get("author") != {"name": "Faber"}:
            fail(f"{product} author identity is not canonical")
        if manifest.get("repository") != PUBLIC_DISTRIBUTION_URL.decode():
            fail(f"{product} repository identity is not canonical")
        if manifest.get("homepage") != "https://www.getfaber.app":
            fail(f"{product} homepage identity is not canonical")

    for relative in STAMP_FILES:
        content = payload.joinpath(relative).read_text()
        if "__VERSION__" not in content:
            fail(f"{relative} is missing its release-version placeholder")


def candidate_digest(
    contents: dict[str, bytes], payload_files: set[str] = PAYLOAD_FILES
) -> str:
    manifest_lines = []
    for relative in sorted(payload_files):
        manifest_lines.append(f"{hashlib.sha256(contents[relative]).hexdigest()}  {relative}\n")
    return hashlib.sha256("".join(manifest_lines).encode()).hexdigest()


def public_candidate_digest(
    repo: Path, version: str, payload_files: set[str] = PAYLOAD_FILES
) -> str:
    contents = {}
    encoded_version = version.encode()
    for relative in payload_files:
        content = repo.joinpath(relative).read_bytes()
        if relative in STAMP_FILES:
            if encoded_version not in content:
                fail(f"{relative} does not contain the public version")
            content = content.replace(encoded_version, b"__VERSION__")
        contents[relative] = content
    return candidate_digest(contents, payload_files)


def verify_public_candidate(repo: Path) -> str:
    version = repo.joinpath("VERSION").read_text().strip()
    candidate_id = repo.joinpath("CANDIDATE").read_text().strip()
    payload_files = LEGACY_PAYLOAD_FILES if candidate_id == LEGACY_CANDIDATE else PAYLOAD_FILES
    if public_candidate_digest(repo, version, payload_files) != candidate_id:
        fail("public plugin contents do not match CANDIDATE")
    return candidate_id


def next_patch(current: str) -> str:
    match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", current)
    if not match:
        fail("public VERSION is not stable SemVer")
    major, minor, patch = (int(part) for part in match.groups())
    return f"{major}.{minor}.{patch + 1}"


def write_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"{name}={value}\n")


def validate_revision(repo: Path, revision: str) -> None:
    with tempfile.TemporaryDirectory(prefix="faber-open-release-") as temporary:
        workspace = Path(temporary)
        archive = workspace / "release.tar"
        checkout = workspace / "checkout"
        checkout.mkdir()
        subprocess.run(
            ["git", "archive", "--format=tar", "--output", str(archive), revision],
            cwd=repo,
            check=True,
        )
        with tarfile.open(archive, "r:") as bundle:
            member_count = 0
            extracted_bytes = 0
            for member in bundle:
                member_count += 1
                if member_count > 128:
                    fail("open release branch contains too many entries")
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or ".." in relative.parts:
                    fail("open release branch contains an unsafe path")
                if member.isdir():
                    continue
                if not member.isfile():
                    fail("open release branch contains a non-file entry")
                extracted_bytes += member.size
                if extracted_bytes > MAX_EXTRACTED_BYTES:
                    fail("open release branch exceeds the size limit")
                target = checkout.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    fail("cannot read open release branch entry")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)
        subprocess.run(
            [str(repo / "scripts/validate-public-release.sh"), str(checkout)],
            check=True,
        )


def inspect_open_release(repo: Path) -> OpenRelease | None:
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--json", "number,isDraft,headRefName"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        releases = [
            value
            for value in json.loads(result.stdout)
            if value["headRefName"].startswith("release/v")
        ]
    except (FileNotFoundError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        fail(f"cannot inspect open release PRs: {error}")
    if len(releases) > 1:
        fail("multiple plugin release PRs are open")
    if not releases:
        return None
    release = releases[0]
    if not release["isDraft"]:
        fail("the open plugin release PR is no longer a draft")
    branch = str(release["headRefName"])
    version = branch.removeprefix("release/v")
    next_patch(version)
    subprocess.run(
        ["git", "fetch", "--quiet", "origin", "refs/" + f"heads/{branch}"],
        cwd=repo,
        check=True,
    )
    candidate_id = subprocess.check_output(
        ["git", "show", "FETCH_HEAD:CANDIDATE"], cwd=repo, text=True
    ).strip()
    branch_version = subprocess.check_output(
        ["git", "show", "FETCH_HEAD:VERSION"], cwd=repo, text=True
    ).strip()
    if branch_version != version:
        fail("open release branch version disagrees with its name")
    contents: dict[str, bytes] = {}
    for relative in PAYLOAD_FILES:
        content = subprocess.check_output(
            ["git", "show", f"FETCH_HEAD:{relative}"], cwd=repo
        )
        if relative in STAMP_FILES:
            content = content.replace(version.encode(), b"__VERSION__")
        contents[relative] = content
    if candidate_digest(contents) != candidate_id:
        fail("open release branch contents do not match its candidate")
    validate_revision(repo, "FETCH_HEAD")
    return OpenRelease(int(release["number"]), branch, version, candidate_id)


def release_action(
    current_candidate: str,
    current_version: str,
    candidate_id: str,
    open_release: OpenRelease | None,
) -> tuple[str, str]:
    target_version = next_patch(current_version)
    target_branch = f"release/v{target_version}"
    if current_candidate == candidate_id:
        if open_release and open_release.candidate_id != candidate_id:
            fail("deployed candidate reverted while a different release draft remains open")
        return "noop", current_version
    if open_release:
        if open_release.branch != target_branch:
            fail(f"open release branch {open_release.branch} does not match {target_branch}")
        if open_release.candidate_id == candidate_id:
            return "noop", open_release.version
    return "update", target_version


def update_repository(
    repo: Path,
    payload: Path,
    candidate_id: str,
    open_release: OpenRelease | None = None,
) -> tuple[bool, str]:
    current_candidate = repo.joinpath("CANDIDATE")
    current_id = current_candidate.read_text().strip() if current_candidate.is_file() else ""
    current_version = repo.joinpath("VERSION").read_text().strip()
    action, version = release_action(current_id, current_version, candidate_id, open_release)
    if action == "noop":
        subprocess.run(
            [str(repo / "scripts/validate-public-release.sh"), str(repo)],
            check=True,
        )
        return False, version

    prepared = payload.parent / "prepared"
    shutil.copytree(payload, prepared)
    for relative in (
        "LICENSE",
        "README.md",
        "plugins/faber-claude-code/README.md",
        "plugins/faber-claude-code/SETUP.md",
        "plugins/faber-cowork/README.md",
    ):
        target = prepared / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / relative, target)
    shutil.copytree(repo / ".github", prepared / ".github")
    shutil.copytree(repo / "scripts", prepared / "scripts")
    for relative in STAMP_FILES:
        path = prepared / relative
        rendered = path.read_text().replace("__VERSION__", version)
        if "__VERSION__" in rendered:
            fail(f"could not stamp {relative}")
        path.write_text(rendered)
    for relative in (
        "VERSION",
        "plugins/faber-claude-code/VERSION",
        "plugins/faber-cowork/VERSION",
    ):
        prepared.joinpath(relative).write_text(version + "\n")
    prepared.joinpath("CANDIDATE").write_text(candidate_id + "\n")

    code_root = prepared / "plugins/faber-claude-code"
    checksum_lines = []
    for binary in sorted(code_root.joinpath("bin").glob("faber-companion_*")):
        checksum_lines.append(f"{sha256(binary)}  bin/{binary.name}\n")
    catalog = code_root / "tools/catalog.json"
    checksum_lines.append(f"{sha256(catalog)}  tools/catalog.json\n")
    code_root.joinpath("SHA256SUMS").write_text("".join(checksum_lines))

    subprocess.run(
        [str(prepared / "scripts/validate-public-release.sh"), str(prepared)],
        check=True,
    )

    for relative in (".claude-plugin", "plugins"):
        shutil.rmtree(repo / relative, ignore_errors=True)
    for relative in ("README.md", "VERSION", "CANDIDATE"):
        target = repo / relative
        if target.exists():
            target.unlink()

    for source in prepared.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(prepared)
        if relative.parts[0] in {".github", "scripts"} or relative.as_posix() == "LICENSE":
            continue
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    return True, version


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--origin",
        default="https://www.getfaber.app/downloads/mcp-plugins",
    )
    source.add_argument("--candidate-dir", type=Path)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--check-open-release", action="store_true")
    parser.add_argument("--verify-public", type=Path)
    args = parser.parse_args()

    if args.verify_public:
        candidate_id = verify_public_candidate(args.verify_public.resolve())
        print(f"Public plugin contents match candidate {candidate_id}")
        return

    repo = args.repo.resolve()
    if not repo.joinpath(".git").exists():
        fail("--repo must point to the public repository root")

    with tempfile.TemporaryDirectory(prefix="faber-candidate-pull-") as temporary:
        workspace = Path(temporary)
        downloads = workspace / "downloads"
        payload = workspace / "payload"
        downloads.mkdir()
        payload.mkdir()
        if args.candidate_dir:
            copy_candidate(args.candidate_dir.resolve(), downloads)
        else:
            download(args.origin, downloads)
        manifest = verify_downloads(downloads)
        extract_safely(downloads / "candidate.tar.gz", payload)
        candidate_id = str(manifest["candidate_id"])
        verify_payload(payload, candidate_id)
        open_release = inspect_open_release(repo) if args.check_open_release else None
        changed, version = update_repository(repo, payload, candidate_id, open_release)

    write_output("changed", str(changed).lower())
    write_output("version", version)
    write_output("candidate_id", candidate_id)
    if changed:
        print(f"Prepared Faber MCP plugins v{version} from candidate {candidate_id}")
    elif open_release and open_release.candidate_id == candidate_id:
        print(f"Open release draft already contains candidate {candidate_id}")
    else:
        print(f"Public plugins already contain candidate {candidate_id}")


if __name__ == "__main__":
    main()
