"""
repo2prompt.core
================

Walk a repository and emit a single text "prompt" suitable for feeding to an
LLM.  Supports:

  * Proper `.gitignore` semantics via the `pathspec` library (the same library
    used by Black, pre-commit, etc.).  Handles anchored patterns (``/dist``),
    directory-only patterns (``build/``), negation (``!keep.me``), ``**``
    globs, and nested ``.gitignore`` files in sub-directories.
  * Language detection by line-of-code count, with a tidy bar-chart summary.
  * Auto-inclusion of "important" project files: Dockerfile, Makefile,
    docker-compose.yml, package.json, pyproject.toml, README, LICENSE, etc.
"""

from __future__ import annotations

import os
import fnmatch
import warnings
from pathlib import Path
from collections import defaultdict
from typing import Iterable

import pathspec

# Silence pathspec's "GitWildMatchPattern is deprecated" warning, which is
# emitted from inside pathspec itself and not actionable for end users.
warnings.filterwarnings(
    "ignore",
    message=r"GitWildMatchPattern \('gitwildmatch'\) is deprecated.*",
)


# ---------------------------------------------------------------------------
# Language definitions
# ---------------------------------------------------------------------------

# Each canonical language key maps to its file-extension list.  These power
# both detection (extension → language) and the CLI shorthand flags
# (``--py``, ``--js``, ``--cpp`` …).
LANGUAGE_EXTENSIONS: dict[str, list[str]] = {
    "py": [".py", ".pyi"],
    "js": [".js", ".mjs", ".cjs", ".jsx"],
    "ts": [".ts", ".tsx"],
    "cpp": [".cpp", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".hxx"],
    "c": [".c", ".h"],
    "java": [".java"],
    "go": [".go"],
    "rs": [".rs"],
    "rb": [".rb"],
    "php": [".php"],
    "cs": [".cs"],
    "swift": [".swift"],
    "kt": [".kt", ".kts"],
    "scala": [".scala"],
    "r": [".r", ".R"],
    "sh": [".sh", ".bash", ".zsh"],
    "lua": [".lua"],
    "ex": [".ex", ".exs"],
    "hs": [".hs"],
    "ml": [".ml", ".mli"],
    "dart": [".dart"],
    "vue": [".vue"],
    "svelte": [".svelte"],
    "sql": [".sql"],
}

# Reverse map: extension → canonical language key.
# When a language with overlapping extensions appears later in the dict
# (e.g. ``c`` claims ``.h``), it wins over earlier ones — fine for stats.
EXT_TO_LANG: dict[str, str] = {}
for _lang, _exts in LANGUAGE_EXTENSIONS.items():
    for _ext in _exts:
        EXT_TO_LANG[_ext.lower()] = _lang


# Important non-source files that should always be included by default.
IMPORTANT_FILENAMES: set[str] = {
    # Build / CI
    "Dockerfile",
    "dockerfile",
    "Containerfile",
    "Makefile",
    "makefile",
    "GNUmakefile",
    "CMakeLists.txt",
    "Jenkinsfile",
    "tox.ini",
    "docker-compose.yml",
    "docker-compose.yaml",
    "docker-compose.override.yml",
    "docker-compose.override.yaml",
    # Env templates
    ".env.example",
    ".env.sample",
    ".env.template",
    # Python
    "requirements.txt",
    "requirements-dev.txt",
    "Pipfile",
    "Pipfile.lock",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "MANIFEST.in",
    ".flake8",
    ".pylintrc",
    "mypy.ini",
    ".pre-commit-config.yaml",
    "uv.lock",
    "poetry.lock",
    "pdm.lock",
    # Node / JS
    "package.json",
    "tsconfig.json",
    "tsconfig.base.json",
    "webpack.config.js",
    "webpack.config.ts",
    "vite.config.js",
    "vite.config.ts",
    "rollup.config.js",
    "rollup.config.ts",
    "babel.config.js",
    "babel.config.json",
    ".babelrc",
    "jest.config.js",
    "jest.config.ts",
    "next.config.js",
    "nuxt.config.js",
    "svelte.config.js",
    ".eslintrc",
    ".eslintrc.js",
    ".eslintrc.json",
    ".eslintrc.yml",
    ".prettierrc",
    ".prettierrc.js",
    ".prettierrc.json",
    # Go / Rust / JVM / Ruby / PHP
    "go.mod",
    "go.sum",
    "Cargo.toml",
    "Cargo.lock",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "pom.xml",
    "Gemfile",
    "Gemfile.lock",
    "composer.json",
    "composer.lock",
    # Project meta
    "README.md",
    "README.rst",
    "README.txt",
    "README",
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "COPYING",
    "CHANGELOG.md",
    "CHANGELOG.rst",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    ".editorconfig",
    ".gitattributes",
    # CI configs
    ".travis.yml",
    "azure-pipelines.yml",
    ".circleci",
}


