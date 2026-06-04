"""Central registry for assistant tools."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from src.tools.base_tool import BaseTool


class ToolRegistrationError(ValueError):
    """Raised when a tool cannot be registered."""


class ToolRegistry:
    """Register, discover, list, and fetch assistant tools by name."""

    def __init__(self, tools: Iterable[BaseTool] | None = None) -> None:
        self._tools: dict[str, BaseTool] = {}
        for tool in tools or []:
            self.register_tool(tool)

    def register_tool(self, tool: BaseTool, replace: bool = False) -> BaseTool:
        """Register a tool instance for dynamic lookup."""
        if not isinstance(tool, BaseTool):
            raise ToolRegistrationError("Registered tool must inherit BaseTool.")

        name = tool.metadata.name.strip()
        if not name:
            raise ToolRegistrationError("Registered tool must have a non-empty name.")
        if name in self._tools and not replace:
            raise ToolRegistrationError(f"Tool already registered: {name}")

        self._tools[name] = tool
        return tool

    def get_tool(self, name: str) -> BaseTool | None:
        """Return a registered tool by name, or None when missing."""
        return self._tools.get(name)

    def list_tools(
        self,
        category: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict]:
        """Return metadata for tools matching optional filters."""
        required_tags = set(tags or [])
        results = []
        for tool in self._tools.values():
            metadata = tool.get_metadata()
            if category and metadata.get("category") != category:
                continue
            if required_tags and not required_tags.issubset(set(metadata.get("tags", []))):
                continue
            results.append(metadata)
        return sorted(results, key=lambda item: item["name"])

    def discover_tools(
        self,
        category: str | None = None,
        tags: list[str] | None = None,
    ) -> list[str]:
        """Return registered tool names matching optional filters."""
        return [metadata["name"] for metadata in self.list_tools(category=category, tags=tags)]

    def __contains__(self, name: object) -> bool:
        return bool(isinstance(name, str) and name in self._tools)

    def __iter__(self) -> Iterator[BaseTool]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)


def create_default_registry() -> ToolRegistry:
    """Create a registry populated with the built-in read-only tool ecosystem."""
    from src.tools.git.git_branch_tool import GitBranchTool
    from src.tools.git.git_diff_tool import GitDiffTool
    from src.tools.git.git_log_tool import GitLogTool
    from src.tools.git.git_status_tool import GitStatusTool
    from src.tools.repository.dependency_search_tool import DependencySearchTool
    from src.tools.repository.file_search_tool import FileSearchTool
    from src.tools.repository.repository_stats_tool import RepositoryStatsTool
    from src.tools.repository.symbol_search_tool import SymbolSearchTool
    from src.tools.system.directory_tree_tool import DirectoryTreeTool
    from src.tools.system.file_metadata_tool import FileMetadataTool
    from src.tools.system.file_reader_tool import FileReaderTool
    from src.tools.validation.lint_tool import LintTool
    from src.tools.validation.pytest_tool import PytestTool
    from src.tools.validation.syntax_check_tool import SyntaxCheckTool

    return ToolRegistry(
        [
            GitStatusTool(),
            GitLogTool(),
            GitDiffTool(),
            GitBranchTool(),
            FileSearchTool(),
            SymbolSearchTool(),
            DependencySearchTool(),
            RepositoryStatsTool(),
            PytestTool(),
            LintTool(),
            SyntaxCheckTool(),
            FileReaderTool(),
            DirectoryTreeTool(),
            FileMetadataTool(),
        ]
    )
