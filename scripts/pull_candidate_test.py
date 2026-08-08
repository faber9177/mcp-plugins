from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
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
    def test_publishing_workflow_is_limited_to_canonical_main(self) -> None:
        workflow = Path(__file__).parents[1].joinpath(
            ".github/workflows/pull-candidate.yml"
        ).read_text()
        self.assertIn("name: Faber plugin mirror", workflow)
        self.assertIn("Validate mirror changes:", workflow)
        self.assertIn("Pull trusted candidate from getfaber.app", workflow)
        self.assertIn(
            "if: github.event_name != 'pull_request' && "
            "github.repository_id == '1327095477' && "
            "github.ref == 'refs/heads/main'",
            workflow,
        )
        self.assertNotIn("github.repository ==", workflow)
        self.assertNotRegex(workflow, r"(?m)^\s*push:\s*$")

    def test_candidate_origin_requires_https_except_for_loopback(self) -> None:
        self.assertTrue(pull_candidate.allowed_origin("https://www.getfaber.app"))
        self.assertTrue(pull_candidate.allowed_origin("http://127.0.0.1:3000"))
        self.assertFalse(pull_candidate.allowed_origin("http://example.com"))

    def test_next_patch_rejects_non_stable_versions(self) -> None:
        self.assertEqual(pull_candidate.next_patch("0.1.1"), "0.1.2")
        with self.assertRaises(SystemExit):
            pull_candidate.next_patch("0.1.1-beta")

    def test_manifest_and_checksum_are_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_id = "a" * 64
            self._write_downloads(root, candidate_id, b"archive")
            self.assertEqual(
                pull_candidate.verify_downloads(root)["candidate_id"], candidate_id
            )
            root.joinpath("candidate.tar.gz").write_bytes(b"changed")
            with self.assertRaises(SystemExit):
                pull_candidate.verify_downloads(root)

    def test_manifest_rejects_untrusted_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_downloads(root, "a" * 64, b"archive")
            manifest = json.loads(root.joinpath("candidate.json").read_text())
            manifest["source_revision"] = "private"
            root.joinpath("candidate.json").write_text(json.dumps(manifest))
            self._write_checksums(root)
            with self.assertRaises(SystemExit):
                pull_candidate.verify_downloads(root)

    def test_archive_accepts_future_client_and_opaque_catalog_metadata(self) -> None:
        files = {
            ".claude-plugin/marketplace.json": (b"{}", 0o644),
            "plugins/faber-gemini/tools/catalog.json": (
                b'{"client":"gemini-cli","instructions":"use Faber","tools":[]}',
                0o644,
            ),
            "plugins/faber-codex/bin/launcher": (b"binary", 0o755),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "candidate.tar.gz"
            self._write_archive(archive, files)
            payload = root / "payload"
            extracted = pull_candidate.extract_safely(archive, payload)
            candidate_id = pull_candidate.candidate_digest(
                {path: content for path, (content, _mode) in files.items()}
            )
            pull_candidate.verify_payload(payload, candidate_id, extracted)
            self.assertEqual(extracted["plugins/faber-codex/bin/launcher"], 0o755)

    def test_archive_rejects_links_traversal_duplicates_and_protected_paths(self) -> None:
        cases = []
        link = tarfile.TarInfo("plugins/faber/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "target"
        cases.append(("link", [(link, None)]))
        cases.append(("traversal", [(self._file_member("../private"), b"data")]))
        cases.append(("backslash", [(self._file_member(r"..\private"), b"data")]))
        cases.append(("protected", [(self._file_member("scripts/replace.sh"), b"data")]))
        cases.append(("git-directory", [(self._file_member("plugins/faber/.git/config"), b"data")]))
        cases.append(("gitignore", [(self._file_member("plugins/faber/.gitignore"), b"data")]))
        cases.append(("gitattributes", [(self._file_member("plugins/faber/.gitattributes"), b"data")]))
        cases.append(("gitmodules", [(self._file_member("plugins/faber/.gitmodules"), b"data")]))
        duplicate = self._file_member("plugins/faber/file")
        cases.append(("duplicate", [(duplicate, b"data"), (duplicate, b"data")]))

        for name, members in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root / "candidate.tar.gz"
                with tarfile.open(archive, "w:gz") as bundle:
                    for member, content in members:
                        bundle.addfile(member, io.BytesIO(content) if content is not None else None)
                with self.assertRaises(SystemExit):
                    pull_candidate.extract_safely(archive, root / "payload")

    def test_archive_rejects_metadata_modes_and_size_overflow(self) -> None:
        for name in ("metadata", "mode", "size"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root / "candidate.tar.gz"
                member = self._file_member("plugins/faber/file")
                if name == "metadata":
                    member.uid = 501
                    member.uname = "developer"
                elif name == "mode":
                    member.mode = 0o777
                with tarfile.open(archive, "w:gz") as bundle:
                    bundle.addfile(member, io.BytesIO(b"data"))
                limit = 3 if name == "size" else pull_candidate.MAX_EXTRACTED_BYTES
                with patch.object(pull_candidate, "MAX_EXTRACTED_BYTES", limit):
                    with self.assertRaises(SystemExit):
                        pull_candidate.extract_safely(archive, root / "payload")

    def test_local_candidate_size_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            destination.mkdir()
            for name in pull_candidate.DOWNLOAD_FILES:
                source.joinpath(name).write_bytes(b"1234")
            with patch.object(pull_candidate, "MAX_DOWNLOAD_BYTES", 3):
                with self.assertRaises(SystemExit):
                    pull_candidate.copy_candidate(source, destination)

    def test_candidate_state_rejects_protected_and_duplicate_paths(self) -> None:
        for paths in (["scripts/replace.sh"], ["plugins/faber/file", "plugins/faber/file"]):
            with self.subTest(paths=paths), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                entries = [
                    {"path": path, "mode": "0644", "version_stamped": False}
                    for path in paths
                ]
                root.joinpath("CANDIDATE_FILES").write_text(
                    json.dumps({"format_version": 1, "files": entries})
                )
                with self.assertRaises(SystemExit):
                    pull_candidate.read_candidate_state(root)

    def test_public_tree_is_bound_to_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            version = "0.1.2"
            placeholder = b'{"client":"codex","version":"__VERSION__"}'
            rendered = placeholder.replace(b"__VERSION__", version.encode())
            path = repo / "plugins/faber-codex/catalog.json"
            path.parent.mkdir(parents=True)
            path.write_bytes(rendered)
            path.chmod(0o644)
            candidate_id = pull_candidate.candidate_digest(
                {"plugins/faber-codex/catalog.json": placeholder}
            )
            repo.joinpath("VERSION").write_text(version + "\n")
            repo.joinpath("CANDIDATE").write_text(candidate_id + "\n")
            repo.joinpath("CANDIDATE_FILES").write_text(
                pull_candidate.state_document(
                    [pull_candidate.CandidateFile("plugins/faber-codex/catalog.json", 0o644, True)]
                )
            )
            self.assertEqual(pull_candidate.verify_public_candidate(repo), candidate_id)
            path.write_text("changed")
            with self.assertRaises(SystemExit):
                pull_candidate.verify_public_candidate(repo)

    def test_update_mirrors_future_files_removes_old_files_and_preserves_public_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            repo = workspace / "repo"
            payload = workspace / "payload"
            repo.mkdir()
            payload.mkdir()
            old_path = "plugins/faber-old/file.txt"
            old_content = b"old"
            old_target = repo / old_path
            old_target.parent.mkdir(parents=True)
            old_target.write_bytes(old_content)
            old_target.chmod(0o644)
            old_id = pull_candidate.candidate_digest({old_path: old_content})
            repo.joinpath("VERSION").write_text("0.1.1\n")
            repo.joinpath("CANDIDATE").write_text(old_id + "\n")
            repo.joinpath("CANDIDATE_FILES").write_text(
                pull_candidate.state_document(
                    [pull_candidate.CandidateFile(old_path, 0o644, False)]
                )
            )
            repo.joinpath("README.md").write_text("public documentation\n")
            checksum = repo / "plugins/faber-gemini/SHA256SUMS"
            checksum.parent.mkdir(parents=True)
            checksum.write_text("placeholder  catalog.json\n")
            validator = repo / "scripts/validate-public-release.sh"
            validator.parent.mkdir()
            validator.write_text("#!/bin/sh\nexit 0\n")
            validator.chmod(0o755)

            new_path = "plugins/faber-gemini/catalog.json"
            new_content = b'{"client":"gemini-cli","version":"__VERSION__"}'
            target = payload / new_path
            target.parent.mkdir(parents=True)
            target.write_bytes(new_content)
            new_id = pull_candidate.candidate_digest({new_path: new_content})
            changed, version = pull_candidate.update_repository(
                repo, payload, {new_path: 0o644}, new_id
            )

            self.assertTrue(changed)
            self.assertEqual(version, "0.1.2")
            self.assertFalse(old_target.exists())
            self.assertEqual(repo.joinpath("README.md").read_text(), "public documentation\n")
            self.assertIn('"client":"gemini-cli"', repo.joinpath(new_path).read_text())
            self.assertIn('"version":"0.1.2"', repo.joinpath(new_path).read_text())
            expected_checksum = pull_candidate.sha256(repo.joinpath(new_path))
            self.assertEqual(checksum.read_text(), f"{expected_checksum}  catalog.json\n")
            self.assertEqual(pull_candidate.verify_public_candidate(repo), new_id)

    def test_update_rejects_collision_with_public_owned_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            repo = workspace / "repo"
            payload = workspace / "payload"
            repo.mkdir()
            payload.mkdir()
            old_path = "plugins/faber-old/file.txt"
            old_content = b"old"
            old_target = repo / old_path
            old_target.parent.mkdir(parents=True)
            old_target.write_bytes(old_content)
            old_target.chmod(0o644)
            repo.joinpath("VERSION").write_text("0.1.1\n")
            repo.joinpath("CANDIDATE").write_text(
                pull_candidate.candidate_digest({old_path: old_content}) + "\n"
            )
            repo.joinpath("CANDIDATE_FILES").write_text(
                pull_candidate.state_document(
                    [pull_candidate.CandidateFile(old_path, 0o644, False)]
                )
            )
            public_path = "plugins/faber-new/README.md"
            public_file = repo / public_path
            public_file.parent.mkdir(parents=True)
            public_file.write_text("public documentation\n")
            candidate_content = b"candidate documentation\n"
            payload_file = payload / public_path
            payload_file.parent.mkdir(parents=True)
            payload_file.write_bytes(candidate_content)

            with self.assertRaises(SystemExit):
                pull_candidate.update_repository(
                    repo,
                    payload,
                    {public_path: 0o644},
                    pull_candidate.candidate_digest({public_path: candidate_content}),
                )
            self.assertEqual(public_file.read_text(), "public documentation\n")

    def test_revision_validation_uses_committed_candidate_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            scripts = repo / "scripts"
            scripts.mkdir()
            source_scripts = Path(__file__).parent
            for name in ("pull_candidate.py", "validate-public-release.sh"):
                shutil.copy2(source_scripts / name, scripts / name)
            relative = "plugins/faber/file.txt"
            content = b"candidate\n"
            candidate_file = repo / relative
            candidate_file.parent.mkdir(parents=True)
            candidate_file.write_bytes(content)
            candidate_file.chmod(0o644)
            candidate_id = pull_candidate.candidate_digest({relative: content})
            repo.joinpath("VERSION").write_text("0.1.1\n")
            repo.joinpath("CANDIDATE").write_text(candidate_id + "\n")
            repo.joinpath("CANDIDATE_FILES").write_text(
                pull_candidate.state_document(
                    [pull_candidate.CandidateFile(relative, 0o644, False)]
                )
            )
            repo.joinpath(".gitignore").write_text(relative + "\n")
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-qm",
                    "test",
                ],
                cwd=repo,
                check=True,
            )
            self.assertEqual(pull_candidate.verify_public_candidate(repo), candidate_id)
            with self.assertRaises(SystemExit):
                pull_candidate.validate_revision(repo, "HEAD")

    def test_release_action_updates_matching_open_release_for_retry(self) -> None:
        release = pull_candidate.OpenRelease(7, "release/v0.1.2", "0.1.2", "a" * 64)
        self.assertEqual(
            pull_candidate.release_action("0" * 64, "0.1.1", "a" * 64, release),
            ("update", "0.1.2"),
        )

    def test_mode_only_candidate_change_creates_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            repo = workspace / "repo"
            payload = workspace / "payload"
            repo.mkdir()
            payload.mkdir()
            relative = "plugins/faber/launcher"
            content = b"launcher"
            for root in (repo, payload):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            repo.joinpath(relative).chmod(0o644)
            payload.joinpath(relative).chmod(0o755)
            candidate_id = pull_candidate.candidate_digest({relative: content})
            repo.joinpath("VERSION").write_text("0.1.1\n")
            repo.joinpath("CANDIDATE").write_text(candidate_id + "\n")
            repo.joinpath("CANDIDATE_FILES").write_text(
                pull_candidate.state_document(
                    [pull_candidate.CandidateFile(relative, 0o644, False)]
                )
            )
            validator = repo / "scripts/validate-public-release.sh"
            validator.parent.mkdir()
            validator.write_text("#!/bin/sh\nexit 0\n")
            validator.chmod(0o755)

            changed, version = pull_candidate.update_repository(
                repo, payload, {relative: 0o755}, candidate_id
            )
            self.assertTrue(changed)
            self.assertEqual(version, "0.1.2")
            self.assertEqual(repo.joinpath(relative).stat().st_mode & 0o777, 0o755)

    def test_release_action_fails_closed_on_production_rollback(self) -> None:
        release = pull_candidate.OpenRelease(7, "release/v0.1.2", "0.1.2", "b" * 64)
        with self.assertRaises(SystemExit):
            pull_candidate.release_action("a" * 64, "0.1.1", "a" * 64, release)

    @staticmethod
    def _write_downloads(root: Path, candidate_id: str, archive: bytes) -> None:
        root.joinpath("candidate.json").write_text(
            json.dumps({"candidate_id": candidate_id, "format_version": 1})
        )
        root.joinpath("candidate.tar.gz").write_bytes(archive)
        CandidatePullTests._write_checksums(root)

    @staticmethod
    def _write_checksums(root: Path) -> None:
        lines = []
        for name in ("candidate.json", "candidate.tar.gz"):
            digest = hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest()
            lines.append(f"{digest}  {name}\n")
        root.joinpath("SHA256SUMS").write_text("".join(lines))

    @staticmethod
    def _file_member(name: str) -> tarfile.TarInfo:
        member = tarfile.TarInfo(name)
        member.size = 4
        member.mode = 0o644
        member.uid = member.gid = 0
        member.uname = member.gname = ""
        member.mtime = 0
        return member

    @staticmethod
    def _write_archive(archive: Path, files: dict[str, tuple[bytes, int]]) -> None:
        with tarfile.open(archive, "w:gz") as bundle:
            for name, (content, mode) in files.items():
                member = CandidatePullTests._file_member(name)
                member.size = len(content)
                member.mode = mode
                bundle.addfile(member, io.BytesIO(content))


if __name__ == "__main__":
    unittest.main()