# ---------------------------------------------------------------------------
# .gitignore handling (pathspec-based)
# ---------------------------------------------------------------------------


class GitignoreFilter:
    """
    Loads every ``.gitignore`` in the repository tree (root + nested) and
    exposes a single :py:meth:`is_ignored` predicate that follows real
    gitignore semantics.

    Implementation notes
    --------------------
    * Each pattern from a *nested* ``.gitignore`` is re-anchored to be relative
      to the repository root.  Example: a pattern ``build/`` inside
      ``ui/.gitignore`` becomes ``/ui/build/`` from the repo root.
    * We combine everything into one ``PathSpec`` for fast matching.
    """

    def __init__(self, repo_path: str, verbose: bool = False) -> None:
        self.repo_path = os.path.abspath(repo_path)
        self.verbose = verbose
        self._raw_patterns: list[str] = []
        self._gitignore_files: list[str] = []
        self._load()
        # ``gitwildmatch`` is the dialect git itself uses.
        self.spec = pathspec.PathSpec.from_lines("gitwildmatch", self._raw_patterns)

    # ------------------------------------------------------------------
    def _load(self) -> None:
        """Walk the repo and collect every .gitignore, re-anchoring patterns."""
        for dirpath, dirnames, filenames in os.walk(self.repo_path):
            # Never descend into .git itself
            if ".git" in dirnames:
                dirnames.remove(".git")
            if ".gitignore" not in filenames:
                continue

            gitignore_path = os.path.join(dirpath, ".gitignore")
            self._gitignore_files.append(gitignore_path)

            # Path of this .gitignore *relative to the repo root*, using
            # forward slashes (gitwildmatch is POSIX-style).
            rel_dir = os.path.relpath(dirpath, self.repo_path).replace(os.sep, "/")
            if rel_dir == ".":
                rel_dir = ""

            try:
                with open(gitignore_path, "r", encoding="utf-8", errors="replace") as f:
                    raw_lines = f.readlines()
            except OSError:
                continue

            for line in raw_lines:
                stripped = self._normalise_line(line)
                if stripped is None:
                    continue
                anchored = self._anchor_pattern(stripped, rel_dir)
                self._raw_patterns.append(anchored)

        if self.verbose:
            import sys

            print(
                f"[repo2prompt] Loaded {len(self._gitignore_files)} "
                f".gitignore file(s) with "
                f"{len(self._raw_patterns)} effective pattern(s).",
                file=sys.stderr,
            )
            for gi in self._gitignore_files:
                print(f"  - {gi}", file=sys.stderr)

    # ------------------------------------------------------------------
    @staticmethod
    def _normalise_line(line: str) -> str | None:
        """Strip trailing newline / comments; return ``None`` for empty/comment."""
        # Per gitignore: trailing spaces are stripped unless escaped (rare).
        line = line.rstrip("\r\n")
        # Inline comments are NOT supported by git — only whole-line comments.
        if not line.strip():
            return None
        if line.lstrip().startswith("#"):
            return None
        return line

    # ------------------------------------------------------------------
    @staticmethod
    def _anchor_pattern(pattern: str, rel_dir: str) -> str:
        """
        Make *pattern* (read from a .gitignore living at *rel_dir* under the
        repo root) into an absolute-from-repo-root pattern.

        Examples (rel_dir == 'ui'):
            'build/'        -> '/ui/build/'        (anchored to ui/)
            '/dist'         -> '/ui/dist'          (anchored, dir-relative)
            'node_modules'  -> '/ui/**/node_modules' (basename, anywhere under ui/)
            '!keep.txt'     -> '!/ui/**/keep.txt'  (negation preserved)
        """
        if not rel_dir:
            # Top-level .gitignore: patterns stay as-is.
            return pattern

        # Pull off the leading '!' (negation) so we can reattach it last.
        negate = pattern.startswith("!")
        if negate:
            pattern = pattern[1:]

        # Strip the leading slash if the pattern is already anchored.
        if pattern.startswith("/"):
            new = f"/{rel_dir}/{pattern.lstrip('/')}"
        elif "/" in pattern.rstrip("/"):
            # Contains a slash → treated as a path relative to the .gitignore
            new = f"/{rel_dir}/{pattern}"
        else:
            # Basename-only pattern → matches anywhere *under* this dir.
            new = f"/{rel_dir}/**/{pattern}"

        return ("!" + new) if negate else new

    # ------------------------------------------------------------------
    def is_ignored(self, abs_path: str) -> bool:
        """
        Return ``True`` if *abs_path* (a real path on disk under the repo)
        should be excluded.
        """
        try:
            rel = os.path.relpath(abs_path, self.repo_path)
        except ValueError:
            return False
        if rel == "." or rel.startswith(".."):
            return False

        # gitwildmatch expects POSIX-style relative paths.
        rel_posix = rel.replace(os.sep, "/")

        # Directories must end with '/' for directory-only rules to match.
        if os.path.isdir(abs_path) and not rel_posix.endswith("/"):
            rel_posix += "/"

        return self.spec.match_file(rel_posix)

    # ------------------------------------------------------------------
    @property
    def gitignore_files(self) -> list[str]:
        return list(self._gitignore_files)


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


