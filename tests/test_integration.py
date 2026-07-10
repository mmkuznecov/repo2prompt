from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from repo2prompt.cli import main
from repo2prompt.core import (
    GitignoreFilter,
    collect_files,
    detect_languages,
    traverse_and_copy,
)


class IntegrationTests(unittest.TestCase):
    def test_analysis_only_writes_map_and_excludes_generated_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "a.py").write_text(
                "from b import two\ndef one(): pass\n", encoding="utf-8"
            )
            (root / "b.py").write_text("def two(): pass\n", encoding="utf-8")
            output = root / "generated" / "prompt.txt"
            analysis_json = root / "generated" / "analysis.json"
            output.parent.mkdir()
            output.write_text("old prompt", encoding="utf-8")
            analysis_json.write_text("{}", encoding="utf-8")

            count = traverse_and_copy(
                str(root),
                ["*.py", "*.json", "*.txt"],
                str(output),
                include_important=False,
                show_language_stats=False,
                analysis_only=True,
                dependency_format="both",
                analysis_json=str(analysis_json),
            )

            self.assertEqual(count, 2)
            prompt = output.read_text(encoding="utf-8")
            self.assertIn("Code Analysis:", prompt)
            self.assertNotIn("File: a.py", prompt)
            self.assertNotIn("prompt.txt", prompt)
            self.assertNotIn("analysis.json", prompt)
            payload = json.loads(analysis_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["stats"]["files"], 2)

    def test_no_analysis_preserves_file_content_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "a.py").write_text("def one(): pass", encoding="utf-8")
            output = root / "prompt.txt"

            traverse_and_copy(
                str(root),
                ["*.py"],
                str(output),
                include_important=False,
                show_language_stats=False,
                include_analysis=False,
            )

            prompt = output.read_text(encoding="utf-8")
            self.assertNotIn("Code Analysis:", prompt)
            self.assertIn("File: a.py", prompt)
            self.assertIn("def one(): pass", prompt)

    def test_cli_version(self) -> None:
        stdout = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            self.assertRaises(SystemExit) as result,
        ):
            main(["--version"])
        self.assertEqual(result.exception.code, 0)
        self.assertEqual(stdout.getvalue().strip(), "repo2prompt 0.4.0")

    def test_cli_supports_analysis_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "a.py").write_text("def one(): pass\n", encoding="utf-8")
            output = root / "prompt.txt"
            analysis_json = root / "analysis.json"

            result = main(
                [
                    str(root),
                    "--py",
                    "--no-important",
                    "--analysis-only",
                    "--dependency-format",
                    "mermaid",
                    "--analysis-json",
                    str(analysis_json),
                    "--max-symbols-per-file",
                    "1",
                    "--output",
                    str(output),
                ]
            )

            self.assertEqual(result, 0)
            self.assertIn(
                "Dependency diagram (Mermaid):", output.read_text(encoding="utf-8")
            )
            self.assertTrue(analysis_json.exists())

    def test_json_analysis_can_be_written_without_embedding_analysis_in_prompt(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "a.py").write_text("def one(): pass\n", encoding="utf-8")
            output = root / "prompt.txt"
            analysis_json = root / "analysis.json"

            traverse_and_copy(
                str(root),
                ["*.py"],
                str(output),
                include_important=False,
                show_language_stats=False,
                include_analysis=False,
                analysis_json=str(analysis_json),
            )

            self.assertNotIn("Code Analysis:", output.read_text(encoding="utf-8"))
            self.assertEqual(
                json.loads(analysis_json.read_text(encoding="utf-8"))["stats"]["files"],
                1,
            )

    def test_collect_and_language_detection_exclude_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.py"
            generated = root / "generated.py"
            source.write_text("print('source')", encoding="utf-8")
            generated.write_text("print('generated')\n" * 10, encoding="utf-8")
            gitignore = GitignoreFilter(str(root))

            files = collect_files(
                str(root),
                ["*.py"],
                gitignore,
                include_important=False,
                exclude_paths=[str(generated)],
            )
            stats = detect_languages(
                str(root), gitignore, exclude_paths=[str(generated)]
            )

            self.assertEqual(files, [str(source)])
            self.assertEqual(stats, {"py": 100.0})

    def test_nested_gitignore_still_excludes_files_from_collection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
            (root / "kept.py").write_text("pass\n", encoding="utf-8")
            (root / "ignored.py").write_text("pass\n", encoding="utf-8")
            gitignore = GitignoreFilter(str(root))

            files = collect_files(
                str(root), ["*.py"], gitignore, include_important=False
            )

            self.assertEqual(files, [str(root / "kept.py")])

    def test_cli_rejects_conflicting_analysis_options_and_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stderr = io.StringIO()
            with (
                contextlib.redirect_stderr(stderr),
                self.assertRaises(SystemExit) as conflict,
            ):
                main([str(root), "--no-analysis", "--analysis-only"])
            self.assertEqual(conflict.exception.code, 2)
            self.assertIn("cannot be combined", stderr.getvalue())

            stderr = io.StringIO()
            output = root / "same.txt"
            with (
                contextlib.redirect_stderr(stderr),
                self.assertRaises(SystemExit) as same_path,
            ):
                main(
                    [
                        str(root),
                        "--output",
                        str(output),
                        "--analysis-json",
                        str(output),
                    ]
                )
            self.assertEqual(same_path.exception.code, 2)
            self.assertIn("must be different", stderr.getvalue())

    def test_programmatic_api_rejects_same_prompt_and_json_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "same.txt"
            with self.assertRaisesRegex(ValueError, "must be different"):
                traverse_and_copy(
                    str(root),
                    [],
                    str(output),
                    analysis_json=str(output),
                )

    def test_line_detection_counts_unterminated_last_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "a.py").write_text("one line", encoding="utf-8")
            stats = detect_languages(str(root), GitignoreFilter(str(root)))
            self.assertEqual(stats, {"py": 100.0})


if __name__ == "__main__":
    unittest.main()
