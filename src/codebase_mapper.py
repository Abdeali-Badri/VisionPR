"""Build a lightweight map of the VisionPR codebase.

The mapper walks the local repository, renders a text file tree, extracts
Python symbols with the standard-library ``ast`` module, and writes the result
to ``data/output_json/codebase_map.json``.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Iterable


SKIP_NAMES = {".git", "node_modules", "__pycache__", ".env"}
OUTPUT_PATH = Path("data") / "output_json" / "codebase_map.json"


def find_project_root(start: Path | None = None) -> Path:
    """Return the nearest parent directory that looks like the repo root."""
    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate

    return Path.cwd().resolve()


def should_skip(path: Path) -> bool:
    """Return True when a path should be excluded from traversal entirely."""
    return path.name in SKIP_NAMES


def visible_children(directory: Path) -> list[Path]:
    """Return sorted, non-skipped children for stable tree output."""
    children = [child for child in directory.iterdir() if not should_skip(child)]
    return sorted(children, key=lambda child: (not child.is_dir(), child.name.lower()))


def iter_repo_files(root: Path) -> Iterable[Path]:
    """Yield all non-skipped files under root."""
    for child in visible_children(root):
        if child.is_dir():
            yield from iter_repo_files(child)
        elif child.is_file():
            yield child


def build_file_tree(root: Path) -> tuple[str, int]:
    """Build a tree-command-style representation of the repository."""
    lines = ["."]
    file_count = 0

    def add_entries(directory: Path, prefix: str = "") -> None:
        nonlocal file_count
        children = visible_children(directory)

        for index, child in enumerate(children):
            is_last = index == len(children) - 1
            connector = "`-- " if is_last else "|-- "
            relative_name = child.name + ("/" if child.is_dir() else "")
            lines.append(f"{prefix}{connector}{relative_name}")

            if child.is_dir():
                extension = "    " if is_last else "|   "
                add_entries(child, prefix + extension)
            elif child.is_file():
                file_count += 1

    add_entries(root)
    return "\n".join(lines), file_count


class SymbolVisitor(ast.NodeVisitor):
    """Collect class and function declarations with class context."""

    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.class_stack: list[str] = []
        self.function_stack: list[str] = []
        self.symbols: list[dict[str, int | str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified_name = ".".join([*self.class_stack, node.name])
        self.symbols.append(
            {
                "type": "class",
                "name": qualified_name,
                "file": self.relative_path,
                "line": node.lineno,
            }
        )

        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if self.class_stack and not self.function_stack:
            symbol_type = "method"
            name = ".".join([*self.class_stack, node.name])
        else:
            symbol_type = "function"
            name_parts = [*self.class_stack, *self.function_stack, node.name]
            name = ".".join(name_parts) if name_parts else node.name

        self.symbols.append(
            {
                "type": symbol_type,
                "name": name,
                "file": self.relative_path,
                "line": node.lineno,
            }
        )

        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()


def extract_python_symbols(file_path: Path, root: Path) -> list[dict[str, int | str]]:
    """Extract classes, functions, and methods from a Python file."""
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    relative_path = file_path.relative_to(root).as_posix()
    visitor = SymbolVisitor(relative_path)
    visitor.visit(tree)
    return visitor.symbols


def build_codebase_map(root: Path | None = None) -> tuple[dict[str, object], int]:
    """Create the codebase map payload and return it with the scanned file count."""
    project_root = root or find_project_root()
    file_tree, files_scanned = build_file_tree(project_root)

    code_symbols: list[dict[str, int | str]] = []
    for file_path in iter_repo_files(project_root):
        if file_path.suffix == ".py":
            code_symbols.extend(extract_python_symbols(file_path, project_root))

    return {"file_tree": file_tree, "code_symbols": code_symbols}, files_scanned


def save_codebase_map(payload: dict[str, object], root: Path | None = None) -> Path:
    """Write the codebase map JSON and return the output path."""
    project_root = root or find_project_root()
    output_path = project_root / OUTPUT_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_path


def main() -> None:
    """Run the mapper and print a short summary."""
    project_root = find_project_root()
    payload, files_scanned = build_codebase_map(project_root)
    output_path = save_codebase_map(payload, project_root)
    symbols_found = len(payload["code_symbols"])

    print(f"Codebase map saved to {output_path.relative_to(project_root).as_posix()}")
    print(f"Files scanned: {files_scanned}; symbols found: {symbols_found}")


if __name__ == "__main__":
    main()
