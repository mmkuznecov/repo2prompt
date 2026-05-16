import os
import fnmatch
import warnings
from pathlib import Path
from collections import defaultdict


# ---------------------------------------------------------------------------
# Language definitions
# ---------------------------------------------------------------------------

LANGUAGE_EXTENSIONS = {
    "py": [".py"],
    "js": [".js", ".mjs", ".cjs", ".jsx"],
    "ts": [".ts", ".tsx"],
    "cpp": [".cpp", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".h", ".hxx"],
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
    "sh": [".sh", ".bash"],
    "lua": [".lua"],
    "ex": [".ex", ".exs"],
    "hs": [".hs"],
    "ml": [".ml", ".mli"],
}

# Map each extension back to its canonical language key
EXT_TO_LANG = {}
for lang, exts in LANGUAGE_EXTENSIONS.items():
    for ext in exts:
        EXT_TO_LANG[ext] = lang

# Important non-source files that should always be included by default
IMPORTANT_FILENAMES = {
    "Dockerfile",
    "dockerfile",
    "Makefile",
    "makefile",
    "GNUmakefile",
    "CMakeLists.txt",
    "docker-compose.yml",
    "docker-compose.yaml",
    "docker-compose.override.yml",
    "docker-compose.override.yaml",
    ".env.example",
    ".env.sample",
    "requirements.txt",
    "requirements-dev.txt",
    "Pipfile",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "go.mod",
    "go.sum",
    "Cargo.toml",
    "Cargo.lock",
    "build.gradle",
    "build.gradle.kts",
    "pom.xml",
    "Gemfile",
    "Gemfile.lock",
    "composer.json",
    "composer.lock",
    "CMakeLists.txt",
    ".travis.yml",
    ".github",  # matched as prefix for workflow files
    "Jenkinsfile",
    "tox.ini",
    ".flake8",
    ".pylintrc",
    "mypy.ini",
    ".pre-commit-config.yaml",
    "README.md",
    "README.rst",
    "README.txt",
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "CHANGELOG.md",
    "CHANGELOG.rst",
    "CONTRIBUTING.md",
    ".editorconfig",
    "tsconfig.json",
    "webpack.config.js",
    "webpack.config.ts",
    "vite.config.js",
    "vite.config.ts",
    "babel.config.js",
    "babel.config.json",
    ".babelrc",
    "jest.config.js",
    "jest.config.ts",
    "rollup.config.js",
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
    "kubernetes",  # matched as directory prefix
    "k8s",
    "helm",
    "terraform",
}

IMPORTANT_EXTENSIONS = {
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".json",  # often config; filtered below for noise
}


# ---------------------------------------------------------------------------
# .gitignore parsing
# ---------------------------------------------------------------------------


