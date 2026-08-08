from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True

spec = importlib.util.spec_from_file_location(
    "pull_candidate", Path(__file__).with_name("pull_candidate.py")
)
assert spec and spec.loader
pull_candidate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pull_candidate
spec.loader.exec_module(pull_candidate)


class CandidatePullTests(unittest.TestCase):
    def test_candidate_origin_requires_https_except_for_loopback(self) -> None:
        self.assertTrue(pull_candidate.allowed_origin("https://www.getfaber.app"))
        self.assertTrue(pull_candidate.allowed_origin("http://127.0.0.1:3000"))
        self.assertFalse(pull_candidate.allowed_origin("http://example.com"))

    def test_next_patch_rejects_non_stable_versions(self) -> None:
        self.assertEqual(pull_candidate.next_patch("0.1.1"), "0.1.2")
        with self.assertRaises(SystemExit):
            pull_candidate.next_patch("0.1.1-beta")

    def test_manifest_rejects_private_metadata_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = {
                "candidate_id": "a" * 64,
                "format_version": 1,
                "source_revision": "private",
            }
            root.joinpath("candidate.json").write_text(json.dumps(manifest))
            root.joinpath("candidate.tar.gz").write_bytes(b"archive")
            lines = []
            for name in ("candidate.json", "candidate.tar.gz"):
                digest = hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest()
                lines.append(f"{digest}  {name}\n")
            root.joinpath("SHA256SUMS").write_text("".join(lines))
            with self.assertRaises(SystemExit):
                pull_candidate.verify_downloads(root)

    def test_archive_rejects_links_and_traversal(self) -> None:
        for name, member in (
            ("link", self._link_member()),
            ("traversal", self._file_member("../private")),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root / "candidate.tar.gz"
                with tarfile.open(archive, "w:gz") as bundle:
                    bundle.addfile(member, io.BytesIO(b"data") if member.isfile() else None)
                with self.assertRaises(SystemExit):
                    pull_candidate.extract_safely(archive, root / "payload")

    def test_archive_rejects_build_identity_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "candidate.tar.gz"
            member = self._file_member(".claude-plugin/marketplace.json")
            member.mode = 0o644
            member.uid = 501
            member.uname = "developer"
            with tarfile.open(archive, "w:gz") as bundle:
                bundle.addfile(member, io.BytesIO(b"data"))
            with self.assertRaises(SystemExit):
                pull_candidate.extract_safely(archive, root / "payload")

    def test_identity_and_build_markers_are_detected(self) -> None:
        samples = (
            b"release-owner" + b"\x40example.org",
            b"git@github" + b".com:example-owner/example-repository.git",
            b"https://gitlab" + b".com/example-group/example-repository",
            b"bitbucket" + b".org/example-team/example-repository",
            b"/" + b"Users/developer/project",
            b"build\t" + b"vcs" + b".revision=abc",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(
                    any(
                        pattern.search(sample)
                        for pattern in (
                            *pull_candidate.BUILD_METADATA_PATTERNS,
                            *pull_candidate.TEXT_IDENTITY_OR_BUILD_PATTERNS,
                        )
                    )
                )

    def test_payload_verification_rejects_identity_and_build_markers(self) -> None:
        samples = (
            b"contact: release-owner" + b"\x40example.org",
            b'contact: "release.owner"' + b"\x40example.org",
            b"contact: 123+github-actions[bot]" + b"\x40evil.example",
            b"source url: git@github" + b".com:example-owner/example-repository.git",
            b"source url: https://github" + b".com./example-owner/example-repository",
            b"source url: https://raw"
            + b".githubuser"
            + b"content.com/example-owner/example-repository/main/file",
            b"source url: " + pull_candidate.PUBLIC_DISTRIBUTION_URL + b"/unpublished",
            b"source url: https://git.corp.example/team/" + b"private-repository.git",
            b"source url: https://github" + b".com\\example-owner\\example-repository",
            b"contact: private.owner&#" + b"64;example.org",
            b"source path: /" + b"Users/developer/project",
            b"build setting: vcs" + b".revision=abc123",
            b"branch: refs/" + b"heads/feature/release-candidate",
            b"revision: " + (b"a" * 40),
        )
        for sample in samples:
            with self.subTest(sample=sample), tempfile.TemporaryDirectory() as temporary:
                payload = Path(temporary)
                for relative in pull_candidate.PAYLOAD_FILES:
                    path = payload / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    content = b"__VERSION__" if relative in pull_candidate.STAMP_FILES else b"safe"
                    path.write_bytes(content)
                marker_path = payload / ".claude-plugin/marketplace.json"
                marker_path.write_bytes(marker_path.read_bytes() + sample)
                candidate_id = self._candidate_id(payload)
                with self.assertRaises(SystemExit):
                    pull_candidate.verify_payload(payload, candidate_id)

    def test_payload_allows_the_public_distribution_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary)
            self._write_valid_payload(payload)
            skill = payload / "plugins/faber-claude-code/skills/faber/SKILL.md"
            for wrapped in (
                pull_candidate.PUBLIC_DISTRIBUTION_URL + b").\n",
                b"`" + pull_candidate.PUBLIC_DISTRIBUTION_URL + b"`\n",
                b"**" + pull_candidate.PUBLIC_DISTRIBUTION_URL + b"**\n",
                b"__" + pull_candidate.PUBLIC_DISTRIBUTION_URL + b"__\n",
                b"~~" + pull_candidate.PUBLIC_DISTRIBUTION_URL + b"~~\n",
            ):
                with self.subTest(wrapped=wrapped):
                    skill.write_bytes(wrapped)
                    pull_candidate.verify_payload(payload, self._candidate_id(payload))

    def test_payload_rejects_identity_in_binary_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary)
            self._write_valid_payload(payload)
            binary = payload / "plugins/faber-claude-code/bin/faber-companion_linux_amd64"
            binary.write_bytes(binary.read_bytes() + b"\x00release-owner\x40example.org\x00")
            with self.assertRaises(SystemExit):
                pull_candidate.verify_payload(payload, self._candidate_id(payload))

    def test_payload_rejects_vcs_reference_in_binary_bytes(self) -> None:
        samples = (
            b"github" + b".com/example-owner/example-repository",
            b"github" + b".com/godbus/dbus-private/private-repository",
            b"github" + b".com/godbus/dbus/private-repository",
            b"github" + b".com/godbus/dbus/v5.private",
            b"github" + b".com/zalando/go-keyring/private-repository",
            b"github" + b".com/zalando/go-keyring/secret_service.private",
            b"private-owner" + b"@github" + b".com/godbus/dbus/v5",
            b"https://git.corp.example/team/" + b"private-repository.git",
        )
        for sample in samples:
            with self.subTest(sample=sample), tempfile.TemporaryDirectory() as temporary:
                payload = Path(temporary)
                self._write_valid_payload(payload)
                binary = payload / "plugins/faber-claude-code/bin/faber-companion_linux_amd64"
                binary.write_bytes(binary.read_bytes() + b"\x00" + sample + b"\x00")
                with self.assertRaises(SystemExit):
                    pull_candidate.verify_payload(payload, self._candidate_id(payload))

    def test_payload_rejects_json_escaped_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary)
            self._write_valid_payload(payload)
            catalog = payload / "plugins/faber-claude-code/tools/catalog.json"
            catalog.write_text(r'{"instructions":"private.owner\u0040example.org","tools":[]}')
            with self.assertRaises(SystemExit):
                pull_candidate.verify_payload(payload, self._candidate_id(payload))

    def test_payload_requires_canonical_manifest_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary)
            self._write_valid_payload(payload)
            manifest_path = payload / "plugins/faber-claude-code/.claude-plugin/plugin.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["author"] = {"name": "Release Owner"}
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaises(SystemExit):
                pull_candidate.verify_payload(payload, self._candidate_id(payload))

    def test_public_contents_are_bound_to_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            version = "0.1.2"
            for relative in pull_candidate.PAYLOAD_FILES:
                path = repo / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                content = b"__VERSION__" if relative in pull_candidate.STAMP_FILES else b"safe"
                path.write_bytes(content.replace(b"__VERSION__", version.encode()))
            contents = {}
            for relative in pull_candidate.PAYLOAD_FILES:
                content = repo.joinpath(relative).read_bytes()
                if relative in pull_candidate.STAMP_FILES:
                    content = content.replace(version.encode(), b"__VERSION__")
                contents[relative] = content
            candidate_id = pull_candidate.candidate_digest(contents)
            repo.joinpath("VERSION").write_text(version + "\n")
            repo.joinpath("CANDIDATE").write_text(candidate_id + "\n")
            self.assertEqual(pull_candidate.verify_public_candidate(repo), candidate_id)
            repo.joinpath("plugins/faber-cowork/hooks/hooks.json").write_text("changed")
            with self.assertRaises(SystemExit):
                pull_candidate.verify_public_candidate(repo)

    def test_release_action_reuses_matching_open_draft(self) -> None:
        candidate_id = "a" * 64
        release = pull_candidate.OpenRelease(7, "release/v0.1.2", "0.1.2", candidate_id)
        self.assertEqual(
            pull_candidate.release_action("0" * 64, "0.1.1", candidate_id, release),
            ("noop", "0.1.2"),
        )

    def test_release_action_fails_closed_on_production_rollback(self) -> None:
        release = pull_candidate.OpenRelease(7, "release/v0.1.2", "0.1.2", "b" * 64)
        with self.assertRaises(SystemExit):
            pull_candidate.release_action("a" * 64, "0.1.1", "a" * 64, release)

    def test_open_release_is_verified_from_its_remote_contents(self) -> None:
        version = "0.1.2"
        placeholder_contents = {
            relative: (b"__VERSION__" if relative in pull_candidate.STAMP_FILES else b"safe")
            for relative in pull_candidate.PAYLOAD_FILES
        }
        candidate_id = pull_candidate.candidate_digest(placeholder_contents)

        def fake_run(args, **_kwargs):
            if args[:3] == ["gh", "pr", "list"]:
                return type(
                    "Result",
                    (),
                    {
                        "stdout": json.dumps(
                            [
                                {
                                    "number": 7,
                                    "isDraft": True,
                                    "headRefName": "release/v0.1.2",
                                }
                            ]
                        )
                    },
                )()
            return type("Result", (), {"stdout": ""})()

        def fake_check_output(args, **kwargs):
            path = args[2].removeprefix("FETCH_HEAD:")
            if path == "CANDIDATE":
                value = candidate_id + "\n"
            elif path == "VERSION":
                value = version + "\n"
            else:
                value = placeholder_contents[path].replace(b"__VERSION__", version.encode())
                return value
            return value if kwargs.get("text") else value.encode()

        with patch.object(pull_candidate.subprocess, "run", side_effect=fake_run), patch.object(
            pull_candidate.subprocess, "check_output", side_effect=fake_check_output
        ), patch.object(pull_candidate, "validate_revision"):
            release = pull_candidate.inspect_open_release(Path("."))
        self.assertEqual(
            release,
            pull_candidate.OpenRelease(7, "release/v0.1.2", version, candidate_id),
        )

    @staticmethod
    def _link_member() -> tarfile.TarInfo:
        member = tarfile.TarInfo("README.md")
        member.type = tarfile.SYMTYPE
        member.linkname = "private"
        return member

    @staticmethod
    def _file_member(name: str) -> tarfile.TarInfo:
        member = tarfile.TarInfo(name)
        member.size = 4
        return member

    @staticmethod
    def _candidate_id(payload: Path) -> str:
        lines = []
        for relative in sorted(pull_candidate.PAYLOAD_FILES):
            digest = hashlib.sha256(payload.joinpath(relative).read_bytes()).hexdigest()
            lines.append(f"{digest}  {relative}\n")
        return hashlib.sha256("".join(lines).encode()).hexdigest()

    @staticmethod
    def _write_valid_payload(payload: Path) -> None:
        for relative in pull_candidate.PAYLOAD_FILES:
            path = payload / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative.endswith(".json"):
                content = (
                    b'{"version":"__VERSION__"}'
                    if relative in pull_candidate.STAMP_FILES
                    else b"{}"
                )
            else:
                content = b"__VERSION__" if relative in pull_candidate.STAMP_FILES else b"safe"
            path.write_bytes(content)
        payload.joinpath(".claude-plugin/marketplace.json").write_text(
            json.dumps({"owner": {"name": "Faber"}})
        )
        manifest = {
            "version": "__VERSION__",
            "author": {"name": "Faber"},
            "repository": pull_candidate.PUBLIC_DISTRIBUTION_URL.decode(),
            "homepage": "https://www.getfaber.app",
        }
        for relative in (
            "plugins/faber-claude-code/.claude-plugin/plugin.json",
            "plugins/faber-cowork/.claude-plugin/plugin.json",
        ):
            payload.joinpath(relative).write_text(json.dumps(manifest))


if __name__ == "__main__":
    unittest.main()
