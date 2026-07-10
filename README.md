# repo2prompt

`repo2prompt` turns a repository into one structured, LLM-ready text file. It
respects nested `.gitignore` files, prints a directory tree, reports language
percentages, builds a file dependency map, extracts function/class/type
signatures, and then appends the selected source files.

The analyzer is local and dependency-light: Python uses the standard-library
AST, while JavaScript/TypeScript and other supported formats use conservative
static scanners. Parse failures are reported in the output instead of stopping
prompt generation.

## Requirements

Python 3.10 or later.

## Installation

```bash
git clone https://github.com/mmkuznecov/repo2prompt.git
cd repo2prompt
pip install -e .
```

## Quick start

```bash
repo2prompt ./myproject --py --ts --html --css -o prompt.txt
```

The generated prompt begins with:

1. Project tree and language percentages.
2. Prompt-size estimates and analysis statistics.
3. File-to-file dependency map.
4. Dependency cycles and the most imported internal files.
5. Function, method, class, interface, enum, type, and constant signatures.
6. Full contents of the selected files.

Code analysis is enabled by default. Use `--no-analysis` for the legacy output
layout.

## Dependency maps

The default compact text map is optimized for LLM input:

```text
File dependency map:
  `src/app.py`
    -> `src/auth.py` [import, L3]
    => `fastapi` [external, L1]
```

Generate a Mermaid diagram instead, or include both forms:

```bash
repo2prompt ./myproject --py --dependency-format mermaid
repo2prompt ./myproject --py --dependency-format both
```

Mermaid output uses stable generated node identifiers, so paths containing
spaces or punctuation remain valid labels.

## Analysis-only mode

Create a compact architectural map without copying full source contents:

```bash
repo2prompt ./myproject --py --ts --analysis-only -o architecture.txt
```

This is useful for repository discovery, planning agents, documentation, and
large codebases where complete source would exceed the context window.

## Machine-readable JSON

Write the same analysis as a standalone JSON document:

```bash
repo2prompt ./myproject --py --ts \
  --analysis-json build/code-map.json \
  -o build/prompt.txt
```

The JSON contains stable schema version `1`, repository statistics, symbols,
resolved internal dependencies, external dependencies, unresolved local
references, warnings, and dependency-cycle groups.

## Symbol limits

By default, up to 50 signatures are shown per file in the text prompt. The JSON
output always contains every extracted signature.

```bash
# Show at most 20 signatures per file
repo2prompt ./myproject --py --max-symbols-per-file 20

# Show every signature
repo2prompt ./myproject --py --max-symbols-per-file 0
```

## Static-analysis coverage

| Format | Dependency extraction | Signature extraction |
|---|---|---|
| Python | AST-based imports, including relative imports and `src/` layouts | AST-based classes, functions, async functions, methods, properties, and module constants |
| JavaScript / TypeScript | `import`, re-export, `require()`, and dynamic `import()` | Classes, methods, constructors, functions, arrow functions, interfaces, enums, and type aliases |
| HTML | Local module scripts and stylesheet/module-preload links | — |
| CSS / Sass / Less | `@import`, `@use`, and `@forward` | — |
| C / C++ / Objective-C | Local and system `#include` references | Lightweight type and function declarations |
| Go | Single/block imports with `go.mod` module resolution | Functions, methods, structs, and interfaces |
| Rust | `use` and `mod` references | Functions, structs, enums, traits, and type aliases |
| Java / C# / Kotlin / Scala | Package/type imports | Lightweight class/interface/enum/record declarations |
| Ruby / PHP | `require_relative`, `require`, include/require statements | Lightweight classes/modules/functions |

Other recognized languages are still selected, counted, and copied normally;
they simply produce no declaration entries until a language-specific analyzer
is added. Static analysis is intentionally conservative and does not execute
project code. Dynamic imports, generated sources, runtime module loading, and
custom TypeScript path aliases may remain external or unresolved.

## File selection

Select files with repeatable glob patterns, language flags, or both:

```bash
repo2prompt ./myproject --html --css --js --output web_prompt.txt
repo2prompt ./myproject --py --yaml --pattern "*.jinja2" -o prompt.txt
```

When no `--pattern` or language flag is supplied, the tool includes only its
recognized important project files. Use `--no-important` to disable that
automatic inclusion.

Generated prompt and analysis JSON paths are automatically excluded from file
selection, language statistics, and the directory tree, including on repeated
runs.

### Main options

- `-p, --pattern GLOB`: Include files whose basename matches a glob. Repeatable.
- `-o, --output PATH`: Write the generated prompt. Defaults to `prompt.txt`.
- `--no-important`: Do not automatically include important project files.
- `--detect-langs`: Print the detected language breakdown and exit.
- `--no-lang-stats`: Do not print language statistics during generation.
- `-v, --verbose`: Print diagnostic information to standard error.
- `--version`: Print the installed `repo2prompt` version.

### Analysis options

- `--no-analysis`: Disable dependency and symbol analysis.
- `--analysis-only`: Omit full file contents.
- `--dependency-format {text,mermaid,both}`: Select dependency-map rendering.
- `--analysis-json PATH`: Write a standalone JSON code map.
- `--max-symbols-per-file N`: Limit displayed signatures; `0` is unlimited.
- `--no-external-deps`: Hide external packages from the text dependency map.

### Language flags

Every supported language has a generated shorthand flag. Flags can be freely
combined. Common examples include:

- Web: `--html`, `--css`, `--scss`, `--less`, `--js`, `--ts`, `--vue`, `--svelte`, `--astro`
- Application: `--py`, `--java`, `--go`, `--rs`, `--rb`, `--php`, `--cs`, `--swift`, `--kt`
- Data/config: `--json`, `--yaml`, `--toml`, `--xml`, `--md`, `--sql`, `--graphql`, `--proto`
- Automation/infra: `--sh`, `--ps1`, `--bat`, `--tf`, `--nix`

Run `repo2prompt --help` for the complete generated list and associated file
extensions.

## Python API

```python
from repo2prompt import analyze_project, render_project_analysis

analysis = analyze_project(
    repo_path="./myproject",
    file_paths=["./myproject/src/app.py", "./myproject/src/models.py"],
)
print(render_project_analysis(analysis, dependency_format="both"))
```

The existing `traverse_and_copy()` API remains compatible; all new parameters
are optional keyword arguments.

## Development checks

```bash
pip install -e ".[dev]"
ruff check .
ruff format --check .
pytest -q
```
