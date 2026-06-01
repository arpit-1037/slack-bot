"""Validation checks for proposed repository modifications."""

from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Mapping

from src.repository.dependency_mapper import DependencyMapper
from src.repository.repository_indexer import RepositoryIndexer
from src.utils.helpers import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ValidationIssue:
    """One validation finding."""

    path: str
    check: str
    message: str
    severity: str = "error"
    line: int | None = None

    def format(self) -> str:
        """Format a concise issue line."""
        location = f":{self.line}" if self.line else ""
        return f"[{self.severity}] {self.path}{location} {self.check}: {self.message}"


@dataclass(frozen=True)
class ValidationResult:
    """Result of syntax, import, and optional test validation."""

    issues: list[ValidationIssue] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True when no error-severity issues were found."""
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def warnings(self) -> list[ValidationIssue]:
        """Return warning-severity issues."""
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def errors(self) -> list[ValidationIssue]:
        """Return error-severity issues."""
        return [issue for issue in self.issues if issue.severity == "error"]

    def format_report(self) -> str:
        """Return a readable validation report."""
        if not self.issues:
            checks = ", ".join(self.checks_run) or "none"
            return f"Validation passed. Checks run: {checks}."
        return "\n".join(issue.format() for issue in self.issues)


class ChangeValidator:
    """Validate proposed changes before and after safe filesystem apply."""

    def __init__(
        self,
        indexer: RepositoryIndexer | None = None,
        dependency_mapper: DependencyMapper | None = None,
        strict_external_imports: bool = False,
        pytest_timeout_seconds: int = 90,
    ) -> None:
        self.indexer = indexer or RepositoryIndexer()
        self.dependency_mapper = dependency_mapper or DependencyMapper()
        self.strict_external_imports = strict_external_imports
        self.pytest_timeout_seconds = pytest_timeout_seconds

    def validate(
        self,
        project_path: str,
        proposed_files: Mapping[str, str | None],
        deleted_paths: set[str] | None = None,
        run_pytest: bool = False,
        request_id: str | None = None,
    ) -> ValidationResult:
        """Validate proposed file contents and optional repository-level checks."""
        issues: list[ValidationIssue] = []
        checks_run: list[str] = []
        repo_modules = self._repo_modules(project_path, proposed_files)

        for path, content in sorted(proposed_files.items()):
            if content is None or not path.endswith(".py"):
                continue
            checks_run.append(f"ast:{path}")
            tree = self._parse_python(path, content, issues)
            if tree is None:
                continue
            checks_run.append(f"imports:{path}")
            issues.extend(self._check_imports(path, tree, repo_modules))

        deleted = deleted_paths or {path for path, content in proposed_files.items() if content is None}
        if deleted:
            checks_run.append("deleted-file-dependents")
            issues.extend(self._check_deleted_dependents(project_path, deleted, set(proposed_files)))

        if run_pytest and not any(issue.severity == "error" for issue in issues):
            checks_run.append("pytest")
            issues.extend(self._run_pytest(project_path))

        result = ValidationResult(issues=issues, checks_run=checks_run)
        log.info(
            "request_id=%s validation ok=%s issues=%d checks=%s",
            request_id,
            result.ok,
            len(issues),
            ",".join(checks_run),
        )
        return result

    def _parse_python(
        self,
        path: str,
        content: str,
        issues: list[ValidationIssue],
    ) -> ast.AST | None:
        try:
            tree = ast.parse(content, filename=path)
            compile(tree, path, "exec")
            return tree
        except SyntaxError as error:
            issues.append(
                ValidationIssue(
                    path=path,
                    check="python-syntax",
                    message=error.msg,
                    line=error.lineno,
                )
            )
        except Exception as error:
            issues.append(
                ValidationIssue(path=path, check="python-compile", message=str(error))
            )
        return None

    def _check_imports(
        self,
        path: str,
        tree: ast.AST,
        repo_modules: set[str],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    issues.extend(self._check_absolute_import(path, alias.name, repo_modules, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                issues.extend(self._check_from_import(path, node, repo_modules))
        return issues

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
                    path=path,
                    check="relative-import",
                    message=f"Could not resolve relative import level={node.level} module={module or '<package>'}.",
                    line=node.lineno,
                )
            ]
        return self._check_absolute_import(path, module, repo_modules, node.lineno)

    def _check_absolute_import(
        self,
        path: str,
        module: str,
        repo_modules: set[str],
        line: int,
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
                    path=path,
                    check="local-import",
                    message=f"Could not resolve local module '{module}'.",
                    line=line,
                )
            ]

        try:
            importable = top_level in sys.builtin_module_names or importlib.util.find_spec(top_level) is not None
        except (ImportError, AttributeError, ValueError):
            importable = False

        if importable:
            return []

        severity = "error" if self.strict_external_imports else "warning"
        return [
            ValidationIssue(
                path=path,
                check="external-import",
                message=f"External module '{top_level}' is not importable in this environment.",
                severity=severity,
                line=line,
            )
        ]

    def _check_deleted_dependents(
        self,
        project_path: str,
        deleted_paths: set[str],
        changed_paths: set[str],
    ) -> list[ValidationIssue]:
        try:
            index = self.indexer.ensure_index(project_path)
            self.dependency_mapper.refresh(index)
        except Exception as error:
            return [
                ValidationIssue(
                    path="repository",
                    check="dependency-map",
                    message=f"Could not validate deleted-file dependents: {error}",
                    severity="warning",
                )
            ]

        issues = []
        for path in sorted(deleted_paths):
            dependents = [
                dependent for dependent in self.dependency_mapper.get_dependents(path)
                if dependent not in changed_paths
            ]
            if dependents:
                issues.append(
                    ValidationIssue(
                        path=path,
                        check="deleted-file-dependents",
                        message="Deleted file is still imported by: " + ", ".join(dependents[:5]),
                    )
                )
        return issues

    def _run_pytest(self, project_path: str) -> list[ValidationIssue]:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q"],
                cwd=project_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.pytest_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return [
                ValidationIssue(
                    path="repository",
                    check="pytest",
                    message=f"pytest timed out after {self.pytest_timeout_seconds}s.",
                )
            ]
        except Exception as error:
            return [
                ValidationIssue(
                    path="repository",
                    check="pytest",
                    message=str(error),
                    severity="warning",
                )
            ]

        if result.returncode == 0:
            return []
        output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        return [
            ValidationIssue(
                path="repository",
                check="pytest",
                message=output[-1000:] or "pytest failed.",
            )
        ]

    def _repo_modules(
        self,
        project_path: str,
        proposed_files: Mapping[str, str | None],
    ) -> set[str]:
        modules = set()
        try:
            index = self.indexer.ensure_index(project_path)
            modules.update(self._path_to_module(path) for path in index if path.endswith(".py"))
        except Exception:
            log.exception("Could not read repository index for import validation.")

        modules.update(
            self._path_to_module(path)
            for path, content in proposed_files.items()
            if content is not None and path.endswith(".py")
        )
        return {module for module in modules if module}

    def _path_to_module(self, path: str) -> str:
        normalized = path.replace(os.sep, "/")
        if not normalized.endswith(".py"):
            return ""
        module = normalized[:-3].replace("/", ".")
        if module.endswith(".__init__"):
            return module.removesuffix(".__init__")
        return module

    def _module_exists(self, module: str, repo_modules: set[str]) -> bool:
        if not module:
            return True
        return module in repo_modules or any(item.startswith(module + ".") for item in repo_modules)

    def _relative_import_candidates(
        self,
        path: str,
        module: str,
        level: int,
        aliases: list[ast.alias],
    ) -> list[str]:
        current_module = self._path_to_module(path)
        parts = current_module.split(".")
        if not parts:
            return []
        normalized_path = path.replace(os.sep, "/")
        package_parts = parts if normalized_path.endswith("__init__.py") else parts[:-1]
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
        return candidates
