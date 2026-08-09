#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
from urllib.parse import urlparse
from urllib.request import urlopen


DOWNLOAD_FILES = ("candidate.json", "candidate.tar.gz", "SHA256SUMS")
PROTECTED_ROOTS = {
    ".git",
    ".github",
    "scripts",
    "LICENSE",
    "README.md",
    "VERSION",
    "CANDIDATE",
    "CANDIDATE_FILES",
}
GIT_CONTROL_NAMES = {".git", ".gitignore", ".gitattributes", ".gitmodules"}
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 96 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 1024


@dataclass(frozen=True)
class CandidateFile:
    path: str
    mode: int
    version_stamped: bool


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
    for name in DOWNLOAD_FILES:
        with urlopen(f"{origin.rstrip('/')}/{name}", timeout=30) as response:
            if not allowed_origin(response.geturl()):
                fail("candidate download redirected away from HTTPS")
            content = response.read(MAX_DOWNLOAD_BYTES + 1)
            if len(content) > MAX_DOWNLOAD_BYTES:
                fail(f"candidate download exceeds the size limit: {name}")
            destination.joinpath(name).write_bytes(content)


def copy_candidate(candidate_dir: Path, destination: Path) -> None:
    for name in DOWNLOAD_FILES:
        source = candidate_dir / name
        if not source.is_file():
            fail(f"local candidate is missing {name}")
        if source.stat().st_size > MAX_DOWNLOAD_BYTES:
            fail(f"local candidate exceeds the size limit: {name}")
        shutil.copyfile(source, destination / name)


def verify_downloads(downloads: Path) -> dict[str, object]:
    try:
        checksum_lines = downloads.joinpath("SHA256SUMS").read_text().splitlines()
    except UnicodeDecodeError as error:
        fail(f"SHA256SUMS is invalid: {error}")
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
    candidate_id = manifest["candidate_id"]
    if not isinstance(candidate_id, str) or not re.fullmatch(r"[0-9a-f]{64}", candidate_id):
        fail("candidate_id is not an opaque SHA-256 digest")
    return manifest


def protected_path(relative: PurePosixPath) -> bool:
    return (
        not relative.parts
        or relative.parts[0] in PROTECTED_ROOTS
        or any(part in GIT_CONTROL_NAMES for part in relative.parts)
    )


def extract_safely(archive: Path, destination: Path) -> dict[str, int]:
    found: dict[str, int] = {}
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
            if "\\" in raw_name:
                fail(f"unsafe archive path: {member.name}")
            relative = PurePosixPath(raw_name)
            if relative.is_absolute() or ".." in relative.parts:
                fail(f"unsafe archive path: {member.name}")
            if protected_path(relative):
                fail(f"candidate targets a protected path: {relative.as_posix()}")
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
            normalized = relative.as_posix()
            if normalized in found:
                fail(f"duplicate candidate file: {normalized}")
            mode = member.mode & 0o777
            if mode not in {0o644, 0o755}:
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
            target.chmod(mode)
            found[normalized] = mode
    if not found:
        fail("candidate archive is empty")
    return found


def candidate_digest(contents: dict[str, bytes]) -> str:
    lines = [
        f"{hashlib.sha256(contents[path]).hexdigest()}  {path}\n"
        for path in sorted(contents)
    ]
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def verify_payload(payload: Path, candidate_id: str, files: dict[str, int]) -> None:
    calculated = candidate_digest(
        {relative: payload.joinpath(relative).read_bytes() for relative in files}
    )
    if calculated != candidate_id:
        fail("candidate digest does not match the extracted payload")


def state_document(files: list[CandidateFile]) -> str:
    return json.dumps(
        {
            "format_version": 1,
            "files": [
                {
                    "path": entry.path,
                    "mode": format(entry.mode, "04o"),
                    "version_stamped": entry.version_stamped,
                }
                for entry in sorted(files, key=lambda value: value.path)
            ],
        },
        indent=2,
    ) + "\n"


