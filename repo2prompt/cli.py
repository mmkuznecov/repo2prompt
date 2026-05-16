"""
repo2prompt.cli
===============

Command-line interface.  Run ``repo2prompt --help`` for usage.
"""

from __future__ import annotations

import argparse
import os
import sys

from .core import (
    GitignoreFilter,
    LANGUAGE_EXTENSIONS,
    detect_languages,
    traverse_and_copy,
)


# All language keys in a stable order — used to generate flags and to
# resolve them in the same order.
_LANG_KEYS = sorted(LANGUAGE_EXTENSIONS.keys())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo2prompt",
        description=(
            "Copy repository files into a single text prompt for LLMs.\n\n"
            "By default the tool includes important project files (Dockerfile,\n"
            "Makefile, docker-compose.yml, package.json, pyproject.toml, …)\n"
            "in addition to whatever source patterns you request.\n\n"
            "Language flags (--py, --js, --cpp, …) are a convenient shorthand\n"
            "for including all files of a given language.  Combine them\n"
            "freely and mix with --pattern."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "repo_path",
        help="Path to the repository root.",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="prompt.txt",
        help="Output file path. (default: prompt.txt)",
    )
    parser.add_argument(
        "--pattern",
        "-p",
        action="append",
        default=[],
        dest="patterns",
        metavar="GLOB",
        help=(
            "Glob pattern for files to include, e.g. '*.py'. "
            "May be given multiple times.  If neither --pattern nor any "
            "language flag is given, only important project files are "
            "included."
        ),
    )
    parser.add_argument(
        "--no-important",
        action="store_true",
        default=False,
        help=(
            "Disable automatic inclusion of important project files "
            "(Dockerfile, Makefile, docker-compose.yml, etc.)."
        ),
    )
    parser.add_argument(
        "--no-lang-stats",
        action="store_true",
        default=False,
        help="Do not print language statistics to stdout.",
    )
    parser.add_argument(
        "--detect-langs",
        action="store_true",
        default=False,
        help=(
            "Analyse repository languages and print the breakdown, "
            "then exit without producing an output file."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help=(
            "Print extra diagnostics to stderr "
            "(loaded .gitignore files, file counts, etc.). "
            "Useful when nothing seems to match."
        ),
    )

    # Language shorthand flags --------------------------------------------
    lang_group = parser.add_argument_group(
        "language flags",
        "Include all files of a specific language.  "
        "Multiple flags can be combined (e.g. --py --js).",
    )
    for key in _LANG_KEYS:
        exts = LANGUAGE_EXTENSIONS[key]
        lang_group.add_argument(
            f"--{key}",
            action="store_true",
            default=False,
            help=f"Include {key} files ({', '.join(exts)}).",
        )

    return parser


def resolve_patterns(args: argparse.Namespace) -> list[str]:
    """Combine explicit --pattern values with language-flag patterns."""
    patterns = list(args.patterns)
    for key in _LANG_KEYS:
        if getattr(args, key, False):
            for ext in LANGUAGE_EXTENSIONS[key]:
                glob = f"*{ext}"
                if glob not in patterns:
                    patterns.append(glob)
    return patterns


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_path = os.path.abspath(args.repo_path)
    if not os.path.isdir(repo_path):
        print(f"Error: '{repo_path}' is not a directory.", file=sys.stderr)
        return 1

    # ------------------------------------------------------------------
    # --detect-langs only: scan and exit.
    # ------------------------------------------------------------------
    if args.detect_langs:
        if not os.path.exists(os.path.join(repo_path, ".git")):
            print(
                f"Warning: '{repo_path}' has no .git directory.",
                file=sys.stderr,
            )
        gitignore = GitignoreFilter(repo_path, verbose=args.verbose)
        stats = detect_languages(repo_path, gitignore)
        if not stats:
            print("No recognised source files found.")
        else:
            print("Language breakdown (by lines of code):")
            for lang, pct in stats.items():
                bar = "█" * max(1, int(pct / 2))
                print(f"  {lang:<8} {pct:>5.1f}%  {bar}")
        return 0

    patterns = resolve_patterns(args)

    n_written = traverse_and_copy(
        repo_path=repo_path,
        patterns=patterns,
        output_file=args.output,
        include_important=not args.no_important,
        show_language_stats=not args.no_lang_stats,
        verbose=args.verbose,
    )

    return 0 if n_written >= 0 else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