def load_gitignore_patterns(repo_path: str) -> list[str]:
    """
    Read all .gitignore files in the repository (root + subdirs) and return
    a flat list of (absolute_base_dir, pattern) tuples.
    """
    patterns = []
    for root, dirs, files in os.walk(repo_path):
        # Skip .git itself
        dirs[:] = [d for d in dirs if d != ".git"]
        if ".gitignore" in files:
            gitignore_path = os.path.join(root, ".gitignore")
            try:
                with open(gitignore_path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.rstrip("\n")
                        # Strip inline comments (only if preceded by whitespace)
                        if " #" in line:
                            line = line[: line.index(" #")]
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        patterns.append((root, line))
            except OSError:
                pass
    return patterns


def _is_ignored(path: str, repo_path: str, patterns: list[tuple[str, str]]) -> bool:
    """
    Return True if *path* (absolute) matches any .gitignore pattern.
    Supports:
      - leading / (anchored to the .gitignore's base directory)
      - trailing / (directory-only match)
      - ** globs
      - negation (!) — last matching rule wins
    """
    rel_from_repo = os.path.relpath(path, repo_path)
    is_dir = os.path.isdir(path)
    result = False  # default: not ignored

    for base_dir, raw_pattern in patterns:
        negate = raw_pattern.startswith("!")
        pattern = raw_pattern.lstrip("!")

        dir_only = pattern.endswith("/")
        if dir_only:
            pattern = pattern.rstrip("/")
            if not is_dir:
                continue

        anchored = pattern.startswith("/")
        if anchored:
            pattern = pattern.lstrip("/")

        # Compute relative path from the .gitignore's location
        rel = os.path.relpath(path, base_dir)

        if anchored:
            # Match only directly under the base_dir
            matched = (
                fnmatch.fnmatch(os.path.basename(rel), pattern)
                and os.path.dirname(rel) == ""
            )
            # But also support paths like "dist/file.js" when pattern is "dist"
            if not matched:
                matched = (
                    fnmatch.fnmatch(rel, pattern)
                    or fnmatch.fnmatch(rel, pattern + "/*")
                    or rel.startswith(pattern + os.sep)
                )
        else:
            # Match against the basename OR the full relative path
            matched = (
                fnmatch.fnmatch(os.path.basename(path), pattern)
                or fnmatch.fnmatch(rel, pattern)
                or fnmatch.fnmatch(rel_from_repo, pattern)
                # Support wildcards like "**/*.pyc"
                or any(
                    fnmatch.fnmatch(part, pattern)
                    for part in rel_from_repo.split(os.sep)
                )
            )

        if matched:
            result = not negate  # negation flips the current decision

    return result


def should_ignore(path: str, repo_path: str, patterns: list[tuple[str, str]]) -> bool:
    """
    Check whether *path* or any of its parent directories (up to repo_path)
    should be ignored.
    """
    check = path
    while True:
        if _is_ignored(check, repo_path, patterns):
            return True
        parent = os.path.dirname(check)
        if parent == check or not check.startswith(repo_path):
            break
        check = parent
    return False


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


def detect_languages(
    repo_path: str, gitignore_patterns: list[tuple[str, str]]
) -> dict[str, float]:
    """
    Walk the repo and count source-code files per language (by line count).
    Returns a dict of {lang_key: percentage} sorted descending.
    """
    counts: dict[str, int] = defaultdict(int)
    total = 0

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [
            d
            for d in dirs
            if d != ".git"
            and not should_ignore(os.path.join(root, d), repo_path, gitignore_patterns)
        ]
        for fname in files:
            fpath = os.path.join(root, fname)
            if should_ignore(fpath, repo_path, gitignore_patterns):
                continue
            ext = Path(fname).suffix.lower()
            lang = EXT_TO_LANG.get(ext)
            if lang is None:
                continue
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    lines = sum(1 for _ in f)
                counts[lang] += lines
                total += lines
            except OSError:
                pass

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
    gitignore_patterns: list[tuple[str, str]],
    prefix: str = "",
) -> str:
    """
    Generates a visual representation of the directory tree,
    respecting .gitignore patterns.
    """
    tree_str = ""
    try:
        items = os.listdir(dir_path)
    except PermissionError:
        return tree_str

    items_path = [os.path.join(dir_path, i) for i in items]
    # Filter out .git and gitignored items
    items_path = [
        p
        for p in items_path
        if os.path.basename(p) != ".git"
        and not should_ignore(p, repo_path, gitignore_patterns)
    ]
    items_sorted = sorted(items_path, key=lambda x: (not os.path.isdir(x), x))

    for i, item_path in enumerate(items_sorted, 1):
        item = os.path.basename(item_path)
        connector = "├── " if i < len(items_sorted) else "└── "
        tree_str += f"{prefix}{connector}{item}\n"
        if os.path.isdir(item_path):
            extension = "│   " if i < len(items_sorted) else "    "
            tree_str += generate_directory_tree(
                item_path, repo_path, gitignore_patterns, prefix=prefix + extension
            )
    return tree_str


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------


