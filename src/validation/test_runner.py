"""Safe test discovery and execution."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from src.utils.helpers import get_logger, int_env
from src.validation.validation_models import (
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIPPED,
    TestExecutionResult,
    ValidationIssue,
    confidence_from_issues,
)

log = get_logger(__name__)


class TestRunner:
    """Run pytest, unittest, or configured test commands with timeouts."""

    def __init__(self, timeout_seconds: int | None = None) -> None:
        self.timeout_seconds = timeout_seconds or int_env(
            "VALIDATION_TEST_TIMEOUT_SECONDS",
            90,
            minimum=1,
        )

    def discover_tests(self, project_path: str) -> list[list[str]]:
        """Return candidate test commands for a repository."""
        configured = os.getenv("VALIDATION_TEST_COMMAND", "").strip()
        if configured:
            return [shlex.split(configured)]

        root = Path(project_path)
        has_tests_dir = (root / "tests").is_dir()
        has_test_files = any(root.glob("test_*.py")) or any(root.glob("tests/test_*.py"))
        if not has_tests_dir and not has_test_files:
            return []

        if shutil.which("pytest"):
            return [[sys.executable, "-m", "pytest", "-q"]]
        return [[sys.executable, "-m", "unittest", "discover", "-s", "tests"]]

    def run_tests(
        self,
        project_path: str,
        command: list[str] | str | None = None,
        timeout_seconds: int | None = None,
    ) -> TestExecutionResult:
        """Run tests safely and return captured execution details."""
        start = time.monotonic()
        commands = [self._command_list(command)] if command else self.discover_tests(project_path)
        if not commands:
            return TestExecutionResult(
                name="tests",
                status=STATUS_SKIPPED,
                execution_time_seconds=round(time.monotonic() - start, 4),
                summary="No test command or discoverable tests found.",
                confidence_score=0.5,
            )

        active_command = commands[0]
        timeout = timeout_seconds or self.timeout_seconds
        try:
            result = subprocess.run(
                active_command,
                cwd=project_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            issue = ValidationIssue(
                file_path="repository",
                message=f"Test command timed out after {timeout}s.",
                check="tests",
            )
            return TestExecutionResult(
                name="tests",
                status=STATUS_FAIL,
                execution_time_seconds=round(time.monotonic() - start, 4),
                errors=[issue],
                summary=issue.message,
                confidence_score=0.0,
                command=active_command,
                stdout=error.stdout or "",
                stderr=error.stderr or "",
            )
        except Exception as error:
            issue = ValidationIssue(
                file_path="repository",
                message=str(error),
                check="tests",
            )
            return TestExecutionResult(
                name="tests",
                status=STATUS_FAIL,
                execution_time_seconds=round(time.monotonic() - start, 4),
                errors=[issue],
                summary=f"Test execution failed: {error}",
                confidence_score=0.0,
                command=active_command,
            )

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        output = "\n".join(part for part in (stdout, stderr) if part)
        passed, failed, skipped = self._parse_counts(output, result.returncode)
        errors = []
        if result.returncode != 0:
            errors.append(
                ValidationIssue(
                    file_path="repository",
                    message=self._short_output(output) or "Tests failed.",
                    check="tests",
                )
            )

        status = STATUS_PASS if result.returncode == 0 else STATUS_FAIL
        summary = self._summary(status, passed, failed, skipped)
        return TestExecutionResult(
            name="tests",
            status=status,
            execution_time_seconds=round(time.monotonic() - start, 4),
            errors=errors,
            summary=summary,
            confidence_score=confidence_from_issues(errors, [], base=0.9 if status == STATUS_PASS else 0.4),
            command=active_command,
            passed_tests=passed,
            failed_tests=failed,
            skipped_tests=skipped,
            stdout=stdout[-4000:],
            stderr=stderr[-4000:],
        )

    def _command_list(self, command: list[str] | str) -> list[str]:
        if isinstance(command, str):
            return shlex.split(command)
        return list(command)

    def _parse_counts(self, output: str, returncode: int) -> tuple[int, int, int]:
        failed = 0
        skipped = 0
        passed = 0

        count_matches = re.findall(r"(\d+)\s+(passed|failed|skipped|error|errors)", output)
        if count_matches:
            for count_text, label in count_matches:
                count = int(count_text)
                if label == "passed":
                    passed += count
                elif label in {"failed", "error", "errors"}:
                    failed += count
                elif label == "skipped":
                    skipped += count
            return passed, failed, skipped

        unittest_match = re.search(r"Ran (\d+) tests?", output)
        failures_match = re.search(r"failures=(\d+)", output)
        errors_match = re.search(r"errors=(\d+)", output)
        skipped_match = re.search(r"skipped=(\d+)", output)
        if unittest_match:
            total = int(unittest_match.group(1))
            failed = int(failures_match.group(1)) if failures_match else 0
            failed += int(errors_match.group(1)) if errors_match else 0
            skipped = int(skipped_match.group(1)) if skipped_match else 0
            passed = max(total - failed - skipped, 0)
            return passed, failed, skipped

        return (1, 0, 0) if returncode == 0 else (0, 1, 0)

    def _summary(self, status: str, passed: int, failed: int, skipped: int) -> str:
        if status == STATUS_PASS:
            return f"Tests passed: passed={passed} skipped={skipped}."
        return f"Tests failed: passed={passed} failed={failed} skipped={skipped}."

    def _short_output(self, output: str) -> str:
        return output.strip()[-1000:]
