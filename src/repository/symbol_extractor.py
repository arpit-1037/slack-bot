"""AST-based Python symbol extraction."""

from __future__ import annotations

import ast
from typing import TypedDict

from src.utils.helpers import get_logger

log = get_logger(__name__)


class ImportInfo(TypedDict, total=False):
    """Structured import metadata."""

    type: str
    module: str
    name: str
    alias: str | None
    line_start: int
    line_end: int


class FunctionInfo(TypedDict, total=False):
    """Structured function metadata."""

    name: str
    line_start: int
    line_end: int
    docstring: str | None
    arguments: list[str]


class MethodInfo(FunctionInfo, total=False):
    """Structured method metadata."""


class ClassInfo(TypedDict, total=False):
    """Structured class metadata."""

    name: str
    line_start: int
    line_end: int
    docstring: str | None
    arguments: list[str]
    methods: list[MethodInfo]


class SymbolMap(TypedDict):
    """Symbols extracted from one source file."""

    functions: list[FunctionInfo]
    classes: list[ClassInfo]
    imports: list[ImportInfo]


class SymbolExtractor:
    """Extract Python functions, classes, methods, and imports using Python AST."""

    def extract(self, content: str, file_path: str = "<memory>") -> SymbolMap:
        """Return structured symbols from Python source content."""
        try:
            tree = ast.parse(content, filename=file_path)
        except SyntaxError as error:
            log.warning("Could not parse Python file %s: %s", file_path, error)
            return empty_symbol_map()

        functions: list[FunctionInfo] = []
        classes: list[ClassInfo] = []
        imports: list[ImportInfo] = []

        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imports.extend(self._extract_imports(node))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(self._extract_function(node))
            elif isinstance(node, ast.ClassDef):
                classes.append(self._extract_class(node))

        return {
            "functions": functions,
            "classes": classes,
            "imports": imports,
        }

    def _extract_imports(self, node: ast.Import | ast.ImportFrom) -> list[ImportInfo]:
        """Extract import entries from an import AST node."""
        line_end = getattr(node, "end_lineno", node.lineno)

        if isinstance(node, ast.Import):
            return [
                {
                    "type": "import",
                    "module": alias.name,
                    "name": alias.name,
                    "alias": alias.asname,
                    "line_start": node.lineno,
                    "line_end": line_end,
                }
                for alias in node.names
            ]

        module = "." * node.level + (node.module or "")
        return [
            {
                "type": "from_import",
                "module": module,
                "name": alias.name,
                "alias": alias.asname,
                "line_start": node.lineno,
                "line_end": line_end,
            }
            for alias in node.names
        ]

    def _extract_class(self, node: ast.ClassDef) -> ClassInfo:
        """Extract class metadata and direct methods."""
        methods = [
            self._extract_function(child)
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        init_method = next((method for method in methods if method["name"] == "__init__"), None)

        return {
            "name": node.name,
            "line_start": node.lineno,
            "line_end": getattr(node, "end_lineno", node.lineno),
            "docstring": ast.get_docstring(node),
            "arguments": init_method.get("arguments", []) if init_method else [],
            "methods": methods,
        }

    def _extract_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> FunctionInfo:
        """Extract function or method metadata."""
        return {
            "name": node.name,
            "line_start": node.lineno,
            "line_end": getattr(node, "end_lineno", node.lineno),
            "docstring": ast.get_docstring(node),
            "arguments": self._extract_arguments(node.args),
        }

    def _extract_arguments(self, args: ast.arguments) -> list[str]:
        """Return readable argument names from AST function arguments."""
        names = [arg.arg for arg in args.posonlyargs + args.args]
        if args.vararg:
            names.append(f"*{args.vararg.arg}")
        names.extend(arg.arg for arg in args.kwonlyargs)
        if args.kwarg:
            names.append(f"**{args.kwarg.arg}")
        return names


def empty_symbol_map() -> SymbolMap:
    """Return an empty symbol map for unsupported or unparsable files."""
    return {"functions": [], "classes": [], "imports": []}
