# Changelog

## 0.4.0

- Added file-to-file dependency analysis with internal, external, and unresolved references.
- Added text and Mermaid dependency maps.
- Added dependency-cycle detection and reverse-import hotspot reporting.
- Added Python AST symbol extraction for classes, functions, methods, properties, and constants.
- Added JavaScript/TypeScript signatures for classes, methods, functions, interfaces, enums, and type aliases.
- Added HTML/CSS dependency extraction and lightweight analyzers for C-family, Go, Rust, JVM languages, Ruby, and PHP.
- Added `--analysis-only`, `--analysis-json`, `--dependency-format`, `--max-symbols-per-file`, `--no-analysis`, and `--no-external-deps`.
- Added prompt-size statistics and approximate token counts.
- Added `--version` and a development dependency group for tests and linting.
- Distinguished Python standard-library imports from third-party packages.
- Split the language registry into a neutral module to keep the analyzer architecture acyclic.
- Excluded generated output files from collection, language statistics, and directory trees.
- Fixed physical line counting for files without a trailing newline.

## 0.3.0

- Expanded language flags to include HTML, CSS, data/configuration formats, infrastructure formats, and additional programming languages.
