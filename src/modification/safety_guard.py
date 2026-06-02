"""Safety controls for proposed repository modifications."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from src.modification.modification_models import CodePatch, PatchChange, SafetyIssue
from src.utils.helpers import get_logger

log = get_logger(__name__)


DEFAULT_DENY_PATTERNS = (
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*secret*",
    "*secrets*",
    "*credential*",
    "*credentials*",
)

DEFAULT_APPROVAL_PATTERNS = (
    ".gitignore",
    ".github/*",
    "Dockerfile",
    "docker-compose.yml",
    "package.json",
    "package-lock.json",
    "pyproject.toml",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
    "*.lock",
)

DANGEROUS_CONTENT_SIGNALS = (
    "rm -rf",
    "shutil.rmtree",
    "os.remove(",
    "os.unlink(",
    "os.rmdir(",
    "subprocess.run(",
    "subprocess.call(",
    "os.system(",
)


@dataclass(frozen=True)
class SafetyResult:
    """Safety decision for a proposed patch."""

    issues: list[SafetyIssue] = field(default_factory=list)
    approval_required: bool = True

    @property
    def ok(self) -> bool:
        """Return True when there are no blocking safety errors."""
        return not any(issue.severity == "error" for issue in self.issues)


class SafetyGuard:
    """Validate patch paths, protected files, and approval requirements."""

    def __init__(
        self,
        allow_patterns: Iterable[str] | None = None,
        deny_patterns: Iterable[str] | None = None,
        approval_patterns: Iterable[str] | None = None,
        approval_required_by_default: bool = True,
    ) -> None:
        self.allow_patterns = tuple(allow_patterns or ())
        self.deny_patterns = tuple(deny_patterns or DEFAULT_DENY_PATTERNS)
        self.approval_patterns = tuple(approval_patterns or DEFAULT_APPROVAL_PATTERNS)
        self.approval_required_by_default = approval_required_by_default

    def validate_modification(
        self,
        patch: CodePatch,
        project_path: str | None = None,
    ) -> SafetyResult:
        """Return safety findings for a proposed patch."""
        issues: list[SafetyIssue] = []
        for change in patch.changes:
            issues.extend(self._validate_change(change, project_path))

        approval_required = (
            patch.has_changes
            and (
                self.approval_required_by_default
                or patch.approval_required
                or any(issue.requires_approval for issue in issues)
            )
        )
        log.info(
            "Validated modification safety files=%d issues=%d approval_required=%s",
            len(patch.affected_paths),
            len(issues),
            approval_required,
        )
        return SafetyResult(issues=issues, approval_required=approval_required)

    def requires_approval(self, patch: CodePatch, project_path: str | None = None) -> bool:
        """Return True when this patch must wait for approval."""
        return self.validate_modification(patch, project_path=project_path).approval_required

    def is_safe(self, patch: CodePatch, project_path: str | None = None) -> bool:
        """Return True when the patch has no blocking safety issue."""
        return self.validate_modification(patch, project_path=project_path).ok

    def _validate_change(
        self,
        change: PatchChange,
        project_path: str | None,
    ) -> list[SafetyIssue]:
        issues: list[SafetyIssue] = []
        normalized = self._normalize_path(change.file_path)
        if not normalized:
            return [
                SafetyIssue(
                    file_path=change.file_path or "<missing>",
                    reason="Modification path is empty.",
                    severity="error",
                )
            ]

        path_issue = self._path_escape_issue(normalized, project_path)
        if path_issue:
            issues.append(path_issue)

        if self.allow_patterns and not self._matches_any(normalized, self.allow_patterns):
            issues.append(
                SafetyIssue(
                    file_path=normalized,
                    reason="Path is outside the configured modification allow list.",
                    severity="error",
                )
            )

        if self._matches_any(normalized, self.deny_patterns):
            issues.append(
                SafetyIssue(
                    file_path=normalized,
                    reason="Protected secret, credential, or environment file.",
                    severity="error",
                )
            )

        if self._matches_any(normalized, self.approval_patterns):
            issues.append(
                SafetyIssue(
                    file_path=normalized,
                    reason="Repository configuration change requires explicit approval.",
                    severity="warning",
                    requires_approval=True,
                )
            )

        if change.is_deletion:
            issues.append(
                SafetyIssue(
                    file_path=normalized,
                    reason="File deletion requires explicit approval and rollback readiness.",
                    severity="warning",
                    requires_approval=True,
                )
            )

        if change.new_content and "\x00" in change.new_content:
            issues.append(
                SafetyIssue(
                    file_path=normalized,
                    reason="Binary-looking content cannot be safely modified as text.",
                    severity="error",
                )
            )

        dangerous_signal = self._dangerous_content_signal(change.new_content or "")
        if dangerous_signal:
            issues.append(
                SafetyIssue(
                    file_path=normalized,
                    reason=f"Potentially dangerous filesystem operation detected: {dangerous_signal}",
                    severity="warning",
                    requires_approval=True,
                )
            )
        return issues

    def _path_escape_issue(self, path: str, project_path: str | None) -> SafetyIssue | None:
        candidate = Path(path)
        if project_path is None:
            if candidate.is_absolute() or ".." in candidate.parts:
                return SafetyIssue(
                    file_path=path,
                    reason="Modification path must be repository-relative.",
                    severity="error",
                )
            return None

        root = Path(project_path).resolve()
        absolute = candidate if candidate.is_absolute() else root / candidate
        resolved = absolute.resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            return SafetyIssue(
                file_path=path,
                reason="Modification path escapes the repository root.",
                severity="error",
            )
        return None

    def _normalize_path(self, path: str) -> str:
        return str(path).strip().replace("\\", "/")

    def _matches_any(self, path: str, patterns: Iterable[str]) -> bool:
        basename = path.rsplit("/", 1)[-1]
        lowered_path = path.lower()
        lowered_basename = basename.lower()
        for pattern in patterns:
            lowered_pattern = pattern.lower()
            if fnmatch.fnmatch(lowered_path, lowered_pattern):
                return True
            if fnmatch.fnmatch(lowered_basename, lowered_pattern):
                return True
        return False

    def _dangerous_content_signal(self, content: str) -> str:
        lowered = content.lower()
        for signal in DANGEROUS_CONTENT_SIGNALS:
            if signal.lower() in lowered:
                return signal
        return ""


def validate_modification(patch: CodePatch, project_path: str | None = None) -> SafetyResult:
    """Convenience wrapper for safety validation."""
    return SafetyGuard().validate_modification(patch, project_path=project_path)


def requires_approval(patch: CodePatch, project_path: str | None = None) -> bool:
    """Convenience wrapper for approval checks."""
    return SafetyGuard().requires_approval(patch, project_path=project_path)


def is_safe(patch: CodePatch, project_path: str | None = None) -> bool:
    """Convenience wrapper for blocking safety checks."""
    return SafetyGuard().is_safe(patch, project_path=project_path)
