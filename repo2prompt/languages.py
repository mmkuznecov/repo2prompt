"""Language-extension registry used by selection, detection, and analysis."""

from __future__ import annotations

# Each canonical language key maps to its file-extension list.  These power
# both detection (extension → language) and the CLI shorthand flags
# (``--py``, ``--js``, ``--cpp`` …).
LANGUAGE_EXTENSIONS: dict[str, list[str]] = {
    # General-purpose languages
    "py": [".py", ".pyi"],
    "js": [".js", ".mjs", ".cjs", ".jsx"],
    "ts": [".ts", ".tsx", ".mts", ".cts"],
    "java": [".java"],
    "go": [".go"],
    "rs": [".rs"],
    "rb": [".rb"],
    "php": [".php"],
    "cs": [".cs"],
    "swift": [".swift"],
    "kt": [".kt", ".kts"],
    "scala": [".scala"],
    "dart": [".dart"],
    "groovy": [".groovy", ".gvy", ".gy", ".gsh"],
    "clj": [".clj", ".cljs", ".cljc", ".edn"],
    "fs": [".fs", ".fsi", ".fsx", ".fsscript"],
    "vb": [".vb"],
    "jl": [".jl"],
    "pl": [".pl", ".pm", ".t"],
    "zig": [".zig"],
    # C-family and systems languages
    "cpp": [".cpp", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".hxx"],
    "c": [".c", ".h"],
    "objc": [".m", ".mm"],
    "asm": [".asm", ".s", ".S"],
    "sol": [".sol"],
    # Functional and VM languages
    "ex": [".ex", ".exs"],
    "erl": [".erl", ".hrl"],
    "hs": [".hs", ".lhs"],
    "ml": [".ml", ".mli"],
    "lua": [".lua"],
    # Web and UI
    "html": [".html", ".htm", ".xhtml"],
    "css": [".css"],
    "scss": [".scss", ".sass"],
    "less": [".less"],
    "vue": [".vue"],
    "svelte": [".svelte"],
    "astro": [".astro"],
    "razor": [".razor", ".cshtml"],
    # Data, configuration, and documentation
    "json": [".json", ".jsonc", ".json5"],
    "yaml": [".yaml", ".yml"],
    "toml": [".toml"],
    "xml": [".xml", ".xsd", ".xsl", ".xslt", ".svg"],
    "md": [".md", ".markdown", ".mdx"],
    "graphql": [".graphql", ".gql"],
    "proto": [".proto"],
    "sql": [".sql"],
    # Shell, automation, and infrastructure
    "sh": [".sh", ".bash", ".zsh"],
    "ps1": [".ps1", ".psm1", ".psd1"],
    "bat": [".bat", ".cmd"],
    "tf": [".tf", ".tfvars"],
    "nix": [".nix"],
    # Scientific computing
    "r": [".r"],
}


# Reverse map: extension → canonical language key.
# When a language with overlapping extensions appears later in the dict
# (e.g. ``c`` claims ``.h``), it wins over earlier ones — fine for stats.
EXT_TO_LANG: dict[str, str] = {}
for _lang, _exts in LANGUAGE_EXTENSIONS.items():
    for _ext in _exts:
        EXT_TO_LANG[_ext.lower()] = _lang
