import argparse
import sys
import os
import warnings

from .core import (
    traverse_and_copy,
    LANGUAGE_EXTENSIONS,
    detect_languages,
    load_gitignore_patterns,
)

# Build the set of language flag names for the help text
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
            "for including all files of a given language. You can combine them\n"
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
            "Can be specified multiple times. "
            "If neither --pattern nor any language flag is given, "
            "only important project files are included."
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
            "Analyse the repository languages and print the breakdown, "
            "then exit without producing an output file."
        ),
    )

    # ------------------------------------------------------------------ #
    # Language shorthand flags                                             #
    # ------------------------------------------------------------------ #
    lang_group = parser.add_argument_group(
        "language flags",
        "Include all files of a specific language. "
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
    patterns = list(args.patterns)  # explicit patterns

    for key in _LANG_KEYS:
        if getattr(args, key, False):
            for ext in LANGUAGE_EXTENSIONS[key]:
                glob = f"*{ext}"
                if glob not in patterns:
                    patterns.append(glob)

    return patterns


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_path = os.path.abspath(args.repo_path)
    if not os.path.isdir(repo_path):
        print(f"Error: '{repo_path}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    # --detect-langs only mode
    if args.detect_langs:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            gitignore_patterns = load_gitignore_patterns(repo_path)
            for w in caught:
                print(f"Warning: {w.message}", file=sys.stderr)
        stats = detect_languages(repo_path, gitignore_patterns)
        if not stats:
            print("No recognised source files found.")
        else:
            print("Language breakdown (by lines of code):")
            for lang, pct in stats.items():
                bar = "█" * int(pct / 2)
                print(f"  {lang:<8} {pct:>5.1f}%  {bar}")
        return

    patterns = resolve_patterns(args)

    traverse_and_copy(
        repo_path=repo_path,
        patterns=patterns,
        output_file=args.output,
        include_important=not args.no_important,
        show_language_stats=not args.no_lang_stats,
    )