def read_candidate_state(repo: Path) -> list[CandidateFile]:
    try:
        state = json.loads(repo.joinpath("CANDIDATE_FILES").read_text())
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"CANDIDATE_FILES is invalid: {error}")
    if set(state) != {"format_version", "files"} or state["format_version"] != 1:
        fail("CANDIDATE_FILES has an unsupported format")
    if not isinstance(state["files"], list) or not state["files"]:
        fail("CANDIDATE_FILES does not list mirrored files")
    entries: list[CandidateFile] = []
    seen: set[str] = set()
    for value in state["files"]:
        if not isinstance(value, dict) or set(value) != {"path", "mode", "version_stamped"}:
            fail("CANDIDATE_FILES contains an invalid entry")
        path = value["path"]
        relative = PurePosixPath(path) if isinstance(path, str) else PurePosixPath()
        if (
            not isinstance(path, str)
            or relative.is_absolute()
            or ".." in relative.parts
            or protected_path(relative)
            or path != relative.as_posix()
            or path in seen
        ):
            fail("CANDIDATE_FILES contains an unsafe or duplicate path")
        if value["mode"] not in {"0644", "0755"} or not isinstance(
            value["version_stamped"], bool
        ):
            fail("CANDIDATE_FILES contains invalid file metadata")
        seen.add(path)
        entries.append(CandidateFile(path, int(value["mode"], 8), value["version_stamped"]))
    return entries


def public_candidate_digest(repo: Path, version: str, files: list[CandidateFile]) -> str:
    contents: dict[str, bytes] = {}
    encoded_version = version.encode()
    for entry in files:
        path = repo.joinpath(entry.path)
        if not path.is_file() or path.is_symlink():
            fail(f"mirrored candidate file is missing or unsafe: {entry.path}")
        if path.stat().st_mode & 0o777 != entry.mode:
            fail(f"mirrored candidate file mode disagrees: {entry.path}")
        content = path.read_bytes()
        if entry.version_stamped:
            if encoded_version not in content:
                fail(f"{entry.path} does not contain the public version")
            content = content.replace(encoded_version, b"__VERSION__")
        contents[entry.path] = content
    return candidate_digest(contents)


def verify_public_metadata(
    repo: Path, version: str, candidate_files: list[CandidateFile]
) -> None:
    packages = sorted(
        {
            "/".join(PurePosixPath(entry.path).parts[:2])
            for entry in candidate_files
            if len(PurePosixPath(entry.path).parts) >= 3
            and PurePosixPath(entry.path).parts[0] == "plugins"
        }
    )
    binary_packages = {
        package
        for package in packages
        if any(
            entry.path.startswith(package + "/bin/") for entry in candidate_files
        )
    }
    expected_metadata = {f"{package}/VERSION" for package in packages}
    expected_metadata.update(
        f"{package}/SHA256SUMS" for package in binary_packages
    )
    actual_metadata = {
        path.relative_to(repo).as_posix()
        for pattern in ("plugins/**/VERSION", "plugins/**/SHA256SUMS")
        for path in repo.glob(pattern)
    }
    if actual_metadata != expected_metadata:
        fail("public package metadata does not match current candidate packages")

    for package in packages:
        package_root = repo / package
        version_file = package_root / "VERSION"
        if (
            version_file.is_symlink()
            or not version_file.is_file()
            or version_file.stat().st_mode & 0o777 != 0o644
        ):
            fail(f"public package VERSION is unsafe: {package}")
        try:
            package_version = version_file.read_text()
        except (FileNotFoundError, UnicodeDecodeError) as error:
            fail(f"public package VERSION is invalid: {package}: {error}")
        if package_version != version + "\n":
            fail(f"public package VERSION disagrees: {package}")
        if package not in binary_packages:
            continue
        checksum_file = package_root / "SHA256SUMS"
        if (
            checksum_file.is_symlink()
            or not checksum_file.is_file()
            or checksum_file.stat().st_mode & 0o777 != 0o644
        ):
            fail(f"public package SHA256SUMS is unsafe: {package}")
        expected_checksums = []
        for entry in sorted(candidate_files, key=lambda value: value.path):
            if not entry.path.startswith(package + "/"):
                continue
            target = repo / entry.path
            relative = target.relative_to(package_root).as_posix()
            expected_checksums.append(f"{sha256(target)}  {relative}\n")
        try:
            checksums = checksum_file.read_text()
        except (FileNotFoundError, UnicodeDecodeError) as error:
            fail(f"public package SHA256SUMS is invalid: {package}: {error}")
        if checksums != "".join(expected_checksums):
            fail(f"public package SHA256SUMS disagrees: {package}")


