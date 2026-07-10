from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from repo2prompt.analysis import (
    Dependency,
    _find_dependency_cycles,
    analyze_project,
    render_project_analysis,
    write_analysis_json,
)
from repo2prompt.core import EXT_TO_LANG


class AnalysisTests(unittest.TestCase):
    def analyze(self, files: dict[str, str]):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        paths: list[str] = []
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            paths.append(str(path))
        return root, analyze_project(str(root), paths, EXT_TO_LANG)

    def test_python_symbols_relative_imports_external_imports_and_warnings(
        self,
    ) -> None:
        root, analysis = self.analyze(
            {
                "src/acme/__init__.py": "from .service import Service\n",
                "src/acme/models.py": "class User:\n    pass\n",
                "src/acme/service.py": (
                    "from .models import User\n"
                    "import os\n"
                    "import requests\n\n"
                    "MAX_RETRIES: int = 3\n\n"
                    "class Service(Base):\n"
                    "    @classmethod\n"
                    "    async def load(cls, user_id: int = 1) -> User:\n"
                    "        return User()\n"
                ),
                "main.py": "from acme.service import Service\n",
                "broken.py": "def broken(:\n",
            }
        )

        service = analysis.files["src/acme/service.py"]
        signatures = {symbol.name: symbol for symbol in service.symbols}
        self.assertEqual(signatures["Service"].signature, "class Service(Base):")
        self.assertEqual(signatures["MAX_RETRIES"].kind, "constant")
        self.assertEqual(signatures["load"].kind, "method")
        self.assertEqual(signatures["load"].parent, "Service")
        self.assertIn("@classmethod async def load", signatures["load"].signature)

        internal = {(dep.source, dep.target) for dep in analysis.internal_dependencies}
        self.assertIn(("main.py", "src/acme/service.py"), internal)
        self.assertIn(("src/acme/service.py", "src/acme/models.py"), internal)
        self.assertEqual(
            [dep.specifier for dep in analysis.external_dependencies],
            ["requests"],
        )
        self.assertEqual(
            [dep.specifier for dep in analysis.standard_library_dependencies],
            ["os"],
        )
        self.assertIn("Python syntax error", analysis.files["broken.py"].warnings[0])
        self.assertEqual(analysis.files["broken.py"].symbols, [])
        self.assertEqual(analysis.repo_path, str(root.resolve()))

    def test_typescript_imports_symbols_methods_and_cycle_detection(self) -> None:
        _root, analysis = self.analyze(
            {
                "src/app.ts": (
                    "import { api } from './api.js';\n"
                    "export interface User { id: number }\n"
                    "export type UserId = string | number;\n"
                    "export class Client {\n"
                    "  constructor(private base: string) {}\n"
                    "  async load(id: UserId): Promise<User> {\n"
                    "    return api(id);\n"
                    "  }\n"
                    "}\n"
                    "export const create = (base: string): Client => new Client(base);\n"
                ),
                "src/api.ts": (
                    "export type { User } from './app';\n"
                    "export async function api(id: string | number): Promise<object> {\n"
                    "  return { id };\n"
                    "}\n"
                    "const lazy = () => import('./lazy');\n"
                    "const common = require('./common');\n"
                ),
                "src/lazy.ts": "export default function lazy(): void {}\n",
                "src/common.js": "module.exports = {};\n",
            }
        )

        app_symbols = {
            (symbol.kind, symbol.name)
            for symbol in analysis.files["src/app.ts"].symbols
        }
        self.assertTrue(
            {
                ("interface", "User"),
                ("type", "UserId"),
                ("class", "Client"),
                ("constructor", "constructor"),
                ("method", "load"),
                ("function", "create"),
            }.issubset(app_symbols)
        )
        api_symbols = {symbol.name for symbol in analysis.files["src/api.ts"].symbols}
        self.assertIn("api", api_symbols)

        internal = {(dep.source, dep.target) for dep in analysis.internal_dependencies}
        self.assertIn(("src/app.ts", "src/api.ts"), internal)
        self.assertIn(("src/api.ts", "src/app.ts"), internal)
        self.assertIn(("src/api.ts", "src/lazy.ts"), internal)
        self.assertIn(("src/api.ts", "src/common.js"), internal)
        self.assertEqual(analysis.cycles, [("src/api.ts", "src/app.ts")])

    def test_html_and_css_bare_paths_are_local_dependencies(self) -> None:
        _root, analysis = self.analyze(
            {
                "web/index.html": (
                    '<link rel="stylesheet" href="styles/main.css">\n'
                    '<script type="module" src="scripts/app.js"></script>\n'
                    '<script src="https://cdn.example.test/lib.js"></script>\n'
                ),
                "web/styles/main.css": '@import "base.css";\n',
                "web/styles/base.css": "body { margin: 0; }\n",
                "web/scripts/app.js": "export function start() {}\n",
            }
        )

        internal = {(dep.source, dep.target) for dep in analysis.internal_dependencies}
        self.assertIn(("web/index.html", "web/styles/main.css"), internal)
        self.assertIn(("web/index.html", "web/scripts/app.js"), internal)
        self.assertIn(("web/styles/main.css", "web/styles/base.css"), internal)
        self.assertEqual(
            [dep.specifier for dep in analysis.external_dependencies],
            ["https://cdn.example.test/lib.js"],
        )
        self.assertEqual(analysis.unresolved_local_dependencies, [])

    def test_lightweight_c_go_rust_and_java_analysis(self) -> None:
        _root, analysis = self.analyze(
            {
                "include/local.h": "struct Item { int id; };\n",
                "src/main.c": '#include "../include/local.h"\nint run(int value) { return value; }\n',
                "go.mod": "module example.com/project\n",
                "cmd/main.go": 'package main\nimport "example.com/project/internal/util"\nfunc main() {}\n',
                "internal/util/util.go": "package util\nfunc Work() {}\n",
                "src/lib.rs": "pub mod util;\nuse crate::util::Thing;\n",
                "src/util.rs": "pub struct Thing;\n",
                "src/main/java/com/acme/App.java": (
                    "package com.acme;\nimport com.acme.Service;\npublic class App {}\n"
                ),
                "src/main/java/com/acme/Service.java": "package com.acme;\npublic class Service {}\n",
            }
        )

        internal = {(dep.source, dep.target) for dep in analysis.internal_dependencies}
        self.assertIn(("src/main.c", "include/local.h"), internal)
        self.assertIn(("cmd/main.go", "internal/util/util.go"), internal)
        self.assertIn(("src/lib.rs", "src/util.rs"), internal)
        self.assertIn(
            ("src/main/java/com/acme/App.java", "src/main/java/com/acme/Service.java"),
            internal,
        )
        self.assertIn(
            "run", {symbol.name for symbol in analysis.files["src/main.c"].symbols}
        )
        self.assertIn(
            "main", {symbol.name for symbol in analysis.files["cmd/main.go"].symbols}
        )
        self.assertIn(
            "Thing", {symbol.name for symbol in analysis.files["src/util.rs"].symbols}
        )
        self.assertIn(
            "App",
            {
                symbol.name
                for symbol in analysis.files["src/main/java/com/acme/App.java"].symbols
            },
        )

    def test_rendering_symbol_limit_mermaid_and_json(self) -> None:
        root, analysis = self.analyze(
            {
                "a.py": "from b import two\ndef one(): pass\ndef extra(): pass\n",
                "b.py": "from a import one\ndef two(): pass\n",
            }
        )
        rendered = render_project_analysis(
            analysis,
            dependency_format="both",
            max_symbols_per_file=1,
        )
        self.assertIn("Code Analysis:", rendered)
        self.assertIn("Dependency diagram (Mermaid):", rendered)
        self.assertIn("flowchart LR", rendered)
        self.assertIn("Dependency cycle groups:", rendered)
        self.assertIn("1 more symbol(s) omitted", rendered)
        self.assertEqual(rendered.count("    -> `b.py` ["), 1)

        json_path = root / "out" / "analysis.json"
        write_analysis_json(analysis, str(json_path))
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["stats"]["files"], 2)
        self.assertEqual(payload["stats"]["dependency_cycles"], 1)
        self.assertEqual(
            [entry["path"] for entry in payload["files"]], ["a.py", "b.py"]
        )

    def test_python_api_accepts_repo_relative_paths_and_default_extension_map(
        self,
    ) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "a.py").write_text(
            "from b import two\ndef one(): pass\n", encoding="utf-8"
        )
        (root / "b.py").write_text("def two(): pass\n", encoding="utf-8")

        analysis = analyze_project(str(root), ["a.py", "b.py"])

        self.assertEqual(set(analysis.files), {"a.py", "b.py"})
        self.assertEqual(
            {(dep.source, dep.target) for dep in analysis.internal_dependencies},
            {("a.py", "b.py")},
        )

    def test_binary_files_do_not_inflate_token_estimate(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        binary = root / "asset.bin"
        binary.write_bytes(b"\x00" + b"x" * 1000)

        analysis = analyze_project(str(root), [str(binary)])

        self.assertEqual(analysis.files["asset.bin"].language, "binary")
        self.assertEqual(analysis.total_bytes, 0)
        self.assertEqual(analysis.approximate_tokens, 0)

    def test_external_dependencies_can_be_hidden_from_text_but_remain_in_json(
        self,
    ) -> None:
        _root, analysis = self.analyze({"app.py": "import requests\ndef run(): pass\n"})

        rendered = render_project_analysis(
            analysis,
            include_external_dependencies=False,
        )

        self.assertNotIn("=> `requests`", rendered)
        self.assertEqual(analysis.to_dict()["stats"]["external_dependencies"], 1)
        dependency = analysis.to_dict()["files"][0]["dependencies"][0]
        self.assertTrue(dependency["external"])

    def test_cycle_detection_handles_thousands_of_files_without_recursion(self) -> None:
        paths = {f"f{index}.py" for index in range(1500)}
        dependencies = [
            Dependency(
                source=f"f{index}.py",
                target=f"f{index + 1}.py",
                specifier=f"f{index + 1}",
                line=1,
            )
            for index in range(1499)
        ]
        self.assertEqual(_find_dependency_cycles(dependencies, paths), [])

        dependencies.append(
            Dependency(
                source="f1499.py",
                target="f0.py",
                specifier="f0",
                line=1,
            )
        )
        cycles = _find_dependency_cycles(dependencies, paths)
        self.assertEqual(len(cycles), 1)
        self.assertEqual(len(cycles[0]), 1500)

    def test_unresolved_relative_import_is_not_called_external(self) -> None:
        _root, analysis = self.analyze({"pkg/app.py": "from .missing import value\n"})
        self.assertEqual(len(analysis.unresolved_local_dependencies), 1)
        self.assertFalse(analysis.unresolved_local_dependencies[0].external)
        self.assertEqual(analysis.external_dependencies, [])


if __name__ == "__main__":
    unittest.main()
