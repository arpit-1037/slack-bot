"""In-memory repository indexing built from scanner and symbol extraction."""

from __future__ import annotations

from typing import TypedDict

from src.repository.repository_scanner import RepositoryScanner, ScannedFile
from src.repository.symbol_extractor import ImportInfo, SymbolExtractor, SymbolMap, empty_symbol_map
from src.utils.helpers import get_logger

log = get_logger(__name__)


class FileIndexEntry(TypedDict):
    """Indexed metadata for one repository file."""

    path: str
    extension: str
    size: int
    content: str
    truncated: bool
    symbols: SymbolMap


class RepositoryIndexer:
    """Maintain an in-memory structured repository map."""

    def __init__(
        self,
        scanner: RepositoryScanner | None = None,
        symbol_extractor: SymbolExtractor | None = None,
    ) -> None:
        self.scanner = scanner or RepositoryScanner()
        self.symbol_extractor = symbol_extractor or SymbolExtractor()
        self.project_path: str | None = None
        self.files: dict[str, FileIndexEntry] = {}

    def refresh(self, project_path: str) -> dict[str, FileIndexEntry]:
        """Rebuild the in-memory index for a repository path."""
        scanned_files = self.scanner.scan(project_path)
        self.project_path = project_path
        self.files = {}

        for file_info in scanned_files:
            symbols = self._extract_symbols(file_info)
            path = file_info["path"]
            self.files[path] = {
                "path": path,
                "extension": file_info["extension"],
                "size": file_info["size"],
                "content": file_info.get("content", ""),
                "truncated": bool(file_info.get("truncated", False)),
                "symbols": symbols,
            }

        log.info("Indexed repository path=%s files=%d", project_path, len(self.files))
        return self.files

    def reindex(self, project_path: str) -> dict[str, FileIndexEntry]:
        """Alias for refresh, kept explicit for callers that speak in indexing terms."""
        return self.refresh(project_path)

    def ensure_index(self, project_path: str) -> dict[str, FileIndexEntry]:
        """Return the current index, refreshing when the path changes or is empty."""
        if self.project_path != project_path or not self.files:
            return self.refresh(project_path)
        return self.files

    def find_file(self, query: str) -> list[FileIndexEntry]:
        """Find files whose paths contain the query."""
        needle = query.lower()
        return [
            entry for path, entry in sorted(self.files.items())
            if needle in path.lower()
        ]

    def find_function(self, name: str) -> list[tuple[str, dict]]:
        """Find top-level functions or methods by exact case-insensitive name."""
        needle = name.lower()
        matches: list[tuple[str, dict]] = []
        for path, entry in sorted(self.files.items()):
            for function in entry["symbols"]["functions"]:
                if function["name"].lower() == needle:
                    matches.append((path, function))
            for class_info in entry["symbols"]["classes"]:
                for method in class_info.get("methods", []):
                    if method["name"].lower() == needle:
                        matches.append((path, method))
        return matches

    def find_class(self, name: str) -> list[tuple[str, dict]]:
        """Find classes by exact case-insensitive name."""
        needle = name.lower()
        matches: list[tuple[str, dict]] = []
        for path, entry in sorted(self.files.items()):
            for class_info in entry["symbols"]["classes"]:
                if class_info["name"].lower() == needle:
                    matches.append((path, class_info))
        return matches

    def get_file_summary(self, path: str) -> str:
        """Return a compact human-readable summary of one indexed file."""
        entry = self.files.get(path)
        if not entry:
            return f"{path}: not found"

        symbols = entry["symbols"]
        functions = ", ".join(function["name"] for function in symbols["functions"]) or "none"
        classes = ", ".join(class_info["name"] for class_info in symbols["classes"]) or "none"
        imports = ", ".join(
            import_info.get("module") or import_info.get("name", "")
            for import_info in symbols["imports"]
        ) or "none"

        return (
            f"{path} ({entry['extension']}, {entry['size']} bytes)\n"
            f"Classes: {classes}\n"
            f"Functions: {functions}\n"
            f"Imports: {imports}"
        )

    def _extract_symbols(self, file_info: ScannedFile) -> SymbolMap:
        """Extract symbols from supported files."""
        if file_info["extension"] != ".py":
            symbols = empty_symbol_map()
            symbols["imports"] = self._extract_text_imports(file_info)
            return symbols
        if file_info.get("truncated") or file_info.get("skipped_reason"):
            log.info("Skipping symbol extraction for truncated/unreadable file=%s", file_info["path"])
            return empty_symbol_map()
        return self.symbol_extractor.extract(file_info.get("content", ""), file_path=file_info["path"])

    def _extract_text_imports(self, file_info: ScannedFile) -> list[ImportInfo]:
        """Extract simple non-Python import/include references with line parsing."""
        extension = file_info["extension"]
        if extension not in {".js", ".ts", ".php"}:
            return []

        imports: list[ImportInfo] = []
        for line_number, line in enumerate(file_info.get("content", "").splitlines(), start=1):
            stripped = line.strip()
            module = self._text_import_module(stripped, extension)
            if not module:
                continue
            imports.append({
                "type": "text_import",
                "module": module,
                "name": module,
                "alias": None,
                "line_start": line_number,
                "line_end": line_number,
            })
        return imports

    def _text_import_module(self, line: str, extension: str) -> str:
        """Return a module/path from a simple JS/TS/PHP import line."""
        if extension in {".js", ".ts"}:
            if line.startswith("import ") and " from " in line:
                return self._quoted_value(line.rsplit(" from ", 1)[1])
            if line.startswith("import "):
                return self._quoted_value(line.removeprefix("import "))
            if "require(" in line:
                return self._quoted_value(line.split("require(", 1)[1])

        if extension == ".php":
            lowered = line.lower()
            if lowered.startswith(("require ", "require_once ", "include ", "include_once ")):
                return self._quoted_value(line)
            if line.startswith("use ") and line.endswith(";"):
                return line.removeprefix("use ").rstrip(";").strip()

        return ""

    def _quoted_value(self, value: str) -> str:
        """Extract the first quoted value from a line fragment."""
        for quote in ("'", '"'):
            if quote not in value:
                continue
            parts = value.split(quote)
            if len(parts) >= 3:
                return parts[1].strip()
        return ""
