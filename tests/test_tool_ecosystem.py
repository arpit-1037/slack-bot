"""Testable examples for the unified read-only tool ecosystem."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from src.tools.base_tool import BaseTool, ToolMetadata, ToolResult, ToolValidationError
from src.tools.git.git_status_tool import GitStatusTool
from src.tools.repository.file_search_tool import FileSearchTool
from src.tools.repository.symbol_search_tool import SymbolSearchTool
from src.tools.system.file_reader_tool import FileReaderTool
from src.tools.tool_executor import ToolExecutor
from src.tools.tool_registry import ToolRegistry, create_default_registry
from src.tools.validation.syntax_check_tool import SyntaxCheckTool


class EchoTool(BaseTool):
    """Small deterministic tool used to test framework behavior."""

    metadata = ToolMetadata(
        name="example.echo",
        description="Echo a message.",
        category="example",
        input_schema={"message": "Required message."},
        output_schema={"message": "Echoed message."},
    )

    def validate_input(self, tool_input: Mapping[str, Any]) -> list[ToolValidationError]:
        if not isinstance(tool_input.get("message"), str):
            return [ToolValidationError("message", "message must be a string.")]
        return []

    def _execute(self, tool_input: Mapping[str, Any]) -> ToolResult:
        return self._success({"message": tool_input["message"]})


class ToolFrameworkTest(unittest.TestCase):
    """Examples for registration, discovery, and execution."""

    def test_registry_lists_and_fetches_tools(self) -> None:
        registry = ToolRegistry([EchoTool()])

        self.assertIn("example.echo", registry.discover_tools())
        self.assertEqual(registry.get_tool("example.echo").metadata.category, "example")
        self.assertEqual(registry.list_tools(category="example")[0]["name"], "example.echo")

    def test_executor_standardizes_success_and_validation_failure(self) -> None:
        executor = ToolExecutor(ToolRegistry([EchoTool()]))

        success = executor.execute_tool("example.echo", {"message": "hello"})
        failure = executor.execute_tool("example.echo", {})

        self.assertTrue(success.success)
        self.assertEqual(success.data["message"], "hello")
        self.assertFalse(failure.success)
        self.assertEqual(failure.validation_errors[0].field, "message")

    def test_default_registry_contains_requested_tool_groups(self) -> None:
        registry = create_default_registry()

        self.assertIn("git.status", registry.discover_tools(category="git"))
        self.assertIn("repository.file_search", registry.discover_tools(category="repository"))
        self.assertIn("validation.syntax_check", registry.discover_tools(category="validation"))
        self.assertIn("system.file_reader", registry.discover_tools(category="system"))


class RepositoryToolExamplesTest(unittest.TestCase):
    """Examples for concrete repository, validation, and system tools."""

    def test_file_and_symbol_search_use_repository_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.py").write_text(
                "class Calculator:\n"
                "    def total(self, values):\n"
                "        return sum(values)\n",
                encoding="utf-8",
            )

            files = FileSearchTool().execute(
                {
                    "project_path": str(root),
                    "query": "sample",
                }
            )
            symbols = SymbolSearchTool().execute(
                {
                    "project_path": str(root),
                    "query": "total",
                    "kind": "method",
                }
            )

            self.assertTrue(files.success)
            self.assertEqual(files.data["matches"][0]["path"], "sample.py")
            self.assertTrue(symbols.success)
            self.assertEqual(symbols.data["matches"][0]["class_name"], "Calculator")

    def test_syntax_check_reports_supplied_content_failure(self) -> None:
        result = SyntaxCheckTool().execute(
            {
                "file_path": "broken.py",
                "content": "def broken(:\n    pass\n",
            }
        )

        self.assertTrue(result.success)
        self.assertFalse(result.data["passed"])
        self.assertEqual(result.data["result"]["errors"][0]["check"], "python-syntax")

    def test_file_reader_returns_bounded_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.md").write_text("one\ntwo\nthree\n", encoding="utf-8")

            result = FileReaderTool().execute(
                {
                    "project_path": str(root),
                    "path": "notes.md",
                    "start_line": 2,
                    "end_line": 3,
                }
            )

            self.assertTrue(result.success)
            self.assertEqual(result.data["content"], "two\nthree\n")
            self.assertEqual(result.data["line_count"], 3)


class GitToolExamplesTest(unittest.TestCase):
    """Examples for concrete git tools using existing GitTool infrastructure."""

    @unittest.skipIf(shutil.which("git") is None, "git is not installed")
    def test_git_status_reports_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            (root / "sample.py").write_text("value = 1\n", encoding="utf-8")

            result = GitStatusTool().execute({"repo_path": str(root)})

            self.assertTrue(result.success)
            self.assertTrue(result.data["is_git_repo"])
            self.assertIn("sample.py", result.data["untracked_files"])


if __name__ == "__main__":
    unittest.main()