def _count_lines(path: str) -> int:
    """Cheaply count newline characters; binary-safe (no decoding)."""
    try:
        count = 0
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                count += chunk.count(b"\n")
        return count
    except OSError:
        return 0


def detect_languages(
    repo_path: str,
    gitignore: GitignoreFilter,
) -> dict[str, float]:
    """
    Walk the repo and count source-code lines per language.
    Returns ``{lang_key: percentage}`` sorted descending by lines.
    """
    counts: dict[str, int] = defaultdict(int)
    total = 0
    repo_path = os.path.abspath(repo_path)

    for root, dirs, files in os.walk(repo_path):
        # Filter dirs in-place so os.walk skips ignored subtrees entirely.
        dirs[:] = [
            d
            for d in dirs
            if d != ".git" and not gitignore.is_ignored(os.path.join(root, d))
        ]
        for fname in files:
            fpath = os.path.join(root, fname)
            if gitignore.is_ignored(fpath):
                continue
            ext = Path(fname).suffix.lower()
            lang = EXT_TO_LANG.get(ext)
            if lang is None:
                continue
            lines = _count_lines(fpath)
            counts[lang] += lines
            total += lines

    if total == 0:
        return {}

    return {
        lang: round(count / total * 100, 1)
        for lang, count in sorted(counts.items(), key=lambda x: -x[1])
    }


# ---------------------------------------------------------------------------
# Directory tree
# ---------------------------------------------------------------------------


def generate_directory_tree(
    dir_path: str,
    repo_path: str,
    gitignore: GitignoreFilter,
    prefix: str = "",
) -> str:
    """ASCII directory tree, with `.git` and gitignored entries hidden."""
    tree = ""
    try:
        items = os.listdir(dir_path)
    except (PermissionError, OSError):
        return tree

    items_paths = [os.path.join(dir_path, i) for i in items]
    items_paths = [
        p
        for p in items_paths
        if os.path.basename(p) != ".git" and not gitignore.is_ignored(p)
    ]
    # Directories first, then files; both alphabetical.
    items_paths.sort(key=lambda x: (not os.path.isdir(x), x.lower()))

    n = len(items_paths)
    for i, item_path in enumerate(items_paths, 1):
        item = os.path.basename(item_path)
        connector = "├── " if i < n else "└── "
        tree += f"{prefix}{connector}{item}\n"
        if os.path.isdir(item_path):
            extension = "│   " if i < n else "    "
            tree += generate_directory_tree(
                item_path, repo_path, gitignore, prefix + extension
            )
    return tree


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------


def _is_important_file(fpath: str) -> bool:
    """True for files we want in the prompt even without a matching pattern."""
    name = os.path.basename(fpath)
    if name in IMPORTANT_FILENAMES:
        return True
    # Common prefixed variants
    if name.startswith("Dockerfile") or name.startswith("dockerfile"):
        return True
    if name.startswith("Containerfile"):
        return True
    if name.startswith("docker-compose"):
        return True
    if name.startswith("Makefile") or name.startswith("makefile"):
        return True
    return False


