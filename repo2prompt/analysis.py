"""Static repository analysis for dependency maps and symbol signatures.

The analyzer deliberately uses only the Python standard library.  Python files
are parsed with :mod:`ast`; JavaScript and TypeScript use conservative,
brace-aware scanners; several other common languages have lightweight import
and declaration extractors.  Parse failures are reported as warnings and never
prevent prompt generation.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence
from urllib.parse import urlsplit

_MAX_SIGNATURE_LENGTH = 320
_JS_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".json")
_SOURCE_ROOT_NAMES = {"src", "lib", "python"}


@dataclass(frozen=True, slots=True)
class Symbol:
    """A named declaration extracted from a source file."""

    name: str
    kind: str
    signature: str
    line: int
    parent: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "signature": self.signature,
            "line": self.line,
            "parent": self.parent,
        }


@dataclass(frozen=True, slots=True)
class Dependency:
    """A resolved or unresolved dependency originating in a source file."""

    source: str
    specifier: str
    line: int
    kind: str = "import"
    target: str | None = None
    external: bool = False
    standard_library: bool = False
    unresolved_local: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "specifier": self.specifier,
            "line": self.line,
            "kind": self.kind,
            "target": self.target,
            "external": self.external,
            "standard_library": self.standard_library,
            "unresolved_local": self.unresolved_local,
        }


@dataclass(slots=True)
class FileAnalysis:
    """Static metadata for one selected file."""

    path: str
    language: str
    lines: int
    bytes: int
    symbols: list[Symbol] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "language": self.language,
            "lines": self.lines,
            "bytes": self.bytes,
            "symbols": [symbol.to_dict() for symbol in self.symbols],
            "dependencies": [dependency.to_dict() for dependency in self.dependencies],
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class ProjectAnalysis:
    """Complete static-analysis result for a selected set of repository files."""

    repo_path: str
    files: dict[str, FileAnalysis]
    cycles: list[tuple[str, ...]] = field(default_factory=list)

    @property
    def internal_dependencies(self) -> list[Dependency]:
        return [
            dependency
            for file_info in self.files.values()
            for dependency in file_info.dependencies
            if dependency.target is not None
        ]

    @property
    def external_dependencies(self) -> list[Dependency]:
        return [
            dependency
            for file_info in self.files.values()
            for dependency in file_info.dependencies
            if dependency.external
        ]

    @property
    def standard_library_dependencies(self) -> list[Dependency]:
        return [
            dependency
            for file_info in self.files.values()
            for dependency in file_info.dependencies
            if dependency.standard_library
        ]

    @property
    def unique_internal_edges(self) -> set[tuple[str, str, str]]:
        return {
            (dependency.source, dependency.target, dependency.kind)
            for dependency in self.internal_dependencies
            if dependency.target is not None
        }

    @property
    def unresolved_local_dependencies(self) -> list[Dependency]:
        return [
            dependency
            for file_info in self.files.values()
            for dependency in file_info.dependencies
            if dependency.unresolved_local
        ]

    @property
    def symbol_count(self) -> int:
        return sum(len(file_info.symbols) for file_info in self.files.values())

    @property
    def total_lines(self) -> int:
        return sum(file_info.lines for file_info in self.files.values())

    @property
    def total_bytes(self) -> int:
        return sum(file_info.bytes for file_info in self.files.values())

    @property
    def approximate_tokens(self) -> int:
        # A deliberately labelled approximation suitable for source-heavy text.
        return (self.total_bytes + 3) // 4

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "repository": self.repo_path,
            "stats": {
                "files": len(self.files),
                "lines": self.total_lines,
                "bytes": self.total_bytes,
                "approximate_tokens": self.approximate_tokens,
                "symbols": self.symbol_count,
                "internal_dependencies": len(self.unique_internal_edges),
                "internal_import_statements": len(self.internal_dependencies),
                "external_dependencies": len(self.external_dependencies),
                "standard_library_dependencies": len(
                    self.standard_library_dependencies
                ),
                "unresolved_local_dependencies": len(
                    self.unresolved_local_dependencies
                ),
                "dependency_cycles": len(self.cycles),
            },
            "cycles": [list(cycle) for cycle in self.cycles],
            "files": [self.files[path].to_dict() for path in sorted(self.files)],
        }


@dataclass(frozen=True, slots=True)
class _RawDependency:
    specifier: str
    line: int
    kind: str = "import"
    candidates: tuple[str, ...] = ()
    external_hint: bool | None = None


@dataclass(slots=True)
class _ParsedFile:
    symbols: list[Symbol] = field(default_factory=list)
    dependencies: list[_RawDependency] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_project(
    repo_path: str,
    file_paths: Sequence[str],
    extension_map: dict[str, str] | None = None,
) -> ProjectAnalysis:
    """Analyze selected files and resolve local file dependencies.

    ``file_paths`` may contain configuration or documentation files.  Such files
    still contribute to size statistics, while declaration extraction is only
    attempted for recognized source formats.
    """

    root = Path(repo_path).resolve()
    if extension_map is None:
        from .languages import EXT_TO_LANG

        extension_map = EXT_TO_LANG
    files: dict[str, FileAnalysis] = {}
    raw_dependencies: dict[str, list[_RawDependency]] = {}
    go_modules: set[str] = set()
    root_go_mod = root / "go.mod"
    if root_go_mod.is_file():
        try:
            module_match = re.search(
                r"(?m)^\s*module\s+(\S+)",
                root_go_mod.read_text(encoding="utf-8", errors="replace"),
            )
            if module_match:
                go_modules.add(module_match.group(1).strip())
        except OSError:
            pass

    resolved_paths: set[str] = set()
    for path in file_paths:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved_paths.add(str(candidate.resolve()))

    for raw_path in sorted(resolved_paths):
        absolute = Path(raw_path)
        try:
            relative = absolute.relative_to(root).as_posix()
        except ValueError:
            continue

        try:
            data = absolute.read_bytes()
        except OSError as exc:
            files[relative] = FileAnalysis(
                path=relative,
                language="unknown",
                lines=0,
                bytes=0,
                warnings=[f"Could not read file: {exc}"],
            )
            raw_dependencies[relative] = []
            continue

        if b"\x00" in data[:8192]:
            files[relative] = FileAnalysis(
                path=relative,
                language="binary",
                lines=0,
                bytes=0,
                warnings=["Binary file omitted from static analysis."],
            )
            raw_dependencies[relative] = []
            continue

        text = data.decode("utf-8", errors="replace")
        if absolute.name == "go.mod":
            module_match = re.search(r"(?m)^\s*module\s+(\S+)", text)
            if module_match:
                go_modules.add(module_match.group(1).strip())
        line_count = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        language = extension_map.get(absolute.suffix.lower(), "unknown")
        parsed = _analyze_text(text, relative, language)
        files[relative] = FileAnalysis(
            path=relative,
            language=language,
            lines=line_count,
            bytes=len(data),
            symbols=_dedupe_symbols(parsed.symbols),
            warnings=parsed.warnings,
        )
        raw_dependencies[relative] = parsed.dependencies

    all_paths = set(files)
    python_module_index = _build_python_module_index(all_paths)
    directory_index = _build_directory_index(all_paths)

    for source, raw_refs in raw_dependencies.items():
        resolved: list[Dependency] = []
        for raw_ref in raw_refs:
            resolved.extend(
                _resolve_raw_dependency(
                    source=source,
                    language=files[source].language,
                    raw_ref=raw_ref,
                    all_paths=all_paths,
                    python_module_index=python_module_index,
                    directory_index=directory_index,
                    go_modules=go_modules,
                )
            )
        files[source].dependencies = _dedupe_dependencies(resolved)

    project = ProjectAnalysis(repo_path=str(root), files=files)
    project.cycles = _find_dependency_cycles(project.internal_dependencies, all_paths)
    return project


def render_project_analysis(
    analysis: ProjectAnalysis,
    *,
    dependency_format: str = "text",
    max_symbols_per_file: int = 50,
    include_external_dependencies: bool = True,
) -> str:
    """Render analysis as a compact prompt-friendly text section."""

    if dependency_format not in {"text", "mermaid", "both"}:
        raise ValueError("dependency_format must be 'text', 'mermaid', or 'both'")
    if max_symbols_per_file < 0:
        raise ValueError("max_symbols_per_file must be zero or positive")

    lines: list[str] = ["Code Analysis:\n"]
    lines.extend(
        [
            f"  Files scanned: {len(analysis.files)}\n",
            f"  Source lines: {analysis.total_lines}\n",
            f"  Source bytes: {analysis.total_bytes}\n",
            f"  Approximate source tokens: {analysis.approximate_tokens}\n",
            f"  Symbols: {analysis.symbol_count}\n",
            f"  Internal dependency edges: {len(analysis.unique_internal_edges)}\n",
            f"  External package imports: {len(analysis.external_dependencies)}\n",
            f"  Standard-library imports: {len(analysis.standard_library_dependencies)}\n",
            f"  Unresolved local dependencies: {len(analysis.unresolved_local_dependencies)}\n",
            f"  Dependency cycles: {len(analysis.cycles)}\n",
        ]
    )

    if dependency_format in {"text", "both"}:
        lines.append("\nFile dependency map:\n")
        lines.extend(_render_dependency_text(analysis, include_external_dependencies))

    if dependency_format in {"mermaid", "both"}:
        lines.append("\nDependency diagram (Mermaid):\n")
        lines.append(_render_mermaid(analysis))

    if analysis.cycles:
        lines.append("\nDependency cycle groups:\n")
        for cycle in analysis.cycles:
            lines.append("  - " + " <-> ".join(f"`{path}`" for path in cycle) + "\n")

    hotspots = Counter(
        target for _source, target, _kind in analysis.unique_internal_edges
    )
    if hotspots:
        lines.append("\nMost imported internal files:\n")
        for path, count in sorted(
            hotspots.items(), key=lambda item: (-item[1], item[0])
        )[:10]:
            lines.append(f"  - `{path}` <- {count} import(s)\n")

    lines.append("\nSymbol signatures:\n")
    symbol_files = [
        file_info for file_info in analysis.files.values() if file_info.symbols
    ]
    if not symbol_files:
        lines.append("  [No supported declarations found.]\n")
    else:
        for file_info in sorted(symbol_files, key=lambda item: item.path):
            lines.append(f"\n  `{file_info.path}` ({file_info.language}):\n")
            selected = file_info.symbols
            if max_symbols_per_file:
                selected = selected[:max_symbols_per_file]
            for symbol in selected:
                parent = f" [{symbol.parent}]" if symbol.parent else ""
                lines.append(
                    f"    L{symbol.line} {symbol.kind}{parent}: {symbol.signature}\n"
                )
            omitted = len(file_info.symbols) - len(selected)
            if omitted > 0:
                lines.append(f"    ... {omitted} more symbol(s) omitted\n")

    warnings = [
        (file_info.path, warning)
        for file_info in analysis.files.values()
        for warning in file_info.warnings
    ]
    if warnings:
        lines.append("\nAnalysis warnings:\n")
        for path, warning in sorted(warnings):
            lines.append(f"  - `{path}`: {warning}\n")

    return "".join(lines).rstrip() + "\n"


def write_analysis_json(analysis: ProjectAnalysis, output_path: str) -> None:
    """Write a stable, machine-readable analysis document."""

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(analysis.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Language analyzers
# ---------------------------------------------------------------------------


def _analyze_text(text: str, relative_path: str, language: str) -> _ParsedFile:
    if language == "py":
        return _analyze_python(text)
    if language in {"js", "ts"}:
        return _analyze_js_ts(text, language)
    if language == "html":
        return _analyze_html(text)
    if language in {"css", "scss", "less"}:
        return _analyze_css(text)
    if language in {"c", "cpp", "objc"}:
        return _analyze_c_family(text, language)
    if language == "go":
        return _analyze_go(text)
    if language == "rs":
        return _analyze_rust(text)
    if language in {"java", "cs", "kt", "scala"}:
        return _analyze_jvm_family(text, language)
    if language == "rb":
        return _analyze_ruby(text)
    if language == "php":
        return _analyze_php(text)
    return _ParsedFile()


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.symbols: list[Symbol] = []
        self.scope: list[tuple[str, str]] = []

    @property
    def parent(self) -> str | None:
        if not self.scope:
            return None
        return ".".join(name for name, _kind in self.scope)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = [ast.unparse(base) for base in node.bases]
        bases.extend(
            f"{keyword.arg}={ast.unparse(keyword.value)}" for keyword in node.keywords
        )
        type_params = getattr(node, "type_params", [])
        name = node.name
        if type_params:
            name += "[" + ", ".join(ast.unparse(param) for param in type_params) + "]"
        suffix = f"({', '.join(bases)})" if bases else ""
        self.symbols.append(
            Symbol(
                name=node.name,
                kind="class",
                signature=_limit_signature(f"class {name}{suffix}:"),
                line=node.lineno,
                parent=self.parent,
            )
        )
        self.scope.append((node.name, "class"))
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, asynchronous=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, asynchronous=True)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        asynchronous: bool,
    ) -> None:
        decorators = {_decorator_name(decorator) for decorator in node.decorator_list}
        parent_kind = self.scope[-1][1] if self.scope else None
        if "property" in decorators or any(
            name.endswith(".setter") for name in decorators
        ):
            kind = "property"
        elif parent_kind == "class":
            kind = "method"
        else:
            kind = "function"

        prefix_parts: list[str] = []
        for marker in ("classmethod", "staticmethod", "abstractmethod"):
            if marker in decorators:
                prefix_parts.append(f"@{marker}")
        if asynchronous:
            prefix_parts.append("async")
        prefix_parts.append("def")

        type_params = getattr(node, "type_params", [])
        name = node.name
        if type_params:
            name += "[" + ", ".join(ast.unparse(param) for param in type_params) + "]"
        signature = f"{' '.join(prefix_parts)} {name}({ast.unparse(node.args)})"
        if node.returns is not None:
            signature += f" -> {ast.unparse(node.returns)}"
        signature += ":"

        self.symbols.append(
            Symbol(
                name=node.name,
                kind=kind,
                signature=_limit_signature(signature),
                line=node.lineno,
                parent=self.parent,
            )
        )
        self.scope.append((node.name, "function"))
        self.generic_visit(node)
        self.scope.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        if not self.scope:
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    self.symbols.append(
                        Symbol(
                            name=target.id,
                            kind="constant",
                            signature=_limit_signature(
                                f"{target.id} = {_safe_unparse(node.value, max_length=160)}"
                            ),
                            line=node.lineno,
                        )
                    )
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if (
            not self.scope
            and isinstance(node.target, ast.Name)
            and node.target.id.isupper()
        ):
            signature = f"{node.target.id}: {ast.unparse(node.annotation)}"
            if node.value is not None:
                signature += f" = {_safe_unparse(node.value, max_length=160)}"
            self.symbols.append(
                Symbol(
                    name=node.target.id,
                    kind="constant",
                    signature=_limit_signature(signature),
                    line=node.lineno,
                )
            )
        self.generic_visit(node)


def _analyze_python(text: str) -> _ParsedFile:
    parsed = _ParsedFile()
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        location = f"line {exc.lineno}" if exc.lineno else "unknown line"
        parsed.warnings.append(f"Python syntax error at {location}: {exc.msg}")
        return parsed

    visitor = _PythonVisitor()
    visitor.visit(tree)
    parsed.symbols = visitor.symbols

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parsed.dependencies.append(
                    _RawDependency(
                        specifier=alias.name,
                        line=node.lineno,
                        candidates=(alias.name,),
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level + (node.module or "")
            for alias in node.names:
                candidates: list[str] = []
                if alias.name != "*":
                    candidate = (
                        f"{prefix}.{alias.name}"
                        if prefix and not prefix.endswith(".")
                        else f"{prefix}{alias.name}"
                    )
                    candidates.append(candidate)
                if prefix:
                    candidates.append(prefix)
                parsed.dependencies.append(
                    _RawDependency(
                        specifier=prefix or alias.name,
                        line=node.lineno,
                        candidates=tuple(dict.fromkeys(candidates)),
                    )
                )
    return parsed


def _analyze_js_ts(text: str, language: str) -> _ParsedFile:
    parsed = _ParsedFile()
    masked = _mask_comments(text)

    import_patterns = [
        re.compile(
            r"\bimport\s+(?:type\s+)?(?:[\s\S]{0,500}?\s+from\s+)?[\"'](?P<target>[^\"']+)[\"']",
            re.MULTILINE,
        ),
        re.compile(
            r"\bexport\s+(?:type\s+)?(?:\*|\{[\s\S]{0,500}?\})\s+from\s+[\"'](?P<target>[^\"']+)[\"']",
            re.MULTILINE,
        ),
        re.compile(r"\brequire\s*\(\s*[\"'](?P<target>[^\"']+)[\"']\s*\)"),
        re.compile(r"\bimport\s*\(\s*[\"'](?P<target>[^\"']+)[\"']\s*\)"),
    ]
    seen_imports: set[tuple[int, str]] = set()
    for pattern in import_patterns:
        for match in pattern.finditer(text):
            target = match.group("target")
            line = _line_at(text, match.start())
            key = (line, target)
            if key in seen_imports:
                continue
            seen_imports.add(key)
            parsed.dependencies.append(
                _RawDependency(
                    specifier=target,
                    line=line,
                    candidates=(target,),
                    external_hint=not _is_local_specifier(target),
                )
            )

    declaration_patterns: list[tuple[str, re.Pattern[str]]] = [
        (
            "class",
            re.compile(
                r"(?m)^[ \t]*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)\b"
            ),
        ),
        (
            "interface",
            re.compile(
                r"(?m)^[ \t]*(?:export\s+)?interface\s+(?P<name>[A-Za-z_$][\w$]*)\b"
            ),
        ),
        (
            "enum",
            re.compile(
                r"(?m)^[ \t]*(?:export\s+)?(?:const\s+)?enum\s+(?P<name>[A-Za-z_$][\w$]*)\b"
            ),
        ),
        (
            "type",
            re.compile(r"(?m)^[ \t]*(?:export\s+)?type\s+(?P<name>[A-Za-z_$][\w$]*)\b"),
        ),
        (
            "function",
            re.compile(
                r"(?m)^[ \t]*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\b"
            ),
        ),
    ]

    class_ranges: list[tuple[str, int, int, int]] = []
    for kind, pattern in declaration_patterns:
        for match in pattern.finditer(masked):
            name = match.group("name")
            signature, terminator_index = _extract_declaration_signature(
                text, match.start()
            )
            if not signature:
                continue
            parsed.symbols.append(
                Symbol(
                    name=name,
                    kind=kind,
                    signature=_limit_signature(signature),
                    line=_line_at(text, match.start()),
                )
            )
            if (
                kind == "class"
                and terminator_index is not None
                and text[terminator_index] == "{"
            ):
                end = _find_matching_brace(masked, terminator_index)
                if end is not None:
                    class_ranges.append((name, match.start(), terminator_index, end))

    arrow_pattern = re.compile(
        r"(?m)^[ \t]*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=\s*(?:async\s+)?(?:<[^;{}\n]+>\s*)?(?:\([^;{}]*?\)|[A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=>"
    )
    for match in arrow_pattern.finditer(masked):
        signature, _ = _extract_declaration_signature(text, match.start(), arrow=True)
        if signature:
            parsed.symbols.append(
                Symbol(
                    name=match.group("name"),
                    kind="function",
                    signature=_limit_signature(signature),
                    line=_line_at(text, match.start()),
                )
            )

    for class_name, _class_start, body_start, body_end in class_ranges:
        parsed.symbols.extend(
            _extract_js_class_methods(text, masked, class_name, body_start, body_end)
        )

    return parsed


class _HTMLDependencyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.dependencies: list[_RawDependency] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value for name, value in attrs}
        target: str | None = None
        kind = "asset"
        if tag.lower() == "script" and values.get("src"):
            target = values["src"]
            kind = "script"
        elif tag.lower() == "link" and values.get("href"):
            rel = (values.get("rel") or "").lower()
            if any(token in rel for token in ("stylesheet", "modulepreload", "import")):
                target = values["href"]
                kind = "stylesheet" if "stylesheet" in rel else "asset"
        if target:
            self.dependencies.append(
                _RawDependency(
                    specifier=target,
                    line=self.getpos()[0],
                    kind=kind,
                    candidates=(target,),
                    external_hint=not _is_web_local_specifier(target),
                )
            )


def _analyze_html(text: str) -> _ParsedFile:
    parser = _HTMLDependencyParser()
    parsed = _ParsedFile()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:  # HTMLParser is tolerant, but custom input may still fail.
        parsed.warnings.append(f"HTML parsing warning: {exc}")
    parsed.dependencies = parser.dependencies
    return parsed


def _analyze_css(text: str) -> _ParsedFile:
    parsed = _ParsedFile()
    patterns = [
        re.compile(r"@import\s+(?:url\(\s*)?[\"'](?P<target>[^\"']+)[\"']\s*\)?", re.I),
        re.compile(r"@use\s+[\"'](?P<target>[^\"']+)[\"']", re.I),
        re.compile(r"@forward\s+[\"'](?P<target>[^\"']+)[\"']", re.I),
    ]
    for pattern in patterns:
        for match in pattern.finditer(text):
            target = match.group("target")
            parsed.dependencies.append(
                _RawDependency(
                    specifier=target,
                    line=_line_at(text, match.start()),
                    kind="stylesheet",
                    candidates=(target,),
                    external_hint=not _is_web_local_specifier(target),
                )
            )
    return parsed


def _analyze_c_family(text: str, language: str) -> _ParsedFile:
    parsed = _ParsedFile()
    for match in re.finditer(
        r"(?m)^\s*#\s*include\s*(?P<open>[<\"])(?P<target>[^>\"]+)[>\"]", text
    ):
        target = match.group("target")
        parsed.dependencies.append(
            _RawDependency(
                specifier=target,
                line=_line_at(text, match.start()),
                candidates=(target,),
                external_hint=match.group("open") == "<",
            )
        )

    class_pattern = re.compile(
        r"(?m)^\s*(?:template\s*<[^;{]+>\s*)?(?:class|struct|enum(?:\s+class)?)\s+(?P<name>[A-Za-z_]\w*)[^;{]*[;{]"
    )
    for match in class_pattern.finditer(text):
        signature, _ = _extract_declaration_signature(text, match.start())
        parsed.symbols.append(
            Symbol(
                name=match.group("name"),
                kind="type",
                signature=_limit_signature(signature),
                line=_line_at(text, match.start()),
            )
        )

    function_pattern = re.compile(
        r"(?m)^\s*(?!if\b|for\b|while\b|switch\b|return\b)(?:[A-Za-z_]\w*[\w:<>,*&\s]+\s+)+(?P<name>[A-Za-z_]\w*(?:::\w+)*)\s*\([^;{}]*\)\s*(?:const\s*)?(?:noexcept\s*)?[{;]"
    )
    for match in function_pattern.finditer(text):
        signature, _ = _extract_declaration_signature(text, match.start())
        parsed.symbols.append(
            Symbol(
                name=match.group("name").split("::")[-1],
                kind="function",
                signature=_limit_signature(signature),
                line=_line_at(text, match.start()),
            )
        )
    return parsed


def _analyze_go(text: str) -> _ParsedFile:
    parsed = _ParsedFile()
    for match in re.finditer(
        r"(?m)^\s*import\s+(?:[A-Za-z_.]+\s+)?[\"`]([^\"`]+)[\"`]", text
    ):
        target = match.group(1)
        parsed.dependencies.append(
            _RawDependency(
                target,
                _line_at(text, match.start()),
                candidates=(target,),
                external_hint=True,
            )
        )
    for block in re.finditer(r"(?ms)^\s*import\s*\((?P<body>.*?)\)", text):
        body_start = block.start("body")
        for match in re.finditer(
            r"(?:^|\n)\s*(?:[A-Za-z_.]+\s+)?[\"`]([^\"`]+)[\"`]", block.group("body")
        ):
            target = match.group(1)
            parsed.dependencies.append(
                _RawDependency(
                    target,
                    _line_at(text, body_start + match.start()),
                    candidates=(target,),
                    external_hint=True,
                )
            )

    patterns = [
        ("function", re.compile(r"(?m)^\s*func\s+(?P<name>[A-Za-z_]\w*)\s*\(")),
        (
            "method",
            re.compile(r"(?m)^\s*func\s*\([^)]*\)\s*(?P<name>[A-Za-z_]\w*)\s*\("),
        ),
        (
            "type",
            re.compile(
                r"(?m)^\s*type\s+(?P<name>[A-Za-z_]\w*)\s+(?:struct|interface)\b"
            ),
        ),
    ]
    for kind, pattern in patterns:
        for match in pattern.finditer(text):
            signature, _ = _extract_declaration_signature(text, match.start())
            parsed.symbols.append(
                Symbol(
                    match.group("name"),
                    kind,
                    _limit_signature(signature),
                    _line_at(text, match.start()),
                )
            )
    return parsed


def _analyze_rust(text: str) -> _ParsedFile:
    parsed = _ParsedFile()
    for match in re.finditer(
        r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?use\s+(?P<target>[^;]+);", text
    ):
        target = match.group("target").strip()
        parsed.dependencies.append(
            _RawDependency(
                target,
                _line_at(text, match.start()),
                candidates=(target,),
                external_hint=not target.startswith(("crate::", "self::", "super::")),
            )
        )
    for match in re.finditer(
        r"(?m)^\s*(?:pub\s+)?mod\s+(?P<target>[A-Za-z_]\w*)\s*;", text
    ):
        target = match.group("target")
        parsed.dependencies.append(
            _RawDependency(
                target,
                _line_at(text, match.start()),
                kind="module",
                candidates=(target,),
                external_hint=False,
            )
        )
    patterns = [
        (
            "function",
            re.compile(
                r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(?P<name>[A-Za-z_]\w*)\b"
            ),
        ),
        (
            "type",
            re.compile(
                r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|type)\s+(?P<name>[A-Za-z_]\w*)\b"
            ),
        ),
    ]
    for kind, pattern in patterns:
        for match in pattern.finditer(text):
            signature, _ = _extract_declaration_signature(text, match.start())
            parsed.symbols.append(
                Symbol(
                    match.group("name"),
                    kind,
                    _limit_signature(signature),
                    _line_at(text, match.start()),
                )
            )
    return parsed


def _analyze_jvm_family(text: str, language: str) -> _ParsedFile:
    parsed = _ParsedFile()
    if language == "java":
        import_pattern = re.compile(
            r"(?m)^\s*import\s+(?:static\s+)?(?P<target>[\w.]+)\s*;"
        )
    elif language == "cs":
        import_pattern = re.compile(
            r"(?m)^\s*(?:global\s+)?using\s+(?P<target>[\w.]+)\s*;"
        )
    else:
        import_pattern = re.compile(r"(?m)^\s*import\s+(?P<target>[\w.]+)")
    for match in import_pattern.finditer(text):
        target = match.group("target")
        parsed.dependencies.append(
            _RawDependency(
                target,
                _line_at(text, match.start()),
                candidates=(target,),
                external_hint=True,
            )
        )

    type_pattern = re.compile(
        r"(?m)^\s*(?:public|private|protected|internal|open|abstract|final|sealed|data|static|partial|export|\s)*\b(?:class|interface|enum|record|object|trait)\s+(?P<name>[A-Za-z_]\w*)\b"
    )
    for match in type_pattern.finditer(text):
        signature, _ = _extract_declaration_signature(text, match.start())
        parsed.symbols.append(
            Symbol(
                match.group("name"),
                "type",
                _limit_signature(signature),
                _line_at(text, match.start()),
            )
        )
    return parsed


def _analyze_ruby(text: str) -> _ParsedFile:
    parsed = _ParsedFile()
    for match in re.finditer(
        r"(?m)^\s*require_relative\s*[\( ]?[\"'](?P<target>[^\"']+)", text
    ):
        target = match.group("target")
        parsed.dependencies.append(
            _RawDependency(
                target,
                _line_at(text, match.start()),
                candidates=(target,),
                external_hint=False,
            )
        )
    for match in re.finditer(r"(?m)^\s*require\s*[\( ]?[\"'](?P<target>[^\"']+)", text):
        target = match.group("target")
        parsed.dependencies.append(
            _RawDependency(
                target,
                _line_at(text, match.start()),
                candidates=(target,),
                external_hint=True,
            )
        )
    for kind, pattern in [
        ("class", re.compile(r"(?m)^\s*class\s+(?P<name>[A-Z]\w*(?:::\w+)*)")),
        ("module", re.compile(r"(?m)^\s*module\s+(?P<name>[A-Z]\w*(?:::\w+)*)")),
        (
            "function",
            re.compile(r"(?m)^\s*def\s+(?P<name>(?:self\.)?[A-Za-z_]\w*[!?=]?)"),
        ),
    ]:
        for match in pattern.finditer(text):
            parsed.symbols.append(
                Symbol(
                    match.group("name"),
                    kind,
                    _source_line(text, _line_at(text, match.start())),
                    _line_at(text, match.start()),
                )
            )
    return parsed


def _analyze_php(text: str) -> _ParsedFile:
    parsed = _ParsedFile()
    for match in re.finditer(
        r"(?mi)^\s*(?:require|require_once|include|include_once)\s*\(?\s*[\"'](?P<target>[^\"']+)",
        text,
    ):
        target = match.group("target")
        parsed.dependencies.append(
            _RawDependency(
                target,
                _line_at(text, match.start()),
                candidates=(target,),
                external_hint=not _is_local_specifier(target),
            )
        )
    for kind, pattern in [
        (
            "class",
            re.compile(
                r"(?mi)^\s*(?:abstract\s+|final\s+)?class\s+(?P<name>[A-Za-z_]\w*)"
            ),
        ),
        ("interface", re.compile(r"(?mi)^\s*interface\s+(?P<name>[A-Za-z_]\w*)")),
        (
            "function",
            re.compile(
                r"(?mi)^\s*(?:public|protected|private|static|final|abstract|\s)*function\s+&?\s*(?P<name>[A-Za-z_]\w*)\s*\("
            ),
        ),
    ]:
        for match in pattern.finditer(text):
            signature, _ = _extract_declaration_signature(text, match.start())
            parsed.symbols.append(
                Symbol(
                    match.group("name"),
                    kind,
                    _limit_signature(signature),
                    _line_at(text, match.start()),
                )
            )
    return parsed


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _resolve_raw_dependency(
    *,
    source: str,
    language: str,
    raw_ref: _RawDependency,
    all_paths: set[str],
    python_module_index: dict[str, set[str]],
    directory_index: dict[str, list[str]],
    go_modules: set[str],
) -> list[Dependency]:
    targets: list[str] = []
    for candidate in raw_ref.candidates or (raw_ref.specifier,):
        if language == "py":
            targets = _resolve_python(candidate, source, all_paths, python_module_index)
        elif language in {"js", "ts", "html", "css", "scss", "less", "rb", "php"}:
            targets = _resolve_path_like(candidate, source, all_paths, language)
        elif language in {"c", "cpp", "objc"}:
            targets = _resolve_include(candidate, source, all_paths)
        elif language == "go":
            targets = _resolve_go(candidate, directory_index, go_modules)
        elif language == "rs":
            targets = _resolve_rust(candidate, source, all_paths)
        elif language in {"java", "cs", "kt", "scala"}:
            targets = _resolve_dotted_type(candidate, all_paths, language)
        else:
            targets = []
        if targets:
            break

    if targets:
        return [
            Dependency(
                source=source,
                specifier=raw_ref.specifier,
                line=raw_ref.line,
                kind=raw_ref.kind,
                target=target,
            )
            for target in sorted(set(targets))
            if target != source
        ]

    external = raw_ref.external_hint
    if external is None:
        external = not any(
            _is_local_specifier(candidate) for candidate in raw_ref.candidates
        )

    standard_library = False
    if language == "py" and external:
        candidate = next(iter(raw_ref.candidates), raw_ref.specifier)
        top_level = candidate.lstrip(".").split(".", 1)[0]
        standard_library = top_level in getattr(sys, "stdlib_module_names", set())
        if standard_library:
            external = False

    return [
        Dependency(
            source=source,
            specifier=raw_ref.specifier,
            line=raw_ref.line,
            kind=raw_ref.kind,
            external=bool(external),
            standard_library=standard_library,
            unresolved_local=not bool(external) and not standard_library,
        )
    ]


def _build_python_module_index(paths: Iterable[str]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for path in paths:
        if not path.endswith((".py", ".pyi")):
            continue
        pure = PurePosixPath(path)
        parts = list(pure.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        if not parts:
            continue
        aliases = {".".join(parts)}
        if parts[0] in _SOURCE_ROOT_NAMES and len(parts) > 1:
            aliases.add(".".join(parts[1:]))
        for alias in aliases:
            index[alias].add(path)
    return index


def _resolve_python(
    candidate: str,
    source: str,
    all_paths: set[str],
    module_index: dict[str, set[str]],
) -> list[str]:
    candidate = candidate.strip()
    if not candidate:
        return []
    dots = len(candidate) - len(candidate.lstrip("."))
    remainder = candidate[dots:]

    if dots:
        package_parts = list(PurePosixPath(source).parent.parts)
        ascend = max(0, dots - 1)
        if ascend > len(package_parts):
            return []
        if ascend:
            package_parts = package_parts[:-ascend]
        module_parts = package_parts + (
            [part for part in remainder.split(".") if part] if remainder else []
        )
        module_name = ".".join(module_parts)
        targets = sorted(module_index.get(module_name, set()))
        if targets:
            return targets
        base = PurePosixPath(*package_parts) if package_parts else PurePosixPath(".")
        path_part = (
            PurePosixPath(*remainder.split(".")) if remainder else PurePosixPath("")
        )
        return _existing_candidates(
            [base / path_part.with_suffix(".py"), base / path_part / "__init__.py"],
            all_paths,
        )

    targets = sorted(module_index.get(candidate, set()))
    if targets:
        return targets

    source_dir = PurePosixPath(source).parent
    basename = candidate.rsplit(".", 1)[-1]
    same_dir = [source_dir / f"{basename}.py", source_dir / basename / "__init__.py"]
    return _existing_candidates(same_dir, all_paths)


def _resolve_path_like(
    specifier: str,
    source: str,
    all_paths: set[str],
    language: str,
) -> list[str]:
    cleaned = _clean_url_path(specifier)
    web_like = language in {"html", "css", "scss", "less", "php"}
    is_local = (
        _is_web_local_specifier(specifier) if web_like else _is_local_specifier(cleaned)
    )
    if not cleaned or not is_local:
        return []
    if cleaned.startswith("/"):
        base = PurePosixPath(cleaned.lstrip("/"))
    else:
        base = PurePosixPath(source).parent / cleaned
    base = PurePosixPath(os.path.normpath(str(base)).replace(os.sep, "/"))

    candidates: list[PurePosixPath] = [base]
    suffix = base.suffix.lower()
    if language == "ts" and suffix in {".js", ".jsx", ".mjs", ".cjs"}:
        # TypeScript ESM commonly writes runtime .js specifiers while the
        # repository contains .ts/.tsx/.mts/.cts source files.
        candidates.extend(
            base.with_suffix(extension) for extension in (".ts", ".tsx", ".mts", ".cts")
        )
    if not suffix:
        extensions = list(_JS_EXTENSIONS)
        if language in {"css", "scss", "less"}:
            extensions = [".css", ".scss", ".sass", ".less"]
        elif language == "rb":
            extensions = [".rb"]
        elif language == "php":
            extensions = [".php"]
        elif language == "html":
            extensions = list(_JS_EXTENSIONS) + [".css", ".html", ".htm"]
        for extension in extensions:
            candidates.append(PurePosixPath(f"{base}{extension}"))
        for extension in extensions:
            candidates.append(base / f"index{extension}")
        if language in {"css", "scss"}:
            # Sass partial imports may omit a leading underscore and extension.
            parent = base.parent
            name = base.name
            for extension in (".scss", ".sass", ".css"):
                candidates.append(parent / f"_{name}{extension}")
    return _existing_candidates(candidates, all_paths)


def _resolve_include(specifier: str, source: str, all_paths: set[str]) -> list[str]:
    source_dir = PurePosixPath(source).parent
    candidates = [source_dir / specifier, PurePosixPath(specifier)]
    direct = _existing_candidates(candidates, all_paths)
    if direct:
        return direct
    basename = PurePosixPath(specifier).name
    matches = sorted(path for path in all_paths if PurePosixPath(path).name == basename)
    return matches if len(matches) == 1 else []


def _build_directory_index(paths: Iterable[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        result[str(PurePosixPath(path).parent)].append(path)
    return {directory: sorted(items) for directory, items in result.items()}


def _resolve_go(
    specifier: str,
    directory_index: dict[str, list[str]],
    module_names: set[str],
) -> list[str]:
    normalized = specifier.strip("/")
    candidates = [normalized]
    for module_name in sorted(module_names, key=len, reverse=True):
        if normalized == module_name:
            candidates.insert(0, ".")
        elif normalized.startswith(module_name + "/"):
            candidates.insert(0, normalized[len(module_name) + 1 :])

    for candidate in candidates:
        matches: list[str] = []
        for directory, files in directory_index.items():
            if directory == candidate or directory.endswith(f"/{candidate}"):
                matches.extend(path for path in files if path.endswith(".go"))
        if matches:
            return sorted(set(matches))
    return []


def _resolve_rust(specifier: str, source: str, all_paths: set[str]) -> list[str]:
    root = specifier.split("::{", 1)[0].rstrip(":")
    source_dir = PurePosixPath(source).parent
    prefix: PurePosixPath
    if root.startswith("crate::"):
        prefix = PurePosixPath("src")
        parts = root.removeprefix("crate::").split("::")
    elif root.startswith("self::"):
        prefix = source_dir
        parts = root.removeprefix("self::").split("::")
    elif root.startswith("super::"):
        prefix = source_dir.parent
        parts = root.removeprefix("super::").split("::")
    elif "::" not in root:
        prefix = source_dir
        parts = [root]
    else:
        return []

    # The final path segment is often an imported symbol rather than a module,
    # so progressively trim the right-hand side until a source file matches.
    while parts:
        base = prefix / PurePosixPath(*parts)
        targets = _existing_candidates(
            [PurePosixPath(f"{base}.rs"), base / "mod.rs"],
            all_paths,
        )
        if targets:
            return targets
        parts.pop()
    return []


def _resolve_dotted_type(
    specifier: str, all_paths: set[str], language: str
) -> list[str]:
    base = specifier.replace(".", "/")
    extensions = {
        "java": (".java",),
        "cs": (".cs",),
        "kt": (".kt", ".kts"),
        "scala": (".scala",),
    }[language]
    candidates: list[str] = []
    for extension in extensions:
        candidates.append(f"{base}{extension}")
    for path in all_paths:
        normalized = path.replace("\\", "/")
        if any(
            normalized.endswith(f"/{candidate}") or normalized == candidate
            for candidate in candidates
        ):
            return [path]
    return []


def _existing_candidates(
    candidates: Iterable[PurePosixPath], all_paths: set[str]
) -> list[str]:
    for candidate in candidates:
        normalized = posix_normalize(str(candidate))
        if normalized in all_paths:
            return [normalized]
    return []


# ---------------------------------------------------------------------------
# Rendering and graph helpers
# ---------------------------------------------------------------------------


def _render_dependency_text(
    analysis: ProjectAnalysis,
    include_external_dependencies: bool,
) -> list[str]:
    lines: list[str] = []
    dependency_files = [
        file_info
        for file_info in analysis.files.values()
        if any(
            dependency.target is not None
            or dependency.unresolved_local
            or (include_external_dependencies and dependency.external)
            for dependency in file_info.dependencies
        )
    ]
    if not dependency_files:
        return ["  [No dependencies found.]\n"]

    for file_info in sorted(dependency_files, key=lambda item: item.path):
        lines.append(f"  `{file_info.path}`\n")
        grouped: dict[tuple[str, str, str], list[int]] = defaultdict(list)
        for dependency in file_info.dependencies:
            if dependency.target is not None:
                grouped[("internal", dependency.target, dependency.kind)].append(
                    dependency.line
                )
            elif dependency.unresolved_local:
                grouped[("unresolved", dependency.specifier, dependency.kind)].append(
                    dependency.line
                )
            elif include_external_dependencies and dependency.external:
                grouped[("external", dependency.specifier, dependency.kind)].append(
                    dependency.line
                )

        for (category, target, kind), import_lines in sorted(
            grouped.items(),
            key=lambda item: (min(item[1]), item[0][0], item[0][1]),
        ):
            locations = ", ".join(f"L{line}" for line in sorted(set(import_lines)))
            if category == "internal":
                lines.append(f"    -> `{target}` [{kind}, {locations}]\n")
            elif category == "unresolved":
                lines.append(f"    ?  `{target}` [unresolved local, {locations}]\n")
            else:
                lines.append(f"    => `{target}` [external, {locations}]\n")
    return lines


def _render_mermaid(analysis: ProjectAnalysis) -> str:
    internal_edges = sorted(
        {
            (dependency.source, dependency.target, dependency.kind)
            for dependency in analysis.internal_dependencies
            if dependency.target is not None
        }
    )
    involved = sorted(
        {source for source, _target, _kind in internal_edges}
        | {target for _source, target, _kind in internal_edges}
    )
    if not involved:
        return '```mermaid\nflowchart LR\n  empty["No internal dependencies"]\n```\n'

    identifiers = {path: f"f{index}" for index, path in enumerate(involved)}
    lines = ["```mermaid\n", "flowchart LR\n"]
    for path in involved:
        label = path.replace("\\", "\\\\").replace('"', "&quot;")
        lines.append(f'  {identifiers[path]}["{label}"]\n')
    for source, target, kind in internal_edges:
        label = kind.replace('"', "&quot;")
        lines.append(f"  {identifiers[source]} -->|{label}| {identifiers[target]}\n")
    lines.append("```\n")
    return "".join(lines)


def _find_dependency_cycles(
    dependencies: Sequence[Dependency],
    all_paths: set[str],
) -> list[tuple[str, ...]]:
    """Find strongly connected file groups without recursive DFS.

    An iterative Kosaraju pass avoids Python's recursion limit on repositories
    containing thousands of files in a long dependency chain.
    """

    adjacency: dict[str, set[str]] = {path: set() for path in all_paths}
    reverse: dict[str, set[str]] = {path: set() for path in all_paths}
    for dependency in dependencies:
        if dependency.target is not None and dependency.target != dependency.source:
            adjacency.setdefault(dependency.source, set()).add(dependency.target)
            reverse.setdefault(dependency.target, set()).add(dependency.source)

    visited: set[str] = set()
    finish_order: list[str] = []
    for start_node in sorted(all_paths):
        if start_node in visited:
            continue
        stack: list[tuple[str, bool]] = [(start_node, False)]
        while stack:
            node, expanded = stack.pop()
            if expanded:
                finish_order.append(node)
                continue
            if node in visited:
                continue
            visited.add(node)
            stack.append((node, True))
            for target in sorted(adjacency.get(node, ()), reverse=True):
                if target not in visited:
                    stack.append((target, False))

    components: list[tuple[str, ...]] = []
    visited.clear()
    for start_node in reversed(finish_order):
        if start_node in visited:
            continue
        component: list[str] = []
        stack = [start_node]
        visited.add(start_node)
        while stack:
            node = stack.pop()
            component.append(node)
            for source in sorted(reverse.get(node, ()), reverse=True):
                if source not in visited:
                    visited.add(source)
                    stack.append(source)
        if len(component) > 1:
            components.append(tuple(sorted(component)))

    return sorted(components)


# ---------------------------------------------------------------------------
# Scanner helpers
# ---------------------------------------------------------------------------


def _extract_declaration_signature(
    text: str,
    start: int,
    *,
    arrow: bool = False,
) -> tuple[str, int | None]:
    """Read a declaration header until its top-level body/terminator."""

    paren = bracket = angle = 0
    quote: str | None = None
    escaped = False
    index = start
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""

        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue

        if char in {'"', "'", "`"}:
            quote = char
        elif char == "(":
            paren += 1
        elif char == ")":
            paren = max(0, paren - 1)
        elif char == "[":
            bracket += 1
        elif char == "]":
            bracket = max(0, bracket - 1)
        elif char == "<" and _looks_like_generic_start(text, index):
            angle += 1
        elif char == ">" and angle:
            angle -= 1
        elif (
            arrow
            and char == "="
            and next_char == ">"
            and not (paren or bracket or angle)
        ):
            end = index + 2
            return _compact_signature(text[start:end]), index
        elif char in "{;" and not (paren or bracket or angle):
            end = index + 1
            return _compact_signature(text[start:end]), index
        index += 1

    line_end = text.find("\n", start)
    if line_end == -1:
        line_end = len(text)
    return _compact_signature(text[start:line_end]), None


def _extract_js_class_methods(
    text: str,
    masked: str,
    class_name: str,
    body_start: int,
    body_end: int,
) -> list[Symbol]:
    symbols: list[Symbol] = []
    depth = 1
    line_depth = 1
    line_start = body_start + 1
    index = body_start + 1
    while index < body_end:
        char = masked[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        if char == "\n" or index == body_end - 1:
            end = index if char == "\n" else index + 1
            if line_depth == 1:
                line = masked[line_start:end]
                method_match = re.match(
                    r"^[ \t]*(?:(?:public|private|protected|static|readonly|abstract|async|override|declare|get|set)\s+)*(?P<name>constructor|[A-Za-z_$][\w$]*)\s*(?:<[^>{};]+>)?\s*\(",
                    line,
                )
                if method_match and method_match.group("name") not in {
                    "if",
                    "for",
                    "while",
                    "switch",
                    "catch",
                }:
                    absolute_start = line_start + method_match.start()
                    signature, _ = _extract_declaration_signature(text, absolute_start)
                    if signature:
                        name = method_match.group("name")
                        symbols.append(
                            Symbol(
                                name=name,
                                kind=(
                                    "constructor" if name == "constructor" else "method"
                                ),
                                signature=_limit_signature(signature),
                                line=_line_at(text, absolute_start),
                                parent=class_name,
                            )
                        )
            line_start = index + 1
            line_depth = depth
        index += 1
    return symbols


def _find_matching_brace(masked: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(masked)):
        char = masked[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _mask_comments(text: str) -> str:
    """Replace JS/C-style comments with spaces while preserving offsets/newlines."""

    chars = list(text)
    index = 0
    quote: str | None = None
    escaped = False
    while index < len(chars):
        char = chars[index]
        next_char = chars[index + 1] if index + 1 < len(chars) else ""
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {'"', "'", "`"}:
            quote = char
            index += 1
            continue
        if char == "/" and next_char == "/":
            chars[index] = chars[index + 1] = " "
            index += 2
            while index < len(chars) and chars[index] != "\n":
                chars[index] = " "
                index += 1
            continue
        if char == "/" and next_char == "*":
            chars[index] = chars[index + 1] = " "
            index += 2
            while index + 1 < len(chars):
                if chars[index] == "*" and chars[index + 1] == "/":
                    chars[index] = chars[index + 1] = " "
                    index += 2
                    break
                if chars[index] != "\n":
                    chars[index] = " "
                index += 1
            continue
        index += 1
    return "".join(chars)


def _looks_like_generic_start(text: str, index: int) -> bool:
    before = text[index - 1] if index > 0 else ""
    after = text[index + 1] if index + 1 < len(text) else ""
    return (before.isalnum() or before in "_$])") and (
        after.isalnum() or after in "_$[{("
    )


def _compact_signature(text: str) -> str:
    return _limit_signature(re.sub(r"\s+", " ", text.strip()))


def _limit_signature(signature: str) -> str:
    if len(signature) <= _MAX_SIGNATURE_LENGTH:
        return signature
    return signature[: _MAX_SIGNATURE_LENGTH - 3].rstrip() + "..."


def _safe_unparse(node: ast.AST, *, max_length: int) -> str:
    try:
        value = ast.unparse(node)
    except Exception:
        return "..."
    if len(value) > max_length:
        return value[: max_length - 3].rstrip() + "..."
    return value


def _decorator_name(node: ast.expr) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _source_line(text: str, line_number: int) -> str:
    lines = text.splitlines()
    if 1 <= line_number <= len(lines):
        return lines[line_number - 1].strip()
    return ""


def _line_at(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _clean_url_path(specifier: str) -> str:
    if specifier.startswith(("data:", "http://", "https://", "//", "mailto:", "tel:")):
        return specifier
    parsed = urlsplit(specifier)
    return parsed.path


def _is_local_specifier(specifier: str) -> bool:
    return specifier.startswith((".", "/")) and not specifier.startswith("//")


def _is_web_local_specifier(specifier: str) -> bool:
    if not specifier or specifier.startswith(
        ("#", "//", "data:", "http://", "https://", "mailto:", "tel:")
    ):
        return False
    parsed = urlsplit(specifier)
    return not parsed.scheme and not parsed.netloc and bool(parsed.path)


def posix_normalize(path: str) -> str:
    normalized = os.path.normpath(path).replace(os.sep, "/")
    return normalized.removeprefix("./")


def _dedupe_symbols(symbols: Sequence[Symbol]) -> list[Symbol]:
    seen: set[tuple[int, str, str, str | None]] = set()
    result: list[Symbol] = []
    for symbol in sorted(symbols, key=lambda item: (item.line, item.kind, item.name)):
        key = (symbol.line, symbol.kind, symbol.name, symbol.parent)
        if key in seen:
            continue
        seen.add(key)
        result.append(symbol)
    return result


def _dedupe_dependencies(dependencies: Sequence[Dependency]) -> list[Dependency]:
    seen: set[tuple[str | None, str, int, bool, bool, bool, str]] = set()
    result: list[Dependency] = []
    for dependency in sorted(
        dependencies,
        key=lambda item: (
            item.line,
            item.target or "",
            item.specifier,
            item.kind,
        ),
    ):
        key = (
            dependency.target,
            dependency.specifier,
            dependency.line,
            dependency.external,
            dependency.standard_library,
            dependency.unresolved_local,
            dependency.kind,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(dependency)
    return result
