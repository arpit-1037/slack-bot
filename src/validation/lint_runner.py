"""Safe lint detection and execution."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from src.utils.helpers import get_logger, int_env
from src.validation.validation_models import (
    LintResult,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIPPED,
    ValidationIssue,
    confidence_from_issues,
)

log = get_logger(__name__)


class LintRunner:
    """Run available linters with safe subprocess arguments and timeouts."""

    def __init__(self, timeout_seconds: int | None = None) -> None:
        self.timeout_seconds = timeout_seconds or int_env(
            "VALIDATION_LINT_TIMEOUT_SECONDS",
            60,
            minimum=1,
        )

    def detect_linters(self) -> list[str]:
        """Return supported linters available on PATH in preference order."""
        return [
            name
            for name in ("ruff", "flake8", "pylint")
            if shutil.which(name)
        ]

    def run_linting(
        self,
        project_path: str,
        file_paths: list[str] | None = None,
        linter: str | None = None,
        timeout_seconds: int | None = None,
    ) -> LintResult:
        """Run a detected or requested linter and return captured output."""
        start = time.monotonic()
        selected = linter or next(iter(self.detect_linters()), "")
        if not selected:
            return LintResult(
                name="lint",
                status=STATUS_SKIPPED,
                execution_time_seconds=round(time.monotonic() - start, 4),
                summary="No supported linter found.",
                confidence_score=0.5,
            )

        targets = self._targets(project_path, file_paths)
        command = self._command(selected, targets)
        if not command:
            return LintResult(
                name="lint",
                status=STATUS_SKIPPED,
                execution_time_seconds=round(time.monotonic() - start, 4),
                summary=f"Unsupported linter: {selected}.",
                confidence_score=0.5,
                linter=selected,
            )

        timeout = timeout_seconds or self.timeout_seconds
        try:
            result = subprocess.run(
                command,
                cwd=project_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            issue = ValidationIssue(
                file_path="repository",
                message=f"Lint command timed out after {timeout}s.",
                check="lint",
            )
            return LintResult(
                name="lint",
                status=STATUS_FAIL,
                execution_time_seconds=round(time.monotonic() - start, 4),
                errors=[issue],
                summary=issue.message,
                confidence_score=0.0,
                command=command,
                linter=selected,
                stdout=error.stdout or "",
                stderr=error.stderr or "",
            )
        except Exception as error:
            issue = ValidationIssue(
                file_path="repository",
                message=str(error),
                check="lint",
            )
            return LintResult(
                name="lint",
                status=STATUS_FAIL,
                execution_time_seconds=round(time.monotonic() - start, 4),
                errors=[issue],
                summary=f"Lint execution failed: {error}",
                confidence_score=0.0,
                command=command,
                linter=selected,
            )

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        output = "\n".join(part.strip() for part in (stdout, stderr) if part.strip())
        errors = []
        if result.returncode != 0:
            errors.append(
                ValidationIssue(
                    file_path="repository",
                    message=output[-1000:] or f"{selected} reported lint findings.",
                    check="lint",
                )
            )

        status = STATUS_PASS if result.returncode == 0 else STATUS_FAIL
        summary = "Lint passed." if status == STATUS_PASS else f"Lint failed with {selected}."
        return LintResult(
            name="lint",
            status=status,
            execution_time_seconds=round(time.monotonic() - start, 4),
            errors=errors,
            summary=summary,
            confidence_score=confidence_from_issues(errors, [], base=0.85 if status == STATUS_PASS else 0.35),
            command=command,
            linter=selected,
            stdout=stdout[-4000:],
            stderr=stderr[-4000:],
        )

    def _targets(self, project_path: str, file_paths: list[str] | None) -> list[str]:
        if not file_paths:
            return ["."]
        root = Path(project_path)
        targets = []
        for path in sorted(set(file_paths)):
            if (root / path).exists():
                targets.append(path)
        return targets or ["."]

    def _command(self, linter: str, targets: list[str]) -> list[str]:
        if linter == "ruff":
            return ["ruff", "check", *targets]
        if linter == "flake8":
            return ["flake8", *targets]
        if linter == "pylint":
            return ["pylint", *targets]
        return []
