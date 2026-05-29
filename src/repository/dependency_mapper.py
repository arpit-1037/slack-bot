"""Deterministic repository dependency mapping."""

from __future__ import annotations

import os

from src.repository.repository_indexer import FileIndexEntry
from src.utils.helpers import get_logger

log = get_logger(__name__)


class DependencyMapper:
    """Map file dependencies and reverse dependents from indexed imports."""

    def __init__(self) -> None:
        self.dependencies: dict[str, set[str]] = {}
        self.dependents: dict[str, set[str]] = {}

    def refresh(self, index: dict[str, FileIndexEntry]) -> None:
        """Rebuild dependency maps from an index."""
        module_to_path = self._module_to_path(index)
        path_lookup = self._path_lookup(index)
        self.dependencies = {path: set() for path in index}
        self.dependents = {path: set() for path in index}

        for path, entry in index.items():
            for import_info in entry["symbols"]["imports"]:
                for module_name in self._candidate_modules(import_info):
                    target_path = (
                        self._resolve_path_import(path, module_name, path_lookup)
                        or module_to_path.get(module_name)
                    )
                    if target_path and target_path != path:
                        self.dependencies[path].add(target_path)
                        self.dependents[target_path].add(path)

        log.info("Mapped repository dependencies files=%d", len(index))

    def get_dependencies(self, file_path: str) -> list[str]:
        """Return files imported by file_path."""
        return sorted(self.dependencies.get(file_path, set()))

    def get_dependents(self, file_path: str) -> list[str]:
        """Return files that import file_path."""
        return sorted(self.dependents.get(file_path, set()))

    def _module_to_path(self, index: dict[str, FileIndexEntry]) -> dict[str, str]:
        """Map module-ish names to repository file paths."""
        mapping: dict[str, str] = {}
        for path in index:
            without_ext = os.path.splitext(path)[0].replace(os.sep, ".").replace("/", ".")
            mapping[without_ext] = path
            parts = without_ext.split(".")
            for start in range(1, len(parts)):
                mapping[".".join(parts[start:])] = path
            if parts[-1] == "__init__":
                package_name = ".".join(parts[:-1])
                if package_name:
                    mapping[package_name] = path
        return mapping

    def _path_lookup(self, index: dict[str, FileIndexEntry]) -> dict[str, str]:
        """Build normalized path lookup variants for relative imports."""
        lookup: dict[str, str] = {}
        for path in index:
            normalized = path.replace(os.sep, "/")
            without_ext = os.path.splitext(normalized)[0]
            lookup[normalized] = path
            lookup[without_ext] = path
            lookup[f"{without_ext}/index"] = path
            lookup[f"{without_ext}/__init__"] = path
        return lookup

    def _candidate_modules(self, import_info: dict) -> list[str]:
        """Return candidate module names for an import entry."""
        module = (import_info.get("module") or "").lstrip(".")
        name = import_info.get("name") or ""
        candidates = []

        if module:
            candidates.append(module)
            if name and name != "*":
                candidates.append(f"{module}.{name}")
        elif name:
            candidates.append(name)

        return candidates

    def _resolve_path_import(
        self,
        source_path: str,
        module_name: str,
        path_lookup: dict[str, str],
    ) -> str | None:
        """Resolve relative import/include paths to repository paths."""
        if not module_name:
            return None

        if not (
            module_name.startswith(".")
            or module_name.startswith("/")
            or "/" in module_name
            or "\\" in module_name
        ):
            return None

        base_dir = os.path.dirname(source_path)
        module_path = module_name.replace("\\", "/").lstrip("/")
        candidate = os.path.normpath(os.path.join(base_dir, module_path)).replace(os.sep, "/")
        candidate = candidate.removeprefix("./")

        candidate_keys = [candidate, os.path.splitext(candidate)[0]]
        for extension in (".py", ".js", ".ts", ".php", ".json"):
            candidate_keys.append(f"{candidate}{extension}")
            candidate_keys.append(f"{candidate}/index{extension}")
            candidate_keys.append(f"{candidate}/__init__{extension}")

        for key in candidate_keys:
            if key in path_lookup:
                return path_lookup[key]
        return None
