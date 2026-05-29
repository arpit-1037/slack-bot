"""Build minimal repository-aware context for debugging tasks."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from src.debugging.stacktrace_parser import ParsedStackTrace
from src.repository.context_selector import ContextSelector
from src.repository.dependency_mapper import DependencyMapper
from src.repository.repository_indexer import FileIndexEntry, RepositoryIndexer
from src.utils.helpers import get_logger, int_env

log = get_logger(__name__)


@dataclass(frozen=True)
class CodeSnippet:
    """A bounded source snippet with repository line numbers."""

    line_start: int
    line_end: int
    content: str


@dataclass(frozen=True)
class DebugFileContext:
    """Debugging context for one selected file."""

    path: str
    reasons: list[str]
    imports: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    snippets: list[CodeSnippet] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BugContext:
    """Repository context selected for one bug report."""

    files: list[DebugFileContext]
    dependency_edges: list[str]
    stacktrace: ParsedStackTrace

    def format_context(self) -> str:
        """Format selected debugging context for the debug prompt."""
        if not self.files:
            return "No repository files matched the bug report."

        parts = ["FOCUSED DEBUGGING CONTEXT"]
        if self.dependency_edges:
            parts.append("Dependency edges:\n" + "\n".join(f"- {edge}" for edge in self.dependency_edges))

        for file_context in self.files:
            parts.append(self._format_file(file_context))
        return "\n\n".join(parts)

    def _format_file(self, file_context: DebugFileContext) -> str:
        """Format one selected file context block."""
        lines = [
            f"=== {file_context.path} ===",
            f"reasons: {', '.join(file_context.reasons)}",
            f"imports: {', '.join(file_context.imports) or 'none'}",
            f"classes: {', '.join(file_context.classes) or 'none'}",
            f"functions: {', '.join(file_context.functions) or 'none'}",
        ]
        if file_context.dependencies:
            lines.append(f"dependencies: {', '.join(file_context.dependencies)}")
        if file_context.dependents:
            lines.append(f"dependents: {', '.join(file_context.dependents)}")
        for snippet in file_context.snippets:
            lines.append(f"--- lines {snippet.line_start}-{snippet.line_end} ---")
            lines.append(snippet.content)
        return "\n".join(lines)


class BugContextBuilder:
    """Collect minimal useful repository context for a bug report."""

    _file_ref_pattern = re.compile(
        r"(?P<path>[\w./-]+\.(?:py|js|ts|php|json|md|yaml|yml))(?:[:#L]+(?P<line>\d+))?"
    )

    def __init__(
        self,
        indexer: RepositoryIndexer | None = None,
        dependency_mapper: DependencyMapper | None = None,
        context_selector: ContextSelector | None = None,
        max_files: int | None = None,
        snippet_radius: int | None = None,
        max_file_chars: int | None = None,
    ) -> None:
        self.indexer = indexer or RepositoryIndexer()
        self.dependency_mapper = dependency_mapper or DependencyMapper()
        self.context_selector = context_selector or ContextSelector(indexer=self.indexer)
        self.max_files = max_files or int_env("DEBUG_CONTEXT_MAX_FILES", 5, 1)
        self.snippet_radius = snippet_radius or int_env("DEBUG_SNIPPET_RADIUS", 14, 3)
        self.max_file_chars = max_file_chars or int_env("DEBUG_CONTEXT_MAX_FILE_CHARS", 5_000, 500)

    def build(
        self,
        project_path: str,
        bug_description: str,
        stacktrace: ParsedStackTrace,
        request_id: str | None = None,
    ) -> BugContext:
        """Build a focused bug context from stacktrace, explicit refs, and index signals."""
        index = self.indexer.ensure_index(project_path)
        self.dependency_mapper.refresh(index)
        selection = self.context_selector.select_context(project_path, bug_description, request_id=request_id)
        terms = self.context_selector.query_terms(bug_description)

        candidates = self._candidate_paths(index, bug_description, stacktrace, selection.selected_files)
        expanded = self._expand_dependencies(candidates, index)
        files = [
            self._build_file_context(path, reasons, index[path], stacktrace, terms)
            for path, reasons in expanded[: self.max_files]
            if path in index
        ]
        edges = self._dependency_edges([file_context.path for file_context in files])

        log.info(
            "request_id=%s built bug context files=%d edges=%d stack_frames=%d",
            request_id,
            len(files),
            len(edges),
            len(stacktrace.frames),
        )
        return BugContext(files=files, dependency_edges=edges, stacktrace=stacktrace)

    def _candidate_paths(
        self,
        index: dict[str, FileIndexEntry],
        bug_description: str,
        stacktrace: ParsedStackTrace,
        selected_files,
    ) -> list[tuple[str, list[str]]]:
        """Find candidate repository paths from stacktrace, file refs, and selector output."""
        candidates: dict[str, list[str]] = {}

        for frame in stacktrace.frames:
            for path in self._match_repo_paths(index, frame.filename):
                candidates.setdefault(path, []).append(f"stacktrace:{frame.line_number}")

        for ref_path, ref_line in self._extract_file_references(bug_description):
            reason = f"mentioned:{ref_line}" if ref_line else "mentioned"
            for path in self._match_repo_paths(index, ref_path):
                candidates.setdefault(path, []).append(reason)

        for selected in selected_files:
            candidates.setdefault(selected.path, []).append("selector:" + ",".join(selected.reasons[:3]))

        ranked = [
            (path, sorted(set(reasons)))
            for path, reasons in candidates.items()
        ]
        ranked.sort(key=lambda item: (self._rank_reason(item[1]), item[0]))
        return ranked

    def _expand_dependencies(
        self,
        candidates: list[tuple[str, list[str]]],
        index: dict[str, FileIndexEntry],
    ) -> list[tuple[str, list[str]]]:
        """Add immediate dependencies/dependents while respecting max_files."""
        expanded: dict[str, list[str]] = {}
        for path, reasons in candidates:
            expanded.setdefault(path, []).extend(reasons)
            for dependency in self.dependency_mapper.get_dependencies(path)[:2]:
                expanded.setdefault(dependency, []).append(f"dependency-of:{path}")
            for dependent in self.dependency_mapper.get_dependents(path)[:2]:
                expanded.setdefault(dependent, []).append(f"dependent-of:{path}")

        ranked = [
            (path, sorted(set(reasons)))
            for path, reasons in expanded.items()
            if path in index
        ]
        ranked.sort(key=lambda item: (self._rank_reason(item[1]), item[0]))
        return ranked

    def _build_file_context(
        self,
        path: str,
        reasons: list[str],
        entry: FileIndexEntry,
        stacktrace: ParsedStackTrace,
        terms: set[str],
    ) -> DebugFileContext:
        """Build bounded debugging context for one file."""
        stack_lines = [
            frame.line_number
            for frame in stacktrace.frames
            if self._path_matches(path, frame.filename)
        ]
        stack_functions = {
            frame.function_name
            for frame in stacktrace.frames
            if self._path_matches(path, frame.filename)
        }
        snippets = self._snippets_for_file(entry, stack_lines, stack_functions)
        return DebugFileContext(
            path=path,
            reasons=reasons,
            imports=self._imports(entry),
            functions=self._functions(entry, terms, stack_functions),
            classes=self._classes(entry, terms),
            snippets=snippets,
            dependencies=self.dependency_mapper.get_dependencies(path)[:4],
            dependents=self.dependency_mapper.get_dependents(path)[:4],
        )

    def _snippets_for_file(
        self,
        entry: FileIndexEntry,
        stack_lines: list[int],
        stack_functions: set[str],
    ) -> list[CodeSnippet]:
        """Select snippets around stack lines or matching symbols."""
        content = entry["content"]
        if not content:
            return []

        lines = content.splitlines()
        requested_lines = list(stack_lines)
        for function in entry["symbols"]["functions"]:
            if function["name"] in stack_functions:
                requested_lines.append(function["line_start"])
        for class_info in entry["symbols"]["classes"]:
            for method in class_info.get("methods", []):
                if method["name"] in stack_functions:
                    requested_lines.append(method["line_start"])

        if not requested_lines:
            excerpt = content[: self.max_file_chars].rstrip()
            if len(content) > self.max_file_chars:
                excerpt += "\n... [file truncated for debug context]"
            return [CodeSnippet(line_start=1, line_end=min(len(lines), excerpt.count("\n") + 1), content=excerpt)]

        snippets = []
        seen_ranges = set()
        for line_number in requested_lines:
            start = max(1, line_number - self.snippet_radius)
            end = min(len(lines), line_number + self.snippet_radius)
            key = (start, end)
            if key in seen_ranges:
                continue
            seen_ranges.add(key)
            numbered = "\n".join(
                f"{number}: {lines[number - 1]}"
                for number in range(start, end + 1)
            )
            snippets.append(CodeSnippet(line_start=start, line_end=end, content=numbered))
        return snippets[:3]

    def _dependency_edges(self, paths: list[str]) -> list[str]:
        """Return dependency edges among selected files."""
        selected = set(paths)
        edges = []
        for path in paths:
            for dependency in self.dependency_mapper.get_dependencies(path):
                if dependency in selected:
                    edges.append(f"{path} -> {dependency}")
        return edges

    def _extract_file_references(self, text: str) -> list[tuple[str, int | None]]:
        """Extract explicit file references from user text."""
        references = []
        for match in self._file_ref_pattern.finditer(text):
            line = match.group("line")
            references.append((match.group("path"), int(line) if line else None))
        return references

    def _match_repo_paths(self, index: dict[str, FileIndexEntry], referenced_path: str) -> list[str]:
        """Map a stacktrace or user path to indexed repository paths."""
        normalized = referenced_path.replace("\\", "/")
        basename = os.path.basename(normalized)
        matches = []
        for path in index:
            candidate = path.replace("\\", "/")
            if candidate == normalized or candidate.endswith("/" + normalized):
                matches.append(path)
            elif basename and os.path.basename(candidate) == basename:
                matches.append(path)
        return sorted(set(matches))

    def _path_matches(self, repo_path: str, referenced_path: str) -> bool:
        """Return True when a repo path matches a stacktrace path."""
        normalized = referenced_path.replace("\\", "/")
        return repo_path.replace("\\", "/").endswith(normalized) or os.path.basename(repo_path) == os.path.basename(normalized)

    def _imports(self, entry: FileIndexEntry) -> list[str]:
        """Return nearby imports for a file."""
        imports = []
        for import_info in entry["symbols"]["imports"][:10]:
            module = import_info.get("module") or import_info.get("name", "")
            name = import_info.get("name", "")
            imports.append(f"{module}.{name}" if module and name and module != name else module or name)
        return imports

    def _functions(
        self,
        entry: FileIndexEntry,
        terms: set[str],
        stack_functions: set[str],
    ) -> list[str]:
        """Return relevant function/method names."""
        names = []
        for function in entry["symbols"]["functions"]:
            if function["name"] in stack_functions or self._matches_terms(function["name"], terms):
                names.append(function["name"])
        for class_info in entry["symbols"]["classes"]:
            for method in class_info.get("methods", []):
                if method["name"] in stack_functions or self._matches_terms(method["name"], terms):
                    names.append(f"{class_info['name']}.{method['name']}")
        return sorted(set(names))

    def _classes(self, entry: FileIndexEntry, terms: set[str]) -> list[str]:
        """Return relevant class names."""
        names = [
            class_info["name"]
            for class_info in entry["symbols"]["classes"]
            if self._matches_terms(class_info["name"], terms)
        ]
        if not names:
            names = [class_info["name"] for class_info in entry["symbols"]["classes"][:5]]
        return sorted(set(names))

    def _matches_terms(self, value: str, terms: set[str]) -> bool:
        """Return True when a value matches any query term."""
        value = value.lower()
        return any(term in value for term in terms)

    def _rank_reason(self, reasons: list[str]) -> int:
        """Rank reason groups with stacktrace and explicit mentions first."""
        joined = " ".join(reasons)
        if "stacktrace:" in joined:
            return 0
        if "mentioned" in joined:
            return 1
        if "selector:" in joined:
            return 2
        return 3
