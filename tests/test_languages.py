from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from repo2prompt.cli import build_parser, resolve_patterns
from repo2prompt.core import GitignoreFilter, detect_languages


class LanguageSupportTests(unittest.TestCase):
    def test_html_flag_resolves_all_html_extensions(self) -> None:
        parser = build_parser()
        args = parser.parse_args([".", "--html"])

        self.assertEqual(resolve_patterns(args), ["*.html", "*.htm", "*.xhtml"])

    def test_multiple_new_language_flags_can_be_combined(self) -> None:
        parser = build_parser()
        args = parser.parse_args([".", "--html", "--css", "--yaml"])

        self.assertEqual(
            resolve_patterns(args),
            ["*.css", "*.html", "*.htm", "*.xhtml", "*.yaml", "*.yml"],
        )

    def test_language_detection_recognizes_new_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "index.html").write_text("<h1>Hello</h1>\n", encoding="utf-8")
            (root / "styles.css").write_text(
                "h1 { display: block; }\n", encoding="utf-8"
            )
            (root / "config.yaml").write_text("enabled: true\n", encoding="utf-8")

            stats = detect_languages(str(root), GitignoreFilter(str(root)))

        self.assertEqual(set(stats), {"html", "css", "yaml"})
        self.assertAlmostEqual(sum(stats.values()), 100.0, delta=0.2)


if __name__ == "__main__":
    unittest.main()
