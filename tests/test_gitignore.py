from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from repo2prompt.core import GitignoreFilter, collect_files


class GitignoreTests(unittest.TestCase):
    def test_root_negation_and_directory_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".gitignore").write_text(
                "*.log\n!keep.log\n/cache/\n",
                encoding="utf-8",
            )
            (root / "drop.log").write_text("drop", encoding="utf-8")
            (root / "keep.log").write_text("keep", encoding="utf-8")
            (root / "cache").mkdir()
            (root / "cache" / "data.py").write_text("pass", encoding="utf-8")
            (root / "main.py").write_text("pass", encoding="utf-8")

            gitignore = GitignoreFilter(str(root))

            self.assertTrue(gitignore.is_ignored(str(root / "drop.log")))
            self.assertFalse(gitignore.is_ignored(str(root / "keep.log")))
            self.assertTrue(gitignore.is_ignored(str(root / "cache")))
            files = collect_files(
                str(root),
                ["*.py", "*.log"],
                gitignore,
                include_important=False,
            )
            self.assertEqual(files, [str(root / "keep.log"), str(root / "main.py")])

    def test_nested_gitignore_patterns_are_scoped_to_their_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ui = root / "ui"
            ui.mkdir()
            (ui / ".gitignore").write_text(
                "/dist/\n*.tmp\n!keep.tmp\n",
                encoding="utf-8",
            )
            (ui / "dist").mkdir()
            (ui / "dist" / "bundle.js").write_text("bundle", encoding="utf-8")
            (ui / "drop.tmp").write_text("drop", encoding="utf-8")
            (ui / "keep.tmp").write_text("keep", encoding="utf-8")
            (root / "root.tmp").write_text("root", encoding="utf-8")

            gitignore = GitignoreFilter(str(root))

            self.assertTrue(gitignore.is_ignored(str(ui / "dist")))
            self.assertTrue(gitignore.is_ignored(str(ui / "drop.tmp")))
            self.assertFalse(gitignore.is_ignored(str(ui / "keep.tmp")))
            self.assertFalse(gitignore.is_ignored(str(root / "root.tmp")))

    def test_gitignore_is_an_important_project_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ignore_file = root / ".gitignore"
            ignore_file.write_text("build/\n", encoding="utf-8")

            files = collect_files(
                str(root),
                [],
                GitignoreFilter(str(root)),
                include_important=True,
            )

            self.assertEqual(files, [str(ignore_file)])


if __name__ == "__main__":
    unittest.main()
