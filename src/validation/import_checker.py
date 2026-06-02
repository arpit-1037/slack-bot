"""Import validation for proposed and existing Python code."""

from __future__ import annotations

import ast
import importlib.util
import os
import time
from dataclasses import dataclass, field
from typing import Mapping

from src.repository.repository_indexer import RepositoryIndexer
from src.utils.helpers import get_logger
from src.validation.validation_models import (
    ImportCheckResult,
    ValidationIssue,
    confidence_from_issues,
    result_status,
)

log = get_logger(__name__)


@dataclass(frozen=True)
class ParsedImports:
    """Parsed import data for one Python file."""

    path: str
    imports: list[ast.Import | ast.ImportFrom] = field(default_factory=list)
    parse_error: ValidationIssue | None = None


class ImportChecker:
    """Detect missing imports, invalid imports, and simple circular imports."""

    def __init__(
        self,
        indexer: RepositoryIndexer | None = None,
        strict_external_imports: bool = False,
    ) -> None:
        self.indexer = indexer or RepositoryIndexer()
        self.strict_external_imports = strict_external_imports

    def validate_imports(
        self,
        project_path: str,
        files: Mapping[str, str | None] | None = None,
    ) -> ImportCheckResult:
        """Validate imports for proposed files or the current repository."""
        start = time.monotonic()
        content_by_path = self._content_by_path(project_path, files)
        parsed = {
            path: self._parse_imports(path, content)
            for path, content in sorted(content_by_path.items())
            if content is not None and path.endswith(".py")
        }

        parse_errors = [
            item.parse_error
            for item in parsed.values()
            if item.parse_error is not None
        ]
        missing = self.detect_missing_imports(project_path, parsed, content_by_path)
        circular = self.detect_circular_imports(parsed, content_by_path)

        errors = [issue for issue in parse_errors + missing if issue.severity == "error"]
        warnings = [issue for issue in parse_errors + missing if issue.severity != "error"]
        for cycle in circular:
            warnings.append(
                ValidationIssue(
                    file_path=cycle[0] if cycle else "repository",
                    message="Circular import path: " + " -> ".join(cycle),
                    severity="warning",
                    check="circular-import",
                )
            )

        status = result_status(errors, warnings)
        summary = self._summary(status, len(parsed), len(missing), len(circular))
        return ImportCheckResult(
            name="imports",
            status=status,
            execution_time_seconds=round(time.monotonic() - start, 4),
            errors=errors,
            warnings=warnings,
            summary=summary,
            confidence_score=confidence_from_issues(errors, warnings),
            missing_imports=[issue.message for issue in missing],
            circular_imports=circular,
        )

    def detect_missing_imports(
        self,
        project_path: str,
        parsed: Mapping[str, ParsedImports] | None = None,
        content_by_path: Mapping[str, str | None] | None = None,
    ) -> list[ValidationIssue]:
        """Return missing or invalid import issues."""
        if parsed is None or content_by_path is None:
            content_by_path = self._content_by_path(project_path, None)
            parsed = {
                path: self._parse_imports(path, content)
                for path, content in sorted(content_by_path.items())
                if content is not None and path.endswith(".py")
            }

        repo_modules = self._repo_modules(content_by_path)
        issues: list[ValidationIssue] = []
        for path, parsed_file in parsed.items():
            for node in parsed_file.imports:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        issues.extend(self._check_import(path, alias.name, node.lineno, repo_modules))
                elif isinstance(node, ast.ImportFrom):
                    issues.extend(self._check_from_import(path, node, repo_modules))
        return issues

    def detect_circular_imports(
        self,
        parsed: Mapping[str, ParsedImports],
        content_by_path: Mapping[str, str | None],
    ) -> list[list[str]]:
        """Detect simple local import cycles."""
        module_to_path = self._module_to_path(content_by_path)
        graph: dict[str, set[str]] = {path: set() for path in parsed}
        for path, parsed_file in parsed.items():
            for node in parsed_file.imports:
                for module in self._node_modules(path, node):
                    target = module_to_path.get(module)
                    if target and target != path:
                        graph.setdefault(path, set()).add(target)

        cycles: list[list[str]] = []
        seen_cycles: set[tuple[str, ...]] = set()
        for path in sorted(graph):
            self._walk_cycles(path, path, graph, [], cycles, seen_cycles)
        return cycles

    def _content_by_path(
        self,
        project_path: str,
        files: Mapping[str, str | None] | None,
    ) -> dict[str, str | None]:
        index = self.indexer.ensure_index(project_path)
        content_by_path: dict[str, str | None] = {
            path: entry.get("content", "")
            for path, entry in index.items()
            if path.endswith(".py")
        }
        if files is not None:
            for path, content in files.items():
                normalized = path.replace(os.sep, "/")
                if content is None:
                    content_by_path.pop(normalized, None)
                elif normalized.endswith(".py"):
                    content_by_path[normalized] = content
        return content_by_path

    def _parse_imports(self, path: str, content: str | None) -> ParsedImports:
        try:
            tree = ast.parse(content or "", filename=path)
        except SyntaxError as error:
            return ParsedImports(
                path=path,
                parse_error=ValidationIssue(
                    file_path=path,
                    message=error.msg,
                    line=error.lineno,
                    column=error.offset,
                    check="import-parse",
                ),
            )

        imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        return ParsedImports(path=path, imports=imports)

    def _check_import(
        self,
        path: str,
        module: str,
        line: int,
        repo_modules: set[str],
    ) -> list[ValidationIssue]:
        if not module:
            return []
        if self._module_exists(module, repo_modules):
            return []

        top_level = module.split(".", 1)[0]
        repo_roots = {item.split(".", 1)[0] for item in repo_modules}
        if top_level in repo_roots:
            return [
                ValidationIssue(
                    file_path=path,
                    message=f"Could not resolve local module '{module}'.",
                    line=line,
                    check="local-import",
                )
            ]

        try:
            importable = importlib.util.find_spec(top_level) is not None
        except (ImportError, AttributeError, ValueError):
            importable = False
        if importable:
            return []

        severity = "error" if self.strict_external_imports else "warning"
        return [
            ValidationIssue(
                file_path=path,
                message=f"External module '{top_level}' is not importable in this environment.",
                severity=severity,
                line=line,
                check="external-import",
            )
        ]

    def _check_from_import(
        self,
        path: str,
        node: ast.ImportFrom,
        repo_modules: set[str],
    ) -> list[ValidationIssue]:
        module = node.module or ""
        if node.level:
            candidates = self._relative_import_candidates(path, module, node.level, node.names)
            if any(self._module_exists(candidate, repo_modules) for candidate in candidates):
                return []
            return [
                ValidationIssue(
                    file_path=path,
                    message=f"Could not resolve relative import level={node.level} module={module or '<package>'}.",
                    line=node.lineno,
                    check="relative-import",
                )
            ]
        return self._check_import(path, module, node.lineno, repo_modules)

    def _node_modules(self, path: str, node: ast.Import | ast.ImportFrom) -> list[str]:
        if isinstance(node, ast.Import):
            return [alias.name for alias in node.names]
        module = node.module or ""
        if node.level:
            return self._relative_import_candidates(path, module, node.level, node.names)
        modules = [module] if module else []
        modules.extend(f"{module}.{alias.name}" for alias in node.names if module and alias.name != "*")
        return modules

    def _repo_modules(self, content_by_path: Mapping[str, str | None]) -> set[str]:
        return {
            module
            for path, content in content_by_path.items()
            if content is not None
            for module in self._path_modules(path)
        }

    def _module_to_path(self, content_by_path: Mapping[str, str | None]) -> dict[str, str]:
        mapping = {}
        for path, content in content_by_path.items():
            if content is None or not path.endswith(".py"):
                continue
            for module in self._path_modules(path):
                mapping[module] = path
        return mapping

    def _path_modules(self, path: str) -> set[str]:
        normalized = path.replace(os.sep, "/")
        if not normalized.endswith(".py"):
            return set()
        without_ext = normalized[:-3].replace("/", ".")
        modules = {without_ext}
        parts = without_ext.split(".")
        for start in range(1, len(parts)):
            modules.add(".".join(parts[start:]))
        if without_ext.endswith(".__init__"):
            modules.add(without_ext.removesuffix(".__init__"))
        return {module for module in modules if module}

    def _module_exists(self, module: str, repo_modules: set[str]) -> bool:
        return bool(module) and (
            module in repo_modules
            or any(item.startswith(module + ".") for item in repo_modules)
        )

    def _relative_import_candidates(
        self,
        path: str,
        module: str,
        level: int,
        aliases: list[ast.alias],
    ) -> list[str]:
        normalized = path.replace(os.sep, "/")
        if not normalized.endswith(".py"):
            return []
        current_module = normalized[:-3].replace("/", ".")
        parts = current_module.split(".") if current_module else []
        if not parts:
            return []
        package_parts = parts if normalized.endswith("__init__.py") else parts[:-1]
        if level > 1:
            package_parts = package_parts[: -(level - 1)]

        base = ".".join(package_parts)
        candidates = []
        if module:
            candidates.append(".".join(part for part in (base, module) if part))
        else:
            candidates.append(base)
            for alias in aliases:
                candidates.append(".".join(part for part in (base, alias.name) if part))
        return [candidate for candidate in candidates if candidate]

    def _walk_cycles(
        self,
        start: str,
        current: str,
        graph: Mapping[str, set[str]],
        path: list[str],
        cycles: list[list[str]],
        seen_cycles: set[tuple[str, ...]],
    ) -> None:
        path = [*path, current]
        for dependency in sorted(graph.get(current, set())):
            if dependency == start and len(path) > 1:
                cycle = [*path, start]
                key = tuple(sorted(cycle[:-1]))
                if key not in seen_cycles:
                    seen_cycles.add(key)
                    cycles.append(cycle)
            elif dependency not in path:
                self._walk_cycles(start, dependency, graph, path, cycles, seen_cycles)

    def _summary(self, status: str, file_count: int, missing_count: int, cycle_count: int) -> str:
        if status == "pass":
            return f"Imports valid for {file_count} Python file(s)."
        parts = []
        if missing_count:
            parts.append(f"{missing_count} missing import issue(s)")
        if cycle_count:
            parts.append(f"{cycle_count} circular import warning(s)")
        return "Import validation found " + ", ".join(parts or ["warnings"]) + "."