def verify_public_candidate(repo: Path) -> str:
    try:
        version = repo.joinpath("VERSION").read_text().strip()
        candidate_id = repo.joinpath("CANDIDATE").read_text().strip()
    except (FileNotFoundError, UnicodeDecodeError) as error:
        fail(f"public release metadata is invalid: {error}")
    next_patch(version)
    if not re.fullmatch(r"[0-9a-f]{64}", candidate_id):
        fail("CANDIDATE is not an opaque SHA-256 digest")
    files = read_candidate_state(repo)
    if public_candidate_digest(repo, version, files) != candidate_id:
        fail("public plugin contents do not match CANDIDATE")
    verify_public_metadata(repo, version, files)
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
            members = bundle.getmembers()
            if len(members) > 2048:
                fail("open release branch contains too many entries")
            total = 0
            for member in members:
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or ".." in relative.parts:
                    fail("open release branch contains an unsafe path")
                if member.isdir():
                    continue
                if not member.isfile():
                    fail("open release branch contains a non-file entry")
                total += member.size
                if total > MAX_EXTRACTED_BYTES:
                    fail("open release branch exceeds the size limit")
                target = checkout.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    fail("cannot read open release branch entry")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
        try:
            subprocess.run(
                [str(repo / "scripts/validate-public-release.sh"), str(checkout)], check=True
            )
        except subprocess.CalledProcessError:
            fail(f"public revision does not match its candidate: {revision}")


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
    branch = str(release["headRefName"])
    version = branch.removeprefix("release/v")
    next_patch(version)
    subprocess.run(
        ["git", "fetch", "--quiet", "origin", f"refs/heads/{branch}"],
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
    validate_revision(repo, "FETCH_HEAD")
    return OpenRelease(int(release["number"]), branch, version, candidate_id)


def release_action(
    current_candidate: str,
    current_version: str,
    candidate_id: str,
    open_release: OpenRelease | None,
) -> tuple[str, str]:
    if current_candidate == candidate_id:
        if open_release and open_release.candidate_id != candidate_id:
            fail("deployed candidate reverted while a different release remains open")
        return "noop", current_version
    if open_release:
        expected_branch = f"release/v{next_patch(current_version)}"
        if open_release.branch != expected_branch:
            fail(f"open release branch {open_release.branch} does not match {expected_branch}")
        return "update", open_release.version
    return "update", next_patch(current_version)


def remove_empty_parents(path: Path, stop: Path) -> None:
    parent = path.parent
    while parent != stop and parent.is_dir():
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def remove_public_metadata(root: Path) -> None:
    for pattern in ("plugins/**/VERSION", "plugins/**/SHA256SUMS"):
        for path in root.glob(pattern):
            if path.is_symlink() or not path.is_file():
                fail(
                    "public package metadata is unsafe: "
                    + path.relative_to(root).as_posix()
                )
            path.unlink()
            remove_empty_parents(path, root)


def refresh_public_metadata(
    prepared: Path, version: str, candidate_files: list[CandidateFile]
) -> None:
    remove_public_metadata(prepared)

    package_paths = sorted(
        {
            "/".join(PurePosixPath(entry.path).parts[:2])
            for entry in candidate_files
            if len(PurePosixPath(entry.path).parts) >= 3
            and PurePosixPath(entry.path).parts[0] == "plugins"
        }
    )
    for package in package_paths:
        package_root = prepared / package
        version_file = package_root / "VERSION"
        if version_file.is_symlink() or (
            version_file.exists() and not version_file.is_file()
        ):
            fail(f"public package VERSION is unsafe: {package}")
        version_file.write_text(version + "\n")

        has_binary = any(
            entry.path.startswith(package + "/bin/") for entry in candidate_files
        )
        checksum_file = package_root / "SHA256SUMS"
        if not has_binary and not checksum_file.exists():
            continue
        if checksum_file.is_symlink() or (
            checksum_file.exists() and not checksum_file.is_file()
        ):
            fail(f"public package SHA256SUMS is unsafe: {package}")
        mirrored = [
            entry
            for entry in candidate_files
            if entry.path.startswith(package + "/")
            and entry.path != checksum_file.relative_to(prepared).as_posix()
        ]
        entries = []
        for entry in sorted(mirrored, key=lambda value: value.path):
            target = prepared.joinpath(entry.path)
            relative = target.relative_to(checksum_file.parent).as_posix()
            entries.append(f"{sha256(target)}  {relative}\n")
        checksum_file.write_text("".join(entries))


def prepare_release(
    repo: Path,
    payload: Path,
    files: dict[str, int],
    candidate_id: str,
    version: str,
    destination: Path,
) -> list[CandidateFile]:
    shutil.copytree(repo, destination, ignore=shutil.ignore_patterns(".git", ".codex"))
    old_state = read_candidate_state(repo)
    old_paths = {entry.path for entry in old_state}
    for entry in old_state:
        target = destination.joinpath(entry.path)
        if target.exists() or target.is_symlink():
            target.unlink()
            remove_empty_parents(target, destination)

    state: list[CandidateFile] = []
    for relative, mode in sorted(files.items()):
        source = payload.joinpath(relative)
        target = destination.joinpath(relative)
        if relative not in old_paths and (target.exists() or target.is_symlink()):
            fail(f"candidate collides with a public-owned path: {relative}")
        parent = target.parent
        while parent != destination:
            if parent.exists() and (not parent.is_dir() or parent.is_symlink()):
                fail(f"candidate path has a public-owned non-directory parent: {relative}")
            parent = parent.parent
        target.parent.mkdir(parents=True, exist_ok=True)
        content = source.read_bytes()
        version_stamped = False
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            if "__VERSION__" in text:
                content = text.replace("__VERSION__", version).encode()
                version_stamped = True
        target.write_bytes(content)
        target.chmod(mode)
        state.append(CandidateFile(relative, mode, version_stamped))

    destination.joinpath("VERSION").write_text(version + "\n")
    destination.joinpath("CANDIDATE").write_text(candidate_id + "\n")
    destination.joinpath("CANDIDATE_FILES").write_text(state_document(state))
    refresh_public_metadata(destination, version, state)
    subprocess.run(
        [str(destination / "scripts/validate-public-release.sh"), str(destination)], check=True
    )
    return state


def apply_prepared_release(repo: Path, prepared: Path) -> None:
    old_paths = {entry.path for entry in read_candidate_state(repo)}
    new_paths = {entry.path for entry in read_candidate_state(prepared)}
    for relative in old_paths - new_paths:
        target = repo.joinpath(relative)
        if target.exists() or target.is_symlink():
            target.unlink()
            remove_empty_parents(target, repo)
    remove_public_metadata(repo)
    for relative in new_paths:
        source = prepared.joinpath(relative)
        target = repo.joinpath(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for name in ("VERSION", "CANDIDATE", "CANDIDATE_FILES"):
        shutil.copy2(prepared / name, repo / name)
    for pattern in ("plugins/**/VERSION", "plugins/**/SHA256SUMS"):
        for source in prepared.glob(pattern):
            relative = source.relative_to(prepared)
            target = repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def update_repository(
    repo: Path,
    payload: Path,
    files: dict[str, int],
    candidate_id: str,
    open_release: OpenRelease | None = None,
) -> tuple[bool, str]:
    current_id = repo.joinpath("CANDIDATE").read_text().strip()
    current_version = repo.joinpath("VERSION").read_text().strip()
    current_layout = {entry.path: entry.mode for entry in read_candidate_state(repo)}
    layout_changed = current_layout != files
    if current_id == candidate_id and layout_changed:
        if open_release:
            expected_branch = f"release/v{next_patch(current_version)}"
            if open_release.branch != expected_branch:
                fail(f"open release branch {open_release.branch} does not match {expected_branch}")
            action, version = "update", open_release.version
        else:
            action, version = "update", next_patch(current_version)
    else:
        action, version = release_action(
            current_id, current_version, candidate_id, open_release
        )
    if action == "noop":
        subprocess.run([str(repo / "scripts/validate-public-release.sh"), str(repo)], check=True)
        return False, version

    prepared = payload.parent / "prepared"
    prepare_release(repo, payload, files, candidate_id, version, prepared)
    apply_prepared_release(repo, prepared)
    return True, version


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--origin", default="https://www.getfaber.app/downloads/mcp-plugins"
    )
    source.add_argument("--candidate-dir", type=Path)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--check-open-release", action="store_true")
    parser.add_argument("--verify-public", type=Path)
    parser.add_argument("--verify-revision")
    args = parser.parse_args()

    if args.verify_public:
        candidate_id = verify_public_candidate(args.verify_public.resolve())
        print(f"Public plugin contents match candidate {candidate_id}")
        return

    repo = args.repo.resolve()
    if not repo.joinpath(".git").exists():
        fail("--repo must point to the public repository root")
    if args.verify_revision:
        validate_revision(repo, args.verify_revision)
        print(f"Public revision {args.verify_revision} matches its candidate")
        return

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
        files = extract_safely(downloads / "candidate.tar.gz", payload)
        candidate_id = str(manifest["candidate_id"])
        verify_payload(payload, candidate_id, files)
        open_release = inspect_open_release(repo) if args.check_open_release else None
        changed, version = update_repository(repo, payload, files, candidate_id, open_release)

    write_output("changed", str(changed).lower())
    write_output("version", version)
    write_output("candidate_id", candidate_id)
    print(
        f"Candidate {candidate_id} {'prepared as v' + version if changed else 'is already published'}"
    )


if __name__ == "__main__":
    main()
