"""Checks that terminal publication preserves unrelated work and stops safely."""

import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "brief_and_publish", Path(__file__).with_name("brief_and_publish.py")
)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.behind = "0"
        self.ahead = "0"
        self.root = Path("/project")
        self.paths = ["docs/_posts/2026-10-20-summary-en.md"]

    def fake_git(self, root, *args, **kwargs):
        self.calls.append(args)
        outputs = {
            ("branch", "--show-current"): "main",
            ("remote", "get-url", "origin"): "https://github.com/peijie36/Horizon.git",
            ("rev-list", "--count", "HEAD..origin/main"): self.behind,
            ("rev-list", "--count", "origin/main..HEAD"): self.ahead,
        }
        return subprocess.CompletedProcess(
            args, 1 if args[:1] == ("diff",) else 0, stdout=outputs.get(args, "")
        )

    def test_only_briefings_are_committed(self):
        with patch.object(publisher, "git", side_effect=self.fake_git), patch.object(
            publisher, "brief_paths", return_value=self.paths
        ):
            publisher.publish(self.root, 24, True, False)
        commit = next(args for args in self.calls if args[0] == "commit")
        self.assertIn("--only", commit)
        self.assertEqual(commit[commit.index("--") + 1:], tuple(self.paths))
        self.assertIn(("push", "origin", "HEAD:main"), self.calls)

    def test_failed_generation_does_not_commit_or_push(self):
        with patch.object(publisher, "git", side_effect=self.fake_git), patch.object(
            Path, "is_file", return_value=True
        ), patch.object(
            publisher.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["horizon"])
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                publisher.publish(self.root, 24, False, False)
        self.assertFalse(any(args[0] in {"add", "commit", "push"} for args in self.calls))

    def test_remote_changes_stop_before_generation(self):
        self.behind = "1"
        with patch.object(publisher, "git", side_effect=self.fake_git), patch.object(
            publisher.subprocess, "run"
        ) as generation:
            with self.assertRaisesRegex(RuntimeError, "behind"):
                publisher.publish(self.root, 24, False, False)
        generation.assert_not_called()

    def test_dry_run_makes_no_git_or_ai_calls(self):
        with patch.object(publisher, "git") as git, patch.object(
            publisher.subprocess, "run"
        ) as generation:
            publisher.publish(self.root, 24, False, True)
        git.assert_not_called()
        generation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