def _is_important_file(fpath: str) -> bool:
    """Return True if the file should be included as an 'important' config/meta file."""
    name = os.path.basename(fpath)
    # Exact name match
    if name in IMPORTANT_FILENAMES:
        return True
    # Dockerfile variants: Dockerfile.prod, Dockerfile.dev, …
    if name.startswith("Dockerfile") or name.startswith("dockerfile"):
        return True
    # docker-compose variants
    if name.startswith("docker-compose"):
        return True
    return False


def collect_files(
    repo_path: str,
    patterns: list[str],
    gitignore_patterns: list[tuple[str, str]],
    include_important: bool = True,
) -> list[str]:
    """
    Walk repo_path and return sorted list of absolute file paths that match:
      - any of the glob *patterns*, OR
      - the 'important' file heuristic (when include_important=True)
    while respecting .gitignore.
    """
    collected = []
    seen = set()

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = sorted(
            d
            for d in dirs
            if d != ".git"
            and not should_ignore(os.path.join(root, d), repo_path, gitignore_patterns)
        )
        for fname in sorted(files):
            fpath = os.path.join(root, fname)
            if fpath in seen:
                continue

            is_important = include_important and _is_important_file(fpath)
            # Important files bypass .gitignore; regular source files don't.
            if not is_important and should_ignore(fpath, repo_path, gitignore_patterns):
                continue

            include = is_important
            if not include:
                for pat in patterns:
                    if fnmatch.fnmatch(fname, pat):
                        include = True
                        break

            if include:
                collected.append(fpath)
                seen.add(fpath)

    return collected


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------


def write_file_contents(f_out, relative_path: str, file_path: str) -> None:
    f_out.write(f"\nFile: {relative_path}\n")
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f_in:
            contents = f_in.read()
        f_out.write(contents + "\n")
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
) -> None:
    """
    Main function: analyse the repo, collect matching files, write the prompt.

    Parameters
    ----------
    repo_path         : root directory of the repository
    patterns          : list of glob patterns (e.g. ["*.py", "*.js"])
    output_file       : path to the output text file
    include_important : whether to auto-include Dockerfile, Makefile, etc.
    show_language_stats: whether to print language breakdown to stdout
    """
    repo_path = os.path.abspath(repo_path)

    # ------------------------------------------------------------------
    # 1. Warn if not a git repository
    # ------------------------------------------------------------------
    git_dir = os.path.join(repo_path, ".git")
    if not os.path.isdir(git_dir):
        warnings.warn(
            f"No .git directory found in '{repo_path}'. "
            "This does not appear to be a Git repository. "
            ".gitignore-based filtering will still work if the file exists.",
            stacklevel=2,
        )

    # ------------------------------------------------------------------
    # 2. Load .gitignore patterns
    # ------------------------------------------------------------------
    gitignore_patterns = load_gitignore_patterns(repo_path)

    # ------------------------------------------------------------------
    # 3. Detect languages
    # ------------------------------------------------------------------
    lang_stats = detect_languages(repo_path, gitignore_patterns)
    if show_language_stats and lang_stats:
        print("\nLanguage breakdown (by lines of code):")
        for lang, pct in lang_stats.items():
            bar = "█" * int(pct / 2)
            print(f"  {lang:<8} {pct:>5.1f}%  {bar}")
        print()

    # ------------------------------------------------------------------
    # 4. Collect files
    # ------------------------------------------------------------------
    files = collect_files(repo_path, patterns, gitignore_patterns, include_important)

    # ------------------------------------------------------------------
    # 5. Write output
    # ------------------------------------------------------------------
    directory_tree = generate_directory_tree(repo_path, repo_path, gitignore_patterns)

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

    if not files:
        hint = (
            " (tip: re-run with --detect-langs to see what's in the repo)"
            if not patterns
            else ""
        )
        print(f"Warning: 0 files collected — check your patterns and .gitignore.{hint}")
    print(f"Wrote {len(files)} file(s) to '{output_file}'.")