def _matches_any_pattern(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def collect_files(
    repo_path: str,
    patterns: list[str],
    gitignore: GitignoreFilter,
    include_important: bool = True,
) -> list[str]:
    """
    Walk *repo_path* and return absolute file paths to include.  A file is
    included if it matches one of *patterns* or is recognised as important
    (and ``include_important`` is True).  ``.gitignore`` is always respected.
    """
    collected: list[str] = []
    seen: set[str] = set()
    repo_path = os.path.abspath(repo_path)

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = sorted(
            d
            for d in dirs
            if d != ".git" and not gitignore.is_ignored(os.path.join(root, d))
        )
        for fname in sorted(files):
            fpath = os.path.join(root, fname)
            if fpath in seen:
                continue
            if gitignore.is_ignored(fpath):
                continue

            include = False
            if patterns and _matches_any_pattern(fname, patterns):
                include = True
            elif include_important and _is_important_file(fpath):
                include = True

            if include:
                collected.append(fpath)
                seen.add(fpath)

    return collected


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------


def _looks_binary(path: str) -> bool:
    """Quick binary sniff: presence of a NUL byte in the first 8 KiB."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
        return b"\x00" in chunk
    except OSError:
        return False


def write_file_contents(f_out, relative_path: str, file_path: str) -> None:
    """Write the contents of *file_path* to *f_out* with a header banner."""
    f_out.write(f"\nFile: {relative_path}\n")
    if _looks_binary(file_path):
        f_out.write("[binary file omitted]\n")
        return
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f_in:
            f_out.write(f_in.read())
        f_out.write("\n")
    except OSError as exc:
        f_out.write(f"[Could not read file: {exc}]\n")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def traverse_and_copy(
    repo_path: str,
    patterns: list[str],
    output_file: str,
    include_important: bool = True,
    show_language_stats: bool = True,
    verbose: bool = False,
) -> int:
    """
    Analyse the repo, collect matching files, write the prompt.

    Parameters
    ----------
    repo_path           : root directory of the repository
    patterns            : list of glob patterns (e.g. ["*.py", "*.js"])
    output_file         : path to the output text file
    include_important   : auto-include Dockerfile / Makefile / etc.
    show_language_stats : print the language breakdown to stdout
    verbose             : extra diagnostic output to stderr

    Returns
    -------
    The number of files written.
    """
    import sys

    repo_path = os.path.abspath(repo_path)

    # 1. Warn if not a Git repository
    git_dir = os.path.join(repo_path, ".git")
    if not os.path.exists(git_dir):
        print(
            f"Warning: no .git directory found in '{repo_path}'. "
            "This does not appear to be a Git repository. "
            ".gitignore-based filtering will still work if a .gitignore exists.",
            file=sys.stderr,
        )

    # 2. Build the gitignore filter
    gitignore = GitignoreFilter(repo_path, verbose=verbose)

    # 3. Detect languages
    lang_stats = detect_languages(repo_path, gitignore)
    if show_language_stats and lang_stats:
        print("\nLanguage breakdown (by lines of code):")
        for lang, pct in lang_stats.items():
            bar = "█" * max(1, int(pct / 2))
            print(f"  {lang:<8} {pct:>5.1f}%  {bar}")
        print()

    # 4. Collect files
    files = collect_files(repo_path, patterns, gitignore, include_important)

    if verbose:
        print(
            f"[repo2prompt] Collected {len(files)} file(s); "
            f"patterns={patterns!r}; include_important={include_important}",
            file=sys.stderr,
        )
        if not files:
            # Helpful diagnostic when nothing matches.
            print(
                "[repo2prompt] No files matched.  "
                "Tips: run with --detect-langs to see what's in the repo, "
                "or add explicit --pattern '*.ext' globs.",
                file=sys.stderr,
            )

    # 5. Write the output
    directory_tree = generate_directory_tree(repo_path, repo_path, gitignore)

    with open(output_file, "w", encoding="utf-8") as f_out:
        f_out.write("Project Structure:\n")
        f_out.write(directory_tree + "\n")

        if lang_stats:
            f_out.write("Language breakdown (lines of code):\n")
            for lang, pct in lang_stats.items():
                f_out.write(f"  {lang}: {pct}%\n")
            f_out.write("\n")

        for fpath in files:
            relative_path = os.path.relpath(fpath, start=repo_path)
            write_file_contents(f_out, relative_path, fpath)

    print(f"Wrote {len(files)} file(s) to '{output_file}'.")
    return len(files)
